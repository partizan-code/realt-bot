"""Сохранить публичную разметку Kufar без запуска бота и чтения .env."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

# Поддерживаем прямой запуск: python scripts/inspect_kufar.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402


async def collect_report(page):
    """Считать совпадения и признаки карточек, не предполагая новые имена классов."""
    report = await page.evaluate(
        """({limits, selectors}) => ({
            section_count: document.querySelectorAll(selectors.sections).length,
            date_candidates: [...document.querySelectorAll(selectors.dates)]
                .slice(0, limits.dates).map(node => ({
                    tag: node.tagName,
                    class: node.getAttribute('class'),
                    datetime: node.getAttribute('datetime'),
                    text: node.textContent.trim().slice(0, limits.text)
                })),
            section_samples: [...document.querySelectorAll(selectors.sections)].slice(0, limits.sections)
                .map(section => ({
                    class: section.getAttribute('class'),
                    attributes: [...section.attributes].map(a => [a.name, a.value]),
                    link_count: section.querySelectorAll(selectors.links).length,
                    descendants: [...section.querySelectorAll(selectors.descendants)].slice(0, limits.descendants)
                        .map(node => ({
                            tag: node.tagName,
                            class: node.getAttribute('class'),
                            testid: node.getAttribute('data-testid'),
                            itemprop: node.getAttribute('itemprop')
                        }))
                }))
        })""",
        {"limits": config.DIAGNOSTIC_LIMITS, "selectors": config.DIAGNOSTIC_SELECTORS},
    )
    report["old_selector_matches"] = {
        name: await page.locator(selector).count()
        for name, selector in config.LEGACY_AD_SELECTORS.items()
    }
    return report


async def inspect(city, output_dir):
    url = config.LISTING_URL_TEMPLATE.format(city=city)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=config.BROWSER_HEADLESS)
        try:
            context = await browser.new_context(**config.BROWSER_CONTEXT_OPTIONS)
            page = await context.new_page()
            print(f"Открываю страницу города {city}...", flush=True)
            response = await page.goto(
                url,
                wait_until=config.NAVIGATION_WAIT_UNTIL,
                timeout=config.DIAGNOSTIC_NAVIGATION_TIMEOUT_MS,
            )
            try:
                await page.get_by_role(
                    "button", name=config.COOKIE_ACCEPT_BUTTON_TEXT, exact=True
                ).click(timeout=config.COOKIE_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                pass
            # Дожидаемся содержимого карточек, а не только появления контейнеров.
            try:
                await page.locator(config.DIAGNOSTIC_READY_SELECTOR).first.wait_for(
                    timeout=config.DIAGNOSTIC_CARDS_TIMEOUT_MS
                )
            except PlaywrightTimeoutError:
                print("Ссылки внутри section не появились; сохраню страницу для диагностики.")
            report = await collect_report(page)
            report.update(
                city=city, url=page.url, http_status=response.status if response else None
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            report_path = output_dir / f"{city}.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (output_dir / f"{city}.html").write_text(await page.content(), encoding="utf-8")
            await page.screenshot(path=str(output_dir / f"{city}.png"), full_page=True)
            print(f"HTTP: {report['http_status']}; section: {report['section_count']}")
            print("Совпадения старых селекторов:")
            for name, count in report["old_selector_matches"].items():
                print(f"  {name}: {count}")
            print(f"Отчёт: {report_path}")
            print("HTML и снимок сохранены рядом. .env и история ID не использовались.")
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", choices=config.CITIES, default=config.DIAGNOSTIC_DEFAULT_CITY)
    args = parser.parse_args()
    output_dir = config.DIAGNOSTIC_OUTPUT_DIR
    try:
        asyncio.run(inspect(args.city, output_dir))
    except Exception as error:
        print(f"Диагностика не завершена: {type(error).__name__}: {error}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
