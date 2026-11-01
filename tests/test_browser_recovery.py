import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from log_capture import capture_logs
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

import main
from browser_session import BrowserSession, BrowserUnavailable


class BrowserRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        self.session = BrowserSession(self.playwright, {"timezone_id": "Europe/Minsk"})
        self.addAsyncCleanup(self.session.close)
        await self.session.restart()
        self.sender = self.enterContext(patch("main.send_to_telegram", AsyncMock()))

    async def test_closed_page_is_replaced_before_parsing(self):
        previous = self.session.page
        await previous.close()
        with patch("main.parse_new_ads", AsyncMock(return_value=[])) as parse:
            await main.process_city(self.session, "minsk")
        self.assertIsNot(self.session.page, previous)
        parse.assert_awaited_once_with(self.session.page, city="minsk")
        self.assertEqual(
            await self.session.page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone"),
            "Europe/Minsk",
        )
        self.assertFalse(self.session.broken)

    async def test_disconnected_browser_is_relaunched(self):
        previous = self.session.browser
        await previous.close()
        with patch("main.parse_new_ads", AsyncMock(return_value=[])):
            await main.process_city(self.session, "minsk")
        self.assertIsNot(self.session.browser, previous)
        self.assertTrue(self.session.browser.is_connected())

    async def test_crash_event_invalidates_session(self):
        previous = self.session.page
        # Crash only the renderer of this isolated test page through Chromium's protocol.
        protocol = await self.session.context.new_cdp_session(previous)
        crash = asyncio.create_task(protocol.send("Page.crash"))
        try:
            await previous.wait_for_event("crash", timeout=10000)
        finally:
            if not crash.done():
                crash.cancel()
            await asyncio.gather(crash, return_exceptions=True)
        self.assertTrue(self.session.broken)
        with patch("main.parse_new_ads", AsyncMock(return_value=[])):
            await main.process_city(self.session, "minsk")
        self.assertIsNot(self.session.page, previous)
        self.assertFalse(self.session.broken)

    async def test_crash_during_parsing_retries_same_city_with_new_page(self):
        previous = self.session.page
        with (
            patch(
                "main.parse_new_ads", AsyncMock(side_effect=[BrowserUnavailable("crash"), []])
            ) as parse,
            capture_logs(level="WARNING"),
        ):
            await main.process_city(self.session, "minsk")
        self.assertEqual(parse.await_count, 2)
        self.assertIs(parse.await_args_list[0].args[0], previous)
        self.assertIs(parse.await_args_list[1].args[0], self.session.page)
        self.assertIsNot(self.session.page, previous)
        self.sender.assert_awaited_once_with([], city="minsk")

    async def test_repeated_crash_does_not_loop_forever(self):
        with (
            patch(
                "main.parse_new_ads", AsyncMock(side_effect=BrowserUnavailable("crash"))
            ) as parse,
            capture_logs(level="ERROR"),
        ):
            await main.process_city(self.session, "minsk")
        self.assertEqual(parse.await_count, 2)
        self.sender.assert_not_awaited()
        self.assertTrue(self.session.broken)
        with patch("main.parse_new_ads", AsyncMock(return_value=[])):
            await main.process_city(self.session, "gomel")
        self.assertFalse(self.session.broken)
        self.sender.assert_awaited_once_with([], city="gomel")

    async def test_ordinary_load_failure_does_not_restart_healthy_browser(self):
        previous = self.session.page
        with patch("main.parse_new_ads", AsyncMock(return_value=None)) as parse:
            await main.process_city(self.session, "minsk")
        parse.assert_awaited_once()
        self.assertIs(self.session.page, previous)
        self.sender.assert_not_awaited()

    async def test_navigation_crash_is_not_swallowed_as_empty_result(self):
        with patch.object(
            self.session.page,
            "goto",
            AsyncMock(side_effect=PlaywrightError("Page.goto: Page crashed")),
        ):
            with self.assertRaises(BrowserUnavailable):
                await main.parse_new_ads(self.session.page, "minsk")
