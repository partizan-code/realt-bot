import json
import unittest
from contextlib import chdir
from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

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
