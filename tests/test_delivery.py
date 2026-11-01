import json
import unittest
from contextlib import chdir
from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendPhoto

import main


def ad(ad_id, image=None):
    return {
        "id": ad_id,
        "title": ["1-комн. 40 м² 8/9 эт."],
        "price_byn": ["1000 р."],
        "address": ["Тестовая улица"],
        "description": ["Тестовое описание"],
        "link": f"https://example.invalid/{ad_id}",
        "img": image,
        "time": time(14, 30),
    }


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(chdir(directory))
        self.history = Path("ads_ids_minsk.json")
        self.history.write_text('["old"]', encoding="utf-8")
        self.bot = MagicMock()
        self.bot.send_message = AsyncMock()
        self.bot.send_photo = AsyncMock()
        factory = self.enterContext(patch("main.Bot"))
        factory.return_value.__aenter__ = AsyncMock(return_value=self.bot)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        self.enterContext(patch.dict(main.cities_ch_ids, {"minsk": -1000}))
        self.enterContext(patch("main.asyncio.sleep", AsyncMock()))

    def saved(self):
        return json.loads(self.history.read_text(encoding="utf-8"))

    async def test_history_is_written_only_after_success(self):
        async def deliver(**kwargs):
            self.assertEqual(self.saved(), ["old"])

        self.bot.send_message.side_effect = deliver
        await main.send_to_telegram([ad("new")], "minsk")
        self.assertEqual(self.saved(), ["old", "new"])

    async def test_failed_message_remains_eligible_for_retry(self):
        self.bot.send_message.side_effect = RuntimeError("temporary failure")
        with self.assertLogs(main.logger, level="ERROR"):
            await main.send_to_telegram([ad("new")], "minsk")
        self.assertEqual(self.saved(), ["old"])
        self.bot.send_message.side_effect = None
        await main.send_to_telegram([ad("new")], "minsk")
        self.assertEqual(self.saved(), ["old", "new"])

    async def test_partial_success_does_not_confirm_failed_message(self):
        self.bot.send_message.side_effect = [RuntimeError("temporary failure"), None]
        with self.assertLogs(main.logger, level="ERROR"):
            await main.send_to_telegram([ad("failed"), ad("sent")], "minsk")
        self.assertEqual(self.saved(), ["old", "sent"])

    async def test_interrupted_batch_keeps_already_sent_ids(self):
        async def deliver(**kwargs):
            if "/second" in kwargs["text"]:
                raise KeyboardInterrupt("stop batch")

        self.bot.send_message.side_effect = deliver
        with self.assertRaises(KeyboardInterrupt):
            await main.send_to_telegram([ad("first"), ad("second")], "minsk")
        self.assertEqual(self.saved(), ["old", "first"])

    async def test_already_confirmed_id_is_not_sent_again(self):
        await main.send_to_telegram([ad("old"), ad("new"), ad("new")], "minsk")
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual(self.saved(), ["old", "new"])

    async def test_successful_photo_is_confirmed_without_text_fallback(self):
        await main.send_to_telegram([ad("new", "https://example.invalid/image.jpg")], "minsk")
        self.bot.send_photo.assert_awaited_once()
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(self.saved(), ["old", "new"])

    def photo_error(self, error_type):
        return error_type(
            method=SendPhoto(chat_id=-1000, photo="https://example.invalid/image.jpg"),
            message="failed to get HTTP URL content",
        )

    async def test_rejected_photo_falls_back_to_text_before_confirmation(self):
        self.bot.send_photo.side_effect = self.photo_error(TelegramBadRequest)

        async def deliver(**kwargs):
            self.assertEqual(self.saved(), ["old"])
            self.assertIn("https://example.invalid/new", kwargs["text"])
            self.assertEqual(kwargs["text"], self.bot.send_photo.call_args.kwargs["caption"])

        self.bot.send_message.side_effect = deliver
        with self.assertLogs(main.logger, level="WARNING"):
            await main.send_to_telegram([ad("new", "https://example.invalid/image.jpg")], "minsk")
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.saved(), ["old", "new"])

    async def test_failed_text_fallback_does_not_confirm_id(self):
        self.bot.send_photo.side_effect = self.photo_error(TelegramBadRequest)
        self.bot.send_message.side_effect = RuntimeError("temporary failure")
        with self.assertLogs(main.logger, level="WARNING"):
            await main.send_to_telegram([ad("new", "https://example.invalid/image.jpg")], "minsk")
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.saved(), ["old"])

    async def test_ambiguous_network_failure_does_not_send_duplicate_text(self):
        self.bot.send_photo.side_effect = self.photo_error(TelegramNetworkError)
        with self.assertLogs(main.logger, level="ERROR"):
            await main.send_to_telegram([ad("new", "https://example.invalid/image.jpg")], "minsk")
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(self.saved(), ["old"])
