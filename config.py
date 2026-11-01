"""Настройки бота. Секреты загружаются из .env только при вызове configure()."""

from os import getenv
from pathlib import Path

from dotenv import load_dotenv

# Окружение и локальные файлы.
PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
HISTORY_FILE_TEMPLATE = "ads_ids_{city}.json"
COOKIE_STORAGE_FILE = Path("re.kufar.by_cookies.json")
MAX_HISTORY = 50
CITIES = ("minsk", "gomel", "mogilev", "vitebsk", "grodno", "brest")
TOKEN = None
ADMIN_ID = None
CITIES_CH_IDS = {}

# Telegram.
TELEGRAM_PARSE_MODE = "HTML"
TELEGRAM_DISABLE_LINK_PREVIEW = True
TELEGRAM_SEND_DELAY_SECONDS = 3
TELEGRAM_TIME_FORMAT = "%H:%M"
TELEGRAM_MESSAGE_TEMPLATE = (
    "✅ <b>{title}</b>\n"
    "💰 <b>{price}</b>\n\n"
    "Адрес: <b>{address}</b>\n\n"
    "📌 <i>{description}</i>\n\n"
    '🔥 <b><a href="{link}">Ссылка на объявление Kufar</a></b>\n\n'
    "⌛ Размещено: {time}\n\n"
    "{signature}"
)
SIGNATURE = (
    '<b><a href="https://t.me/realt_by_minsk">МИНСК</a> | '
    '<a href="https://t.me/realt_by_gomel">ГОМЕЛЬ</a> | '
    '<a href="https://t.me/realt_by_mogilev">МОГИЛЕВ</a> | '
    '<a href="https://t.me/realt_by_vitebsk">ВИТЕБСК</a> | '
    '<a href="https://t.me/realt_by_grodno">ГРОДНО</a> | '
    '<a href="https://t.me/realt_by_brest">БРЕСТ</a></b>'
)

# Kufar и интервалы обработки.
KUFAR_BASE_URL = "https://re.kufar.by"
LISTING_URL_TEMPLATE = KUFAR_BASE_URL + "/l/{city}/snyat/kvartiru?cur=BYN&size=30&sort=lst.d"
TODAY_LABEL = "Сегодня"
CITY_POLL_DELAY_SECONDS = 15
CITY_ATTEMPTS = 2

# Chromium: таймауты Playwright задаются в миллисекундах, задержки — в секундах.
BROWSER_HEADLESS = True
BROWSER_CONTEXT_OPTIONS = {"timezone_id": "Europe/Minsk"}
BROWSER_RESTART_INTERVAL_CYCLES = 60
BROWSER_RESTART_DELAY_SECONDS = 10
NAVIGATION_WAIT_UNTIL = "domcontentloaded"
NAVIGATION_TIMEOUT_MS = 30000
FIELD_TIMEOUT_MS = 1000
COOKIE_TIMEOUT_MS = 3000
CARDS_TIMEOUT_MS = 10000
COOKIE_ACCEPT_BUTTON_TEXT = "Принять"
BROWSER_FAILURE_MESSAGES = (
    "page crashed",
    "target crashed",
    "target page, context or browser has been closed",
    "browser has been closed",
    "browser has disconnected",
)

# Селекторы: хеши сборки не фиксируются для действующего парсера.
CARD_SELECTOR = 'a[data-testid^="kufar-realty-card-"]'


def field_selector(name):
    return f'[class$="_{name}"], [class*="_{name} "]'


AD_SELECTORS = {
    "date": field_selector("date"),
    "title": field_selector("parameters"),
    "price_byn": '[data-testid="card-price"]',
    "price_usd": '[class*="_price__usd"]',
    "address": '[data-testid="card-adress"]',
    "address_fallback": field_selector("address"),
    "description": f"p:is({field_selector('body')})",
    "images": "img[src]",
}

# Логирование.
LOG_LEVEL = "INFO"
CONSOLE_LOG_LEVEL = "INFO"
FILE_LOG_LEVEL = "ERROR"
LOG_FILE = "error.log"
LOG_FILE_ENCODING = "utf-8"
LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss,SSS} - {name} - {level} - {message}"
LOG_BACKTRACE = False
LOG_DIAGNOSE = False

# Отдельная диагностика сайта: не требует .env и не публикует сообщения.
DIAGNOSTIC_DEFAULT_CITY = CITIES[0]
DIAGNOSTIC_OUTPUT_DIR = PROJECT_ROOT / "tmp" / "kufar-diagnostic"
DIAGNOSTIC_NAVIGATION_TIMEOUT_MS = 45000
DIAGNOSTIC_CARDS_TIMEOUT_MS = 15000
DIAGNOSTIC_READY_SELECTOR = "section a[href]"
DIAGNOSTIC_SELECTORS = {
    "sections": "section",
    "dates": '[class*="date"], time',
    "links": "a",
    "descendants": "*",
}
DIAGNOSTIC_LIMITS = {"dates": 40, "sections": 6, "descendants": 100, "text": 100}
LEGACY_AD_SELECTORS = {
    "date": ".styles-module-scss-module__ykXXHG__date",
    "title": ".styles-module-scss-module__ykXXHG__parameters",
    "address": ".styles-module-scss-module__ykXXHG__address",
    "description": "p.styles-module-scss-module__ykXXHG__body",
    "price_byn": ".styles-module-scss-module__knBPIa__price__byr",
    "price_usd": ".styles_price__usd__HpXMa",
}


def configure():
    """Загрузить и проверить окружение, не раскрывая значения в ошибках."""
    global TOKEN, ADMIN_ID, CITIES_CH_IDS
    load_dotenv(ENV_FILE)
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
    CITIES_CH_IDS = {city: ids[f"{city.upper()}_CH_ID"] for city in CITIES}
