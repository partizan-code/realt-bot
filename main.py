import asyncio
import json
import logging
import os
from collections import deque
from datetime import time
from os import getenv
from urllib.parse import urljoin, urlsplit

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from logger_config import setup_logger

logger = logging.getLogger(__name__)

# Конфигурация
MAX_HISTORY = 50  # Храним только 50 последних ID
TOKEN = None
ADMIN_ID = None
CITIES = ("minsk", "gomel", "mogilev", "vitebsk", "grodno", "brest")
cities_ch_ids = {}

SIGNATURE = (
    '<b><a href="https://t.me/realt_by_minsk">МИНСК</a> | '
    '<a href="https://t.me/realt_by_gomel">ГОМЕЛЬ</a> | '
    '<a href="https://t.me/realt_by_mogilev">МОГИЛЕВ</a> | '
    '<a href="https://t.me/realt_by_vitebsk">ВИТЕБСК</a> | '
    '<a href="https://t.me/realt_by_grodno">ГРОДНО</a> | '
    '<a href="https://t.me/realt_by_brest">БРЕСТ</a></b>'
)


def configure():
    """Проверить окружение перед запуском, не раскрывая его значения в ошибках."""
    global TOKEN, ADMIN_ID, cities_ch_ids
    load_dotenv()
    id_names = ("ADMIN_ID", *(f"{city.upper()}_CH_ID" for city in CITIES))
    missing = [name for name in ("TOKEN", *id_names) if not getenv(name, "").strip()]
    if missing:
        raise ValueError("Заполните переменные в .env: " + ", ".join(missing))
    ids = {}
    for name in id_names:
        try:
            ids[name] = int(getenv(name))
        except ValueError:
            raise ValueError(f"{name} должен быть целым числом") from None
    TOKEN = getenv("TOKEN").strip()
    ADMIN_ID = ids["ADMIN_ID"]
    cities_ch_ids = {city: ids[f"{city.upper()}_CH_ID"] for city in CITIES}


async def load_existing_ids(city):
    file_name = f"ads_ids_{city}.json"

    try:
        with open(file_name, "r") as f:
            data = json.load(f)
            # Создаем deque с ограниченной длиной
            return deque(data, maxlen=MAX_HISTORY)
    except (FileNotFoundError, json.JSONDecodeError):
        return deque(maxlen=MAX_HISTORY)


async def save_ids(ids, city):
    file_name = f"ads_ids_{city}.json"
    with open(file_name, "w") as f:
        # Сохраняем как обычный список
        json.dump(list(ids), f)


CARD_SELECTOR = 'a[data-testid^="kufar-realty-card-"]'


def field_selector(name):
    """Искать смысловой суффикс CSS-класса независимо от хеша сборки."""
    return f'[class$="_{name}"], [class*="_{name} "]'


async def extract_ad(card):
    """Прочитать одну карточку; отсутствие обязательных полей — ошибка разметки."""
    date_text = await card.locator(field_selector("date")).first.inner_text(timeout=1000)
    date_ads, separator, time_ads = date_text.strip().partition(",")
    if not separator:
        raise ValueError("Не распознана дата объявления")
    published_time = time.fromisoformat(time_ads.strip())
    href = await card.get_attribute("href")
    if not href:
        raise ValueError("У карточки отсутствует ссылка")
    ads_link = urljoin("https://re.kufar.by", href).split("?")[0].split("#")[0]
    id_ads = urlsplit(ads_link).path.rstrip("/").split("/")[-1]
    if not id_ads.isdigit():
        raise ValueError("Не распознан ID объявления")

    async def texts(selector):
        values = await card.locator(selector).first.all_inner_texts()
        return [value.strip() for value in values if value.strip()]

    # На сайте параметры разделены пустыми span с CSS-маркерами, а не пробелами.
    title_text = await card.locator(field_selector("parameters")).first.evaluate(
        "node => Array.from(node.childNodes, part => part.textContent.trim()).filter(Boolean).join(' ')",
        timeout=1000,
    )
    title = [" ".join(title_text.split())] if title_text.strip() else []
    price_byn = await texts('[data-testid="card-price"]')
    if not title or not price_byn:
        raise ValueError("Не распознаны параметры или цена объявления")
    address = await texts('[data-testid="card-adress"]')
    if not address:
        address = await texts(field_selector("address"))
    description = await texts(f"p:is({field_selector('body')})")
    price_usd = await texts('[class*="_price__usd"]')
    img_link = None
    for image in await card.locator("img[src]").all():
        source = await image.get_attribute("src")
        if source:
            source = urljoin("https://re.kufar.by", source)
            if urlsplit(source).scheme in ("http", "https") and not urlsplit(source).path.endswith(
                ".svg"
            ):
                img_link = source
                break
    return {
        "id": id_ads,
        "title": title,
        "price_usd": price_usd,
        "price_byn": price_byn,
        "address": address,
        "description": description,
        "img": img_link,
        "link": ads_link,
        "date": date_ads.strip(),
        "time": published_time,
    }


async def parse_new_ads(page, city):
    url = f"https://re.kufar.by/l/{city}/snyat/kvartiru?cur=BYN&size=30&sort=lst.d"
    try:
        response = await page.goto(url, wait_until="domcontentloaded")
        if response and response.status >= 400:
            logger.error("Kufar вернул HTTP %s для %s", response.status, city)
            return None
    except Exception as error:
        logger.error("Не загрузилась страница %s: %s", city, error)
        return None

    try:
        await page.get_by_role("button", name="Принять", exact=True).click(timeout=3000)
        logger.info("Cookies accepted")
    except PlaywrightTimeoutError:
        logger.info("No cookie popup found")

    try:
        await page.locator(CARD_SELECTOR).first.wait_for(timeout=10000)
        ads = await page.locator(CARD_SELECTOR).all()
    except PlaywrightTimeoutError:
        logger.error(
            "Не найдены карточки Kufar для %s: проверьте разметку или доступ к сайту", city
        )
        return None

    new_ads = []
    existing_ids = await load_existing_ids(city)
    seen = set(existing_ids)
    stats = {"dated": 0, "today": 0, "known": 0, "invalid": 0}
    for card in reversed(ads):
        try:
            ad = await extract_ad(card)
            stats["dated"] += 1
            if ad["date"] != "Сегодня":
                continue
            stats["today"] += 1
            if ad["id"] in seen:
                stats["known"] += 1
                continue
            new_ads.append(ad)
            seen.add(ad["id"])
        except Exception as error:
            stats["invalid"] += 1
            logger.warning("Карточка %s пропущена: %s", city, error)

    logger.info(
        "%s: карточек=%s, распознано=%s, сегодня=%s, известных=%s, ошибок=%s, новых=%s",
        city,
        len(ads),
        stats["dated"],
        stats["today"],
        stats["known"],
        stats["invalid"],
        len(new_ads),
    )
    if not stats["dated"]:
        logger.error("Ни одна карточка %s не распознана: проверьте селекторы полей", city)
        return None
    return new_ads


async def send_to_telegram(ads, city):
    channel_id = cities_ch_ids[city]
    existing_ids = await load_existing_ids(city)
    seen = set(existing_ids)

    for ad in ads:
        if ad["id"] in seen:
            continue
        text = (
            f"✅ <b>{ad['title'][0] if ad['title'] else None}</b>\n"
            f"💰 <b>{ad['price_byn'][0] if ad['price_byn'] else None}</b>\n\n"
            f"Адрес: <b>{ad['address'][0] if ad['address'] else None}</b>\n\n"
            f"📌 <i>{ad['description'][0] if ad['description'] else None}</i>\n\n"
            f'🔥 <b><a href="{ad["link"]}">Ссылка на объявление Kufar</a></b>\n\n'
            f"⌛ Размещено: {ad['time'].strftime('%H:%M')}\n\n"
            f"{SIGNATURE}"
        )
        try:
            async with Bot(
                token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
            ) as bot:
                if ad["img"]:
                    try:
                        await bot.send_photo(chat_id=channel_id, photo=ad["img"], caption=text)
                    except TelegramBadRequest as error:
                        # Telegram однозначно отклонил фото — публикуем само объявление.
                        logger.warning(
                            "Фото %s отклонено Telegram, отправляем текст: %s", city, error
                        )
                        await bot.send_message(
                            chat_id=channel_id, text=text, disable_web_page_preview=True
                        )
                else:
                    await bot.send_message(
                        chat_id=channel_id, text=text, disable_web_page_preview=True
                    )
        except Exception as e:
            logger.error(f"Ошибка отправки поста в канал {channel_id}: {e}")
        else:
            # Подтверждаем каждое объявление сразу после успешной доставки.
            existing_ids.append(ad["id"])
            await save_ids(existing_ids, city)
            seen.add(ad["id"])
        await asyncio.sleep(3)


async def main():
    configure()
    RESTART_INTERVAL = 60
    cycle_count = 0
    context_options = {"timezone_id": "Europe/Minsk"}
    if os.path.isfile("re.kufar.by_cookies.json"):
        context_options["storage_state"] = "re.kufar.by_cookies.json"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(**context_options)
        page = await context.new_page()
        try:
            while True:
                cycle_count += 1
                if cycle_count > RESTART_INTERVAL:
                    logger.info("Достигнут интервал перезапуска браузера")
                    await browser.close()
                    await asyncio.sleep(10)
                    browser = await p.chromium.launch(headless=True)
                    context = await browser.new_context(**context_options)
                    page = await context.new_page()
                    cycle_count = 0
                    logger.info("Браузер перезапущен")

                for city in cities_ch_ids:
                    logger.info(f"Проверяем новые объявления {city}...")
                    try:
                        new_ads = await parse_new_ads(page, city=city)
                        if new_ads is not None:  # Проверьте, не вернулся ли None из-за ошибки
                            logger.info(f"Найдено {len(new_ads)} новых объявлений {city}")
                            await send_to_telegram(new_ads, city=city)
                        else:
                            logger.info(f"{city} пропущен из-за ошибки загрузки страницы")
                    except Exception as e:  # Поймайте любые другие ошибки, специфичные для города
                        logger.error(f"Произошла ошибка при обработке города {city}: {e}")
                    # Всегда ждем перед следующим городом/итерацией
                    await asyncio.sleep(15)

        except (
            Exception
        ) as e:  # Этот except теперь поймает ошибки, не связанные с обработкой конкретного города
            logger.error(f"Критическая ошибка в основном цикле, завершение программы: {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    setup_logger()
    try:
        asyncio.run(main())
    except ValueError as error:
        logger.error("Ошибка конфигурации: %s", error)
        raise SystemExit(1) from None
