"""Жизненный цикл Chromium и обнаружение неработоспособной страницы."""

from playwright.async_api import Error as PlaywrightError


class BrowserUnavailable(RuntimeError):
    """Страница или браузер требуют пересоздания."""


def raise_for_browser_error(error):
    """Не скрывать crash/закрытие под обычной ошибкой загрузки или карточки."""
    messages = (
        "page crashed",
        "target crashed",
        "target page, context or browser has been closed",
        "browser has been closed",
        "browser has disconnected",
    )
    if isinstance(error, PlaywrightError) and any(
        message in str(error).lower() for message in messages
    ):
        raise BrowserUnavailable(str(error)) from error


class BrowserSession:
    def __init__(self, playwright, context_options):
        self.playwright = playwright
        self.context_options = dict(context_options)
        self.browser = None
        self.context = None
        self.page = None
        self.crashed = False

    @property
    def broken(self):
        return (
            self.crashed
            or self.browser is None
            or not self.browser.is_connected()
            or self.page is None
            or self.page.is_closed()
        )

    def invalidate(self, *_):
        self.crashed = True

    def ensure_alive(self):
        if self.broken:
            raise BrowserUnavailable("Страница или Chromium недоступны")

    async def restart(self):
        await self.close()
        try:
            self.browser = await self.playwright.chromium.launch(headless=True)
            self.context = await self.browser.new_context(**self.context_options)
            self.page = await self.context.new_page()
            self.page.on("crash", self.invalidate)
            self.crashed = False
        except BaseException:
            await self.close()
            raise

    async def close(self):
        browser = self.browser
        self.browser = self.context = self.page = None
        self.crashed = False
        if browser is not None:
            try:
                await browser.close()
            except PlaywrightError:
                # После аварии соединение уже может быть недоступно.
                pass
