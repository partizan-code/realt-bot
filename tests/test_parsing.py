import unittest
from collections import deque
from datetime import time
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from log_capture import capture_logs
from playwright.async_api import async_playwright

import config
import main


def card(ad_id="1001", date="Сегодня, 14:30", prefix="changedHash", **overrides):
    """Обезличенная структура из сохранённой страницы Kufar."""
    values = {
        "title": "2-комн. 50 м² 3/9 эт.",
        "price": "1 000 р./мес.",
        "address": "Тестовая улица, 1",
        "description": "Тестовое описание",
        **overrides,
    }
    return f"""<section><a data-testid="kufar-realty-card-{ad_id}"
        href="https://re.kufar.by/vi/minsk/snyat/kvartiru/{ad_id}?rank=1#photo">
        <img src="https://example.invalid/photo.jpg?size=large">
        <div class="extra {prefix}_parameters">{values["title"]}</div>
        <span class="unrelated_price" data-testid="card-price">{values["price"]}</span>
        <div class="outer"><span data-testid="card-adress">{values["address"]}</span></div>
        <p class="other_body {prefix}_body">{values["description"]}</p>
        <div class="{prefix}_date extra">{date}</div>
        </a></section>"""


class ParsingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        await self.page.route("**/*", lambda route: route.abort())

    async def test_extracts_fields_without_build_hash_dependency(self):
        for prefix in ("ykXXHG", "differentBuild", "styles-module-scss-module__ykXXHG_"):
            with self.subTest(prefix=prefix):
                await self.page.set_content(card(prefix=prefix))
                ad = await main.extract_ad(self.page.locator(config.CARD_SELECTOR))
                self.assertEqual(ad["id"], "1001")
                self.assertEqual(ad["title"], ["2-комн. 50 м² 3/9 эт."])
                self.assertEqual(ad["price_byn"], ["1 000 р./мес."])
                self.assertEqual(ad["address"], ["Тестовая улица, 1"])
                self.assertEqual(ad["description"], ["Тестовое описание"])
                self.assertEqual(ad["time"], time(14, 30))
                self.assertEqual(ad["link"], "https://re.kufar.by/vi/minsk/snyat/kvartiru/1001")
                self.assertEqual(ad["img"], "https://example.invalid/photo.jpg?size=large")

    async def test_parameters_have_spaces_between_visual_separators(self):
        await self.page.set_content(
            card(
                title='1-комн.<span class="hash_bullet"></span>40 м²'
                '<span class="hash_bullet"></span>8/9 эт.'
            )
        )
        ad = await main.extract_ad(self.page.locator(config.CARD_SELECTOR))
        self.assertEqual(ad["title"], ["1-комн. 40 м² 8/9 эт."])

    async def test_missing_price_does_not_prevent_parsing(self):
        await self.page.set_content(card(price=""))
        ad = await main.extract_ad(self.page.locator(config.CARD_SELECTOR))
        self.assertIsNone(ad["parameters"]["price_byn_month"])
        self.assertEqual(ad["parameters"]["rooms"], 2)

    async def test_structured_values_from_anonymized_html_variants(self):
        for price, title, expected_price, expected_area in (
            ("1\u00a0200,50 BYN/мес.", "2-комн. 50,5 м² 3/9 эт.", "1200.50", "50.5"),
            ("900.25 руб. в месяц", "1-комн. 40.25 м2 1/5 эт.", "900.25", "40.25"),
            ("договорная", "неизвестные параметры", None, None),
        ):
            with self.subTest(price=price):
                await self.page.set_content(card(price=price, title=title))
                ad = await main.extract_ad(self.page.locator(config.CARD_SELECTOR))
                self.assertEqual(
                    ad["parameters"]["price_byn_month"],
                    Decimal(expected_price) if expected_price else None,
                )
                self.assertEqual(
                    ad["parameters"]["area_m2"], Decimal(expected_area) if expected_area else None
                )
                self.assertEqual(ad["price_byn"], [price])

    async def test_explicit_conditions_from_listing_need_no_detail_requests(self):
        result, _ = await self.parse(
            card(description="Район: Советский; Метро: Тестовая; Залог: 500 BYN; Животные: нельзя")
        )
        self.assertEqual(result[0]["parameters"]["district"], "Советский")
        self.assertEqual(result[0]["parameters"]["deposit"], "500 BYN")
        self.assertEqual(result[0]["parameters"]["pets"], "нельзя")

    async def test_absent_parameter_node_does_not_wait_or_drop_card(self):
        await self.page.set_content(card().replace("changedHash_parameters", "unrelated"))
        ad = await main.extract_ad(self.page.locator(config.CARD_SELECTOR))
        self.assertEqual(ad["title"], [])
        self.assertIsNone(ad["parameters"]["rooms"])

    async def parse(self, html, history=()):
        await self.page.set_content(html)
        # Keep all selectors real; replace navigation and cookie UI only.
        with (
            patch.object(self.page, "goto", AsyncMock(return_value=None)),
            patch.object(self.page, "get_by_role") as cookie,
            patch("main.load_existing_ids", AsyncMock(return_value=deque(history, maxlen=50))),
            patch("main.save_ids", AsyncMock()) as save,
        ):
            cookie.return_value.click = AsyncMock()
            result = await main.parse_new_ads(self.page, "minsk")
        return result, save

    async def test_filters_old_known_and_duplicate_ads(self):
        result, save = await self.parse(
            "<section>Unrelated section</section>"
            + card("1002")
            + card("1002")
            + card("1001")
            + card("1003", "Вчера, 12:00"),
            history=("1001",),
        )
        self.assertEqual([ad["id"] for ad in result], ["1002"])
        save.assert_not_awaited()

    async def test_known_ads_are_logged_as_known(self):
        with capture_logs(level="INFO") as logs:
            result, save = await self.parse(card(), history=("1001",))
        self.assertEqual(result, [])
        save.assert_not_awaited()
        self.assertTrue(any("известных=1" in line for line in logs))

    async def test_broken_fields_are_reported_as_failure(self):
        with capture_logs(level="ERROR") as logs:
            result, save = await self.parse(card(date="неизвестная дата"))
        self.assertIsNone(result)
        save.assert_not_awaited()
        self.assertTrue(any("Ни одна карточка" in line for line in logs))
