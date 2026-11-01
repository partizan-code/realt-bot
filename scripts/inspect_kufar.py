"""Сохранить публичную разметку Kufar без запуска бота и чтения .env."""

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

CITIES = ("minsk", "gomel", "mogilev", "vitebsk", "grodno", "brest")
OLD_SELECTORS = {
    "date": ".styles-module-scss-module__ykXXHG__date",
    "title": ".styles-module-scss-module__ykXXHG__parameters",
    "address": ".styles-module-scss-module__ykXXHG__address",
    "description": "p.styles-module-scss-module__ykXXHG__body",
    "price_byn": ".styles-module-scss-module__knBPIa__price__byr",
    "price_usd": ".styles_price__usd__HpXMa",
}


async def collect_report(page):
    """Считать совпадения и признаки карточек, не предполагая новые имена классов."""
    report = await page.evaluate(
        """() => ({
            section_count: document.querySelectorAll('section').length,
            date_candidates: [...document.querySelectorAll('[class*="date"], time')]
                .slice(0, 40).map(node => ({
                    tag: node.tagName,
                    class: node.getAttribute('class'),
                    datetime: node.getAttribute('datetime'),
                    text: node.textContent.trim().slice(0, 100)
                })),
            section_samples: [...document.querySelectorAll('section')].slice(0, 6)
                .map(section => ({
                    class: section.getAttribute('class'),
                    attributes: [...section.attributes].map(a => [a.name, a.value]),
                    link_count: section.querySelectorAll('a').length,
                    descendants: [...section.querySelectorAll('*')].slice(0, 100)
                        .map(node => ({
                            tag: node.tagName,
                            class: node.getAttribute('class'),
                            testid: node.getAttribute('data-testid'),
                            itemprop: node.getAttribute('itemprop')
                        }))
                }))
        })"""
    )
    report["old_selector_matches"] = {
        name: await page.locator(selector).count() for name, selector in OLD_SELECTORS.items()
    }
    return report


async def inspect(city, output_dir):
    url = f"https://re.kufar.by/l/{city}/snyat/kvartiru?cur=BYN&size=30&sort=lst.d"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(timezone_id="Europe/Minsk")
            page = await context.new_page()
            print(f"Открываю страницу города {city}...", flush=True)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.get_by_role("button", name="Принять", exact=True).click(timeout=3000)
            except PlaywrightTimeoutError:
                pass
            # Дожидаемся содержимого карточек, а не только появления контейнеров.
            try:
                await page.locator("section a[href]").first.wait_for(timeout=15000)
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
    parser.add_argument("--city", choices=CITIES, default="minsk")
    args = parser.parse_args()
    output_dir = Path(__file__).resolve().parents[1] / "tmp" / "kufar-diagnostic"
    try:
        asyncio.run(inspect(args.city, output_dir))
    except Exception as error:
        print(f"Диагностика не завершена: {type(error).__name__}: {error}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
