import asyncio
import json
import logging
import os
from collections import deque
from datetime import datetime, time
from os import getenv

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
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


async def parse_new_ads(page, city):
    url = f"https://re.kufar.by/l/{city}/snyat/kvartiru?cur=BYN&size=30&sort=lst.d"
    ads = []
    try:
        await page.goto(url)
    except Exception as e:
        logger.error(f"Не загрузилась страница {e}")
        return None

    # Обработка всплывающего окна cookies
    try:
        await page.locator("button:has-text('Принять')").click(timeout=3000)
        logger.info("Cookies accepted")
    except PlaywrightTimeoutError:
        logger.info("No cookie popup found")

    try:
        # Сначала проверяем, есть ли вообще section на странице
        section_count = await page.locator("section").count()
        logger.info(f"Найдено section элементов: {section_count}")

        if section_count == 0:
            # Если section нет, ждем появления хотя бы одного
            logger.info("Ожидаем появления section элементов...")
            await page.wait_for_selector("section", timeout=10000)

        # Получаем все элементы section
        ads = await page.locator("section").all()
        logger.info(f"Получено {len(ads)} элементов section")

    except Exception as e:
        logger.error(f"Ошибка locator(section) {e}")
        return None

    if not ads:
        # Создаем директорию для скриншотов, если её нет
        screenshots_dir = "img"
        os.makedirs(screenshots_dir, exist_ok=True)

        # Генерируем уникальное имя файла
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = os.path.join(screenshots_dir, f"{city}_{timestamp}.png")

        # Делаем скриншот
        await page.screenshot(path=screenshot_path, full_page=True)
        logger.info(f"Скриншот сохранен: {screenshot_path}")

        async with Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) as bot:
            await bot.send_photo(chat_id=ADMIN_ID, photo=FSInputFile(screenshot_path))
        return None

    new_ads = []
    existing_ids = await load_existing_ids(city)

    for i in reversed(ads):
        try:
            date_time = await i.locator(
                ".styles-module-scss-module__ykXXHG__date"
            ).all_inner_texts()
            if date_time:
                date_ads = date_time[0].split(",")[0]
                time_ads = date_time[0].split(",")[1].strip()
                time_ads = time.fromisoformat(time_ads)
                if date_ads == "Сегодня":
                    ads_link = await i.locator("a").first.get_attribute("href")
                    ads_link = ads_link.split("?")[0]
                    id_ads = ads_link.split("/")[-1]
                    if id_ads not in existing_ids:
                        title = await i.locator(
                            ".styles-module-scss-module__ykXXHG__parameters"
                        ).all_inner_texts()
                        address = await i.locator(
                            ".styles-module-scss-module__ykXXHG__address"
                        ).all_inner_texts()
                        description = await i.locator(
                            "p.styles-module-scss-module__ykXXHG__body"
                        ).all_inner_texts()
                        price_byn = await i.locator(
                            ".styles-module-scss-module__knBPIa__price__byr"
                        ).all_inner_texts()
                        price_usd = await i.locator(".styles_price__usd__HpXMa").all_inner_texts()

                        images = await i.locator("img").all()
                        img_link = None
                        if images:
                            for img in images:
                                img_link = await img.get_attribute("src")
                                if img_link[-4:] == ".jpg":
                                    break

                        new_ads.append(
                            {
                                "id": id_ads,
                                "title": title,
                                "price_usd": price_usd,
                                "price_byn": price_byn,
                                "address": address,
                                "description": description,
                                "img": img_link,
                                "link": ads_link,
                                "date": date_ads,
                                "time": time_ads,
                            }
                        )
                        # Добавляем новый ID в историю
                        existing_ids.append(id_ads)
        except Exception as e:
            logger.error(f"Ошибка при парсинге объявления: {e}")

    if new_ads:
        await save_ids(existing_ids, city)

    return new_ads


async def send_to_telegram(ads, city):
    channel_id = cities_ch_ids[city]

    for ad in ads:
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
                    await bot.send_photo(chat_id=channel_id, photo=ad["img"], caption=text)
                else:
                    await bot.send_message(
                        chat_id=channel_id, text=text, disable_web_page_preview=True
                    )
        except Exception as e:
            logger.error(f"Ошибка отправки поста в канал {channel_id}: {e}")
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
