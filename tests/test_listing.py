import unittest
from datetime import time
from decimal import Decimal
from html.parser import HTMLParser
from unittest.mock import patch

import config
from listing import hashtags, parameters, render_post, telegram_length


def example(**overrides):
    return {
        "title": ["2-комн. 50,5 м² 3/9 эт."],
        "price_byn": ["1 200 р./мес."],
        "address": ["Тестовая, 1; Советский район"],
        "description": [
            "Метро: Тестовая; Залог: 500,50 BYN; Комиссия: нет; "
            "Коммунальные: отдельно; Срок аренды: 12 месяцев; Животные: нельзя"
        ],
        "link": 'https://example.invalid/1001?a=1&b="test"',
        "time": time(14, 30),
        **overrides,
    }


class ListingTests(unittest.TestCase):
    def test_price_formats_and_explicit_month(self):
        for source in (
            "1 200,50 р./мес.",
            "1\u00a0200.50 BYN в месяц",
            "1\u202f200,50 руб. за месяц",
        ):
            with self.subTest(source=source):
                ad = example(price_byn=[source])
                self.assertEqual(parameters(ad, "minsk")["price_byn_month"], Decimal("1200.50"))
                self.assertEqual(ad["price_byn"], [source])

    def test_ambiguous_prices_are_unknown(self):
        for source in (
            "1000 р.",
            "от 800 р./мес.",
            "800–1000 BYN/мес.",
            "300 USD/мес.",
            "100 р./сутки",
            "0 BYN/мес.",
            "1.200 BYN/мес.",
            "1 00 BYN/мес.",
            "1000 BYN/мес. или 300 USD",
            "",
            "договорная",
        ):
            with self.subTest(source=source):
                self.assertIsNone(
                    parameters(example(price_byn=[source]), "minsk")["price_byn_month"]
                )

    def test_parameters_and_evidence(self):
        data = parameters(example(), "minsk")
        self.assertEqual(data["rooms"], 2)
        self.assertEqual(data["area_m2"], Decimal("50.5"))
        self.assertEqual(data["floor"], (3, 9))
        self.assertEqual(data["district"], "Советский")
        self.assertEqual(data["metro"], "Тестовая")
        self.assertEqual(data["deposit"], "500,50 BYN")
        self.assertEqual(data["commission"], "нет")
        self.assertEqual(data["utilities"], "отдельно")
        self.assertEqual(data["rental_term"], "12 месяцев")
        self.assertEqual(data["pets"], "нельзя")

    def test_ambiguous_and_invalid_parameters(self):
        for title in ("1–2-комн. 40–50 м² 10/9 эт.", "1-комн. 2-комн. 40 м² 50 м²", "студия", ""):
            with self.subTest(title=title):
                data = parameters(example(title=[title]), "minsk")
                self.assertIsNone(data["rooms"])
                self.assertIsNone(data["area_m2"])
                self.assertIsNone(data["floor"])

    def test_absence_is_not_permission_or_zero_payment(self):
        data = parameters(
            example(description=["Рядом метро. Обсудим животных и комиссию."]), "minsk"
        )
        for name in (*config.POST_CONDITION_NAMES, "metro"):
            self.assertIsNone(data[name])
        text = render_post(example(description=[]), "minsk")
        self.assertEqual(text.count("не указано"), 1)

    def test_explicit_phrases_and_conflicts(self):
        data = parameters(
            example(description=["Без комиссии. Без залога. С животными нельзя."]), "minsk"
        )
        self.assertEqual(data["commission"].lower(), "без комиссии")
        self.assertEqual(data["deposit"].lower(), "без залога")
        self.assertEqual(data["pets"].lower(), "с животными нельзя")
        data = parameters(example(description=["Животные: можно; Животные: нельзя"]), "minsk")
        self.assertIsNone(data["pets"])
        data = parameters(
            example(description=["Комиссия: 100 BYN; Комиссия: 200 BYN; Без комиссии"]), "minsk"
        )
        self.assertIsNone(data["commission"])

    def test_pets_prohibition_in_rental_sentence(self):
        ad = example(
            description=[
                "Отвечаю только на звонки. Однокомнатная квартира с хорошим расположением, "
                "вокруг развитая инфраструктура. Сдам одному человеку без детей и животных."
            ]
        )
        self.assertEqual(parameters(ad, "gomel")["pets"], "без детей и животных")
        self.assertIn("🐶 Животные: <b>без детей и животных</b>", render_post(ad, "gomel"))
        for description in (
            "Без детей и животных.",
            "Сдам без животных.",
            "Сдам паре без детей и животных.",
            "СДАМ ОДНОМУ ЧЕЛОВЕКУ БЕЗ ДЕТЕЙ И ЖИВОТНЫХ!",
        ):
            with self.subTest(description=description):
                self.assertIsNotNone(
                    parameters(example(description=[description]), "gomel")["pets"]
                )

    def test_uncertain_and_conflicting_pets_conditions_remain_unknown(self):
        for description in (
            "Предпочтительно без детей и животных.",
            "Не обязательно без животных.",
            "Сдам одному человеку без детей.",
            "Обсудим возможность проживания без детей и животных.",
            "Животные: можно; Сдам одному человеку без детей и животных.",
        ):
            with self.subTest(description=description):
                self.assertIsNone(parameters(example(description=[description]), "gomel")["pets"])

    def test_district_requires_label_and_city_dictionary(self):
        self.assertIsNone(parameters(example(address=["Советская улица"]), "minsk")["district"])
        self.assertIsNone(parameters(example(), "brest")["district"])
        self.assertEqual(
            parameters(example(address=["Район: советский"]), "minsk")["district"], "Советский"
        )

    def test_tag_boundaries(self):
        for price, tag in (
            ("799.99", "до800BYN"),
            ("800", "800_1200BYN"),
            ("1199.99", "800_1200BYN"),
            ("1200", "1200_1800BYN"),
            ("1800", "от1800BYN"),
        ):
            with self.subTest(price=price):
                data = parameters(example(price_byn=[f"{price} BYN/мес."]), "minsk")
                self.assertEqual(hashtags(data), f"#комнат_2 #{tag} #район_Советский")
        self.assertEqual(
            hashtags(parameters(example(title=[], price_byn=[], address=[]), "minsk")), ""
        )

    def test_safe_html_and_complete_post(self):
        text = render_post(
            example(
                address=['<script> & "улица"'], description=["<b>Описание</b>; Животные: нельзя"]
            ),
            "minsk",
        )
        self.assertTrue(text.startswith("✅ <b>2-комн. · 50.5 м²</b>\n💰 <b>1200 BYN/мес.</b>\n\n"))
        self.assertIn("&lt;script&gt; &amp;", text)
        self.assertIn("📌 <i>&lt;b&gt;Описание&lt;/b&gt;</i>", text)
        self.assertIn("&amp;b=&quot;test&quot;", text)
        self.assertNotIn("<script>", text)
        self.assertIn("14:30", text)
        HTMLParser().feed(text)

    def test_long_unicode_content_fits_limits_and_retains_conditions(self):
        ad = example(description=["Залог: 500 BYN; Животные: нельзя; " + "😀 & <текст>. " * 1000])
        for limit in (config.TELEGRAM_CAPTION_LIMIT, config.TELEGRAM_TEXT_LIMIT):
            text = render_post(ad, "minsk", limit)
            self.assertLessEqual(telegram_length(text), limit)
            self.assertNotIn("Залог:", text)
            self.assertIn("🐶 Животные: <b>нельзя</b>", text)
            self.assertIn("Объявление на Kufar", text)
            self.assertNotIn("&lt;тек…", text)

    def test_description_is_deduplicated(self):
        text = render_post(
            example(description=["Свежий ремонт. Свежий ремонт. Залог: 500 BYN"]), "minsk"
        )
        self.assertEqual(text.count("Свежий ремонт."), 1)
        self.assertNotIn("Залог:", text)

    def test_readable_blocks_and_tags_below_channels(self):
        text = render_post(example(description=["Свежий ремонт."]), "minsk")
        self.assertIn("\n\n<b>Адрес: Тестовая, 1; Советский район</b>\n", text)
        self.assertIn("\n<b>Этаж: 3/9</b>\n\n", text)
        self.assertIn("\n\n📌 <i>Свежий ремонт.</i>\n\n🔥 <b>", text)
        self.assertIn("\n\n⌛ Размещено: 14:30\n\n" + config.SIGNATURE, text)
        self.assertTrue(
            text.endswith(config.SIGNATURE + "\n\n#комнат_2 #1200_1800BYN #район_Советский")
        )
        without_tags = render_post(example(title=[], price_byn=[], address=[]), "minsk")
        self.assertTrue(without_tags.endswith(config.SIGNATURE))

    def test_unwanted_conditions_are_not_printed_even_in_description(self):
        text = render_post(
            example(
                description=[
                    "Залог: 500 BYN; Комиссия: 50%; Коммунальные: отдельно; "
                    "Срок аренды: год; Без комиссии. Без залога. Свежий ремонт."
                ]
            ),
            "minsk",
        )
        for word in ("залог", "комисси", "коммуналь", "срок аренды"):
            self.assertNotIn(word, text.casefold())
        self.assertIn("📌 <i>Свежий ремонт.</i>", text)

    def test_insufficient_caption_budget_and_invalid_link(self):
        self.assertIsNone(render_post(example(), "minsk", 30))
        with self.assertRaises(ValueError):
            render_post(example(link="javascript:alert(1)"), "minsk")

    def test_configuration_is_used_at_call_time(self):
        with patch.object(config, "PRICE_TAG_BANDS", ((2000, "бюджет"),)):
            self.assertIn("#бюджет", hashtags(parameters(example(), "minsk")))
