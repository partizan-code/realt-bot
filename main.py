import asyncio
import json
from collections import deque
from datetime import time
from urllib.parse import urljoin, urlsplit

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from loguru import logger
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

import config
from browser_session import BrowserSession, BrowserUnavailable, raise_for_browser_error
from listing import parameters, render_post
from logger_config import setup_logger


async def load_existing_ids(city):
    file_name = config.HISTORY_FILE_TEMPLATE.format(city=city)

    try:
        with open(file_name, "r") as f:
            data = json.load(f)
            # Создаем deque с ограниченной длиной
            return deque(data, maxlen=config.MAX_HISTORY)
    except (FileNotFoundError, json.JSONDecodeError):
        return deque(maxlen=config.MAX_HISTORY)


async def save_ids(ids, city):
    file_name = config.HISTORY_FILE_TEMPLATE.format(city=city)
    with open(file_name, "w") as f:
        # Сохраняем как обычный список
        json.dump(list(ids), f)


async def extract_ad(card):
    """Прочитать одну карточку; отсутствие обязательных полей — ошибка разметки."""
    date_text = await card.locator(config.AD_SELECTORS["date"]).first.inner_text(
        timeout=config.FIELD_TIMEOUT_MS
    )
    date_ads, separator, time_ads = date_text.strip().partition(",")
    if not separator:
        raise ValueError("Не распознана дата объявления")
    published_time = time.fromisoformat(time_ads.strip())
    href = await card.get_attribute("href")
    if not href:
        raise ValueError("У карточки отсутствует ссылка")
    ads_link = urljoin(config.KUFAR_BASE_URL, href).split("?")[0].split("#")[0]
    id_ads = urlsplit(ads_link).path.rstrip("/").split("/")[-1]
    if not id_ads.isdigit():
        raise ValueError("Не распознан ID объявления")

    async def texts(selector):
        values = await card.locator(selector).first.all_inner_texts()
        return [value.strip() for value in values if value.strip()]

    # На сайте параметры разделены пустыми span с CSS-маркерами, а не пробелами.
    title_node = card.locator(config.AD_SELECTORS["title"]).first
    title_text = (
        await title_node.evaluate(
            "node => Array.from(node.childNodes, part => part.textContent.trim()).filter(Boolean).join(' ')",
            timeout=config.FIELD_TIMEOUT_MS,
        )
        if await title_node.count()
        else ""
    )
    title = [" ".join(title_text.split())] if title_text.strip() else []
    price_byn = await texts(config.AD_SELECTORS["price_byn"])
    address = await texts(config.AD_SELECTORS["address"])
    if not address:
        address = await texts(config.AD_SELECTORS["address_fallback"])
    description = await texts(config.AD_SELECTORS["description"])
    price_usd = await texts(config.AD_SELECTORS["price_usd"])
    img_link = None
    for image in await card.locator(config.AD_SELECTORS["images"]).all():
        source = await image.get_attribute("src")
        if source:
            source = urljoin(config.KUFAR_BASE_URL, source)
            if urlsplit(source).scheme in ("http", "https") and not urlsplit(source).path.endswith(
                ".svg"
            ):
                img_link = source
                break
    ad = {
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
    ad["parameters"] = parameters(ad, "")
    return ad


async def parse_new_ads(page, city):
    url = config.LISTING_URL_TEMPLATE.format(city=city)
    try:
        response = await page.goto(
            url, wait_until=config.NAVIGATION_WAIT_UNTIL, timeout=config.NAVIGATION_TIMEOUT_MS
        )
        if response and response.status >= 400:
            logger.error("Kufar вернул HTTP {} для {}", response.status, city)
            return None
    except Exception as error:
        raise_for_browser_error(error)
        logger.error("Не загрузилась страница {}: {}", city, error)
        return None

    try:
        await page.get_by_role("button", name=config.COOKIE_ACCEPT_BUTTON_TEXT, exact=True).click(
            timeout=config.COOKIE_TIMEOUT_MS
        )
        logger.info("Cookies accepted")
    except PlaywrightTimeoutError:
        logger.info("No cookie popup found")

    try:
        await page.locator(config.CARD_SELECTOR).first.wait_for(timeout=config.CARDS_TIMEOUT_MS)
        ads = await page.locator(config.CARD_SELECTOR).all()
    except PlaywrightTimeoutError:
        logger.error(
            "Не найдены карточки Kufar для {}: проверьте разметку или доступ к сайту", city
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
            if ad["date"] != config.TODAY_LABEL:
                continue
            stats["today"] += 1
            if ad["id"] in seen:
                stats["known"] += 1
                continue
            new_ads.append(ad)
            ad["parameters"] = parameters(ad, city)
            seen.add(ad["id"])
        except Exception as error:
            raise_for_browser_error(error)
            stats["invalid"] += 1
            logger.warning("Карточка {} пропущена: {}", city, error)

    logger.info(
        "{}: карточек={}, распознано={}, сегодня={}, известных={}, ошибок={}, новых={}",
        city,
        len(ads),
        stats["dated"],
        stats["today"],
        stats["known"],
        stats["invalid"],
        len(new_ads),
    )
    if not stats["dated"]:
        logger.error("Ни одна карточка {} не распознана: проверьте селекторы полей", city)
        return None
    return new_ads


async def send_to_telegram(ads, city):
    channel_id = config.CITIES_CH_IDS[city]
    existing_ids = await load_existing_ids(city)
    seen = set(existing_ids)

    for ad in ads:
        if ad["id"] in seen:
            continue
        try:
            text = render_post(ad, city)
            caption = render_post(ad, city, config.TELEGRAM_CAPTION_LIMIT)
            if text is None:
                raise ValueError("Обязательные поля поста превышают лимит Telegram")
            async with Bot(
                token=config.TOKEN,
                default=DefaultBotProperties(parse_mode=config.TELEGRAM_PARSE_MODE),
            ) as bot:
                if ad["img"] and caption is not None:
                    try:
                        await bot.send_photo(chat_id=channel_id, photo=ad["img"], caption=caption)
                    except TelegramBadRequest as error:
                        # Telegram однозначно отклонил фото — публикуем само объявление.
                        logger.warning(
                            "Фото {} отклонено Telegram, отправляем текст: {}", city, error
                        )
                        await bot.send_message(
                            chat_id=channel_id,
                            text=text,
                            disable_web_page_preview=config.TELEGRAM_DISABLE_LINK_PREVIEW,
                        )
                else:
                    await bot.send_message(
                        chat_id=channel_id,
                        text=text,
                        disable_web_page_preview=config.TELEGRAM_DISABLE_LINK_PREVIEW,
                    )
        except Exception as e:
            logger.error(f"Ошибка отправки поста в канал {channel_id}: {e}")
        else:
            # Подтверждаем каждое объявление сразу после успешной доставки.
            existing_ids.append(ad["id"])
            await save_ids(existing_ids, city)
            seen.add(ad["id"])
        await asyncio.sleep(config.TELEGRAM_SEND_DELAY_SECONDS)


async def process_city(session, city):
    """При аварии восстановить Chromium и один раз повторить текущий город."""
    for attempt in range(config.CITY_ATTEMPTS):
        try:
            if session.broken:
                await session.restart()
            session.ensure_alive()
            new_ads = await parse_new_ads(session.page, city=city)
            session.ensure_alive()
        except BrowserUnavailable as error:
            session.invalidate()
            logger.warning(
                "Сбой Chromium для {}, попытка {}/{}: {}",
                city,
                attempt + 1,
                config.CITY_ATTEMPTS,
                error,
            )
            continue
        except Exception as error:
            if session.broken:
                session.invalidate()
                logger.warning("Не удалось восстановить Chromium для {}: {}", city, error)
                continue
            logger.error("Ошибка обработки {}: {}", city, error)
            return
        if new_ads is None:
            logger.info("{} пропущен из-за ошибки загрузки или разметки", city)
        else:
            logger.info("Найдено {} новых объявлений {}", len(new_ads), city)
            # Ошибка доставки или записи истории не должна повторять парсинг как crash.
            await send_to_telegram(new_ads, city=city)
        return
    logger.error(
        "Город {} пропущен: неудачных попыток восстановления Chromium — {}",
        city,
        config.CITY_ATTEMPTS,
    )


async def main():
    config.configure()
    cycle_count = 0
    context_options = dict(config.BROWSER_CONTEXT_OPTIONS)
    if config.COOKIE_STORAGE_FILE.is_file():
        context_options["storage_state"] = str(config.COOKIE_STORAGE_FILE)

    async with async_playwright() as p:
        session = BrowserSession(p, context_options)
        try:
            while True:
                cycle_count += 1
                if cycle_count > config.BROWSER_RESTART_INTERVAL_CYCLES:
                    logger.info("Достигнут интервал перезапуска браузера")
                    await session.close()
                    await asyncio.sleep(config.BROWSER_RESTART_DELAY_SECONDS)
                    cycle_count = 0

                for city in config.CITIES_CH_IDS:
                    logger.info(f"Проверяем новые объявления {city}...")
                    try:
                        await process_city(session, city)
                    except Exception as e:  # Поймайте любые другие ошибки, специфичные для города
                        logger.error(f"Произошла ошибка при обработке города {city}: {e}")
                    # Всегда ждем перед следующим городом/итерацией
                    await asyncio.sleep(config.CITY_POLL_DELAY_SECONDS)

        except (
            Exception
        ) as e:  # Этот except теперь поймает ошибки, не связанные с обработкой конкретного города
            logger.error(f"Критическая ошибка в основном цикле, завершение программы: {e}")
        finally:
            await session.close()


if __name__ == "__main__":
    setup_logger()
    try:
        asyncio.run(main())
    except ValueError as error:
        logger.error("Ошибка конфигурации: {}", error)
        raise SystemExit(1) from None
