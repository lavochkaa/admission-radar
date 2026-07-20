#!/usr/bin/env python3
"""
Разведка: сохраняет HTML страницы и лог XHR-запросов.
Запуск: python priem_debug.py <URL>
"""

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main(url: str) -> None:
    xhr_log = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()

        def on_response(resp):
            ct = resp.headers.get("content-type", "")
            if "json" in ct or "html" in ct or "xml" in ct:
                xhr_log.append({
                    "url": resp.url,
                    "status": resp.status,
                    "type": resp.request.resource_type,
                    "content_type": ct,
                })

        page.on("response", on_response)

        print(f"Открываю {url} ...", flush=True)
        await page.goto(url, wait_until="networkidle", timeout=90_000)
        await page.wait_for_timeout(5000)  # добить возможные догрузки

        html = await page.content()
        Path("page.html").write_text(html, encoding="utf-8")
        await page.screenshot(path="page.png", full_page=True)

        # Ищем табличные структуры
        counts = {
            "table": len(await page.query_selector_all("table")),
            "tr": len(await page.query_selector_all("tr")),
            "div.row": len(await page.query_selector_all("div.row")),
            "[role=row]": len(await page.query_selector_all("[role=row]")),
            "li": len(await page.query_selector_all("li")),
        }

        await browser.close()

    Path("network.json").write_text(
        json.dumps(xhr_log, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n=== Найдено элементов ===")
    for k, v in counts.items():
        print(f"  {k}: {v}")

    print("\n=== Интересные XHR (json/html, не картинки/шрифты) ===")
    for r in xhr_log:
        if any(s in r["url"] for s in ("api", "data", "list", "json", "1581")):
            print(f"  [{r['status']}] {r['url']}")

    print("\nСохранено: page.html, page.png, network.json")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python priem_debug.py <URL>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))