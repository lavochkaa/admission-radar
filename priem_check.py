#!/usr/bin/env python3
"""
Проверка позиции в конкурсном списке по баллам.
Работает с priem.sutd.ru и подобными (таблица внутри iframe FastReport).

Установка:
    pip install playwright
    playwright install chromium

Запуск:
    python priem_check.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

from playwright.async_api import async_playwright, Browser, BrowserContext, Frame, Page

KNOWN_UNIVERSITIES = {
    "priem.sutd.ru": "СПбГУПТД",
    "abit.etu.ru": "СПбГЭТУ «ЛЭТИ»",
    "apply.tpu.ru": "ТПУ",
    "spbti.ru": "СПбГТИ(ТУ)",
}

# заголовки вкладки, общие для ЛЮБОГО списка на сайте (не содержат название
# направления) — если title совпал с одним из них, для специальности нужен
# другой источник (см. detect_specialty)
GENERIC_TITLE_PATTERNS = [
    re.compile(r"^приемная комиссия", re.I),
    re.compile(r"^личный кабинет", re.I),
    re.compile(r"^списки (поступающих|подавших)", re.I),
]

# код направления по ФГОС в начале строки, напр. "09.03.02 Информационные..."
SPECIALTY_CODE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{2}\b")


def university_name(url: str) -> str:
    host = urlparse(url).netloc
    for domain, name in KNOWN_UNIVERSITIES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host


def is_generic_title(title: str) -> bool:
    return any(p.search(title) for p in GENERIC_TITLE_PATTERNS)


async def detect_specialty(page: Page, context: BrowserContext) -> str:
    url = page.url

    # ТПУ: название направления не попадает ни в title, ни в заголовки
    # (общие для всех списков сайта) — зато отдаётся публичным JSON без авторизации
    m = re.search(r"apply\.tpu\.ru/competition-lists/(\d+)", url)
    if m:
        try:
            resp = await context.request.get(
                f"https://apply.tpu.ru/api/competition/header?competition_id={m.group(1)}"
            )
            data = await resp.json()
            title = data.get("body", {}).get("data", {}).get("title")
            if title:
                return title
        except Exception:
            pass

    title = (await page.title()).strip()
    if title and not is_generic_title(title):
        return title.replace("_", " ")

    # FastReport-подобные страницы: направление печатается прямо над таблицей
    # как код ФГОС (напр. sutd: "09.03.02 Информационные системы и технологии")
    try:
        texts = await page.evaluate(
            "() => Array.from(document.querySelectorAll('div'))"
            ".map(e => e.textContent.trim()).filter(Boolean)"
        )
    except Exception:
        texts = []
    for t in texts:
        if SPECIALTY_CODE_RE.match(t):
            return t

    return "—"


VUZ_LINK_RE = re.compile(r'href="(/vuz/\d+)"[^>]*aria-label="Перейти к вузу')
NAPR_LINK_RE = re.compile(r'href="(/vuz/\d+/napr/\d+)">([^<]*)</a>')
BUDGET_TOTAL_RE = re.compile(
    r"<strong>\s*(\d+)\s*</strong>\s*<span>\s*бюджетных мест всего\s*</span>", re.S
)


# несколько специальностей одного вуза в одном батче не должны каждая заново
# скачивать одну и ту же страницу вуза (список направлений) на vuzopedia.ru
_vuz_page_cache: dict[str, Optional[str]] = {}
_vuz_page_locks: dict[str, asyncio.Lock] = {}


async def _get_vuz_page(university: str, context: BrowserContext) -> Optional[str]:
    if university in _vuz_page_cache:
        return _vuz_page_cache[university]

    lock = _vuz_page_locks.setdefault(university, asyncio.Lock())
    async with lock:
        if university in _vuz_page_cache:  # кто-то уже сходил, пока ждали lock
            return _vuz_page_cache[university]

        vuz_html = None
        try:
            resp = await context.request.get(f"https://vuzopedia.ru/vuzfilter?vuz={quote(university)}")
            html = await resp.text()
            m = VUZ_LINK_RE.search(html)
            if m:
                resp = await context.request.get(f"https://vuzopedia.ru{m.group(1)}")
                vuz_html = await resp.text()
        except Exception:
            pass

        _vuz_page_cache[university] = vuz_html
        return vuz_html


async def vuzopedia_budget_places(university: str, specialty: str, context: BrowserContext) -> Optional[int]:
    """Ищем на vuzopedia.ru бюджетные места на направление. Платные места и
    квоты там закрыты регистрацией на сайте — не пытаемся их доставать."""
    vuz_html = await _get_vuz_page(university, context)
    if not vuz_html:
        return None

    napr_href = None
    code_match = SPECIALTY_CODE_RE.match(specialty)
    if code_match:
        code = code_match.group()
        for href, label in NAPR_LINK_RE.findall(vuz_html):
            if f"({code})" in label:
                napr_href = href
                break
    if not napr_href:
        name = specialty.strip().lower()
        for href, label in NAPR_LINK_RE.findall(vuz_html):
            if name and (name in label.lower() or label.split(" (")[0].strip().lower() == name):
                napr_href = href
                break
    if not napr_href:
        return None

    try:
        resp = await context.request.get(f"https://vuzopedia.ru{napr_href}")
        napr_html = await resp.text()
    except Exception:
        return None

    m = BUDGET_TOTAL_RE.search(napr_html)
    return int(m.group(1)) if m else None

COL_PATTERNS = {
    "score":    re.compile(r"балл|сумм|итог", re.I),
    "priority": re.compile(r"приорит", re.I),
    "consent":  re.compile(r"соглас", re.I),
}

# "балл"/"сумм" слишком общий паттерн и может раньше словить колонку с разбивкой
# по предметам (напр. ТПУ: "Баллы за ВИ" = "М(ЕГЭ): 74\nИ(ЕГЭ): 83..."), а не сам
# итоговый ранжирующий балл. "конкурсный балл" — устойчивое название именно него.
SCORE_STRONG_PATTERN = re.compile(r"конкурс\w*\s*балл|балл\w*\s*конкурс", re.I)

TRUTHY = {"да", "+", "✓", "v", "yes", "true", "1", "подано", "есть", "электронное", "бумажное", "письменное"}


def parse_int(s: str) -> Optional[int]:
    m = re.search(r"-?\d+", s or "")
    return int(m.group()) if m else None


def is_truthy(s: str) -> bool:
    return (s or "").strip().lower() in TRUTHY


def detect_columns(headers: list[str]) -> dict[str, int]:
    idx: dict[str, int] = {}
    # score: берём первую подходящую (обычно "Сумма баллов" идёт раньше "Балл ИД")
    for i, h in enumerate(headers):
        for key, pat in COL_PATTERNS.items():
            if key not in idx and pat.search(h or ""):
                idx[key] = i

    # если где-то есть явный "конкурсный балл" — это и есть итоговый балл,
    # даже если по общему паттерну раньше него нашлась другая колонка
    for i, h in enumerate(headers):
        if SCORE_STRONG_PATTERN.search(h or ""):
            idx["score"] = i
            break

    return idx


GRID_CELL_JS = """
() => {
    const containers = document.querySelectorAll('[class*="-report"]');
    const root = containers.length ? containers[0] : document;
    const out = [];
    for (const el of root.querySelectorAll('div[style*="left:"][style*="top:"]')) {
        const style = el.getAttribute('style') || '';
        const lm = style.match(/left:(-?[\\d.]+)px/);
        const tm = style.match(/top:(-?[\\d.]+)px/);
        if (!lm || !tm) continue;
        const text = el.textContent.replace(/\\u00a0/g, ' ').trim();
        if (!text) continue;
        out.push([parseFloat(tm[1]), parseFloat(lm[1]), text]);
    }
    return out;
}
"""


def align_row(header_lefts: list[float], row_cells: list[tuple[float, str]]) -> list[str]:
    aligned = [""] * len(header_lefts)
    for left, text in row_cells:
        idx = min(range(len(header_lefts)), key=lambda i: abs(header_lefts[i] - left))
        aligned[idx] = f"{aligned[idx]} {text}".strip() if aligned[idx] else text
    return aligned


async def extract_grid_from_frame(frame: Frame) -> Optional[tuple[list[str], list[list[str]]]]:
    """FastReport HTML5-экспорт без <table>: строки — это div'ы с
    абсолютным позиционированием (left/top), сгруппированные по top."""
    try:
        cells = await frame.evaluate(GRID_CELL_JS)
    except Exception:
        return None
    if not cells:
        return None

    rows: dict[int, list[tuple[float, str]]] = {}
    for top, left, text in cells:
        rows.setdefault(round(top), []).append((left, text))

    tops = sorted(rows)
    header_i = None
    for i, t in enumerate(tops):
        row = sorted(rows[t])
        if len(row) >= 4 and "score" in detect_columns([text for _, text in row]):
            header_i = i
            break
    if header_i is None:
        return None

    header_row = sorted(rows[tops[header_i]])
    headers = [text for _, text in header_row]
    header_lefts = [left for left, _ in header_row]

    body = [align_row(header_lefts, sorted(rows[t])) for t in tops[header_i + 1:]]
    return headers, body


async def extract_table_from_frame(frame: Frame) -> Optional[tuple[list[str], list[list[str]]]]:
    tables = await frame.query_selector_all("table")
    best, best_rows = None, 0
    for t in tables:
        rows = await t.query_selector_all("tr")
        if len(rows) > best_rows:
            best, best_rows = t, len(rows)
    if best is None or best_rows < 2:
        return None

    rows = await best.query_selector_all("tr")
    data: list[list[str]] = []
    for r in rows:
        cells = await r.query_selector_all("th, td")
        data.append([(await c.inner_text()).strip() for c in cells])

    # заголовок = первая непустая строка; иногда FastReport делает пару строк заголовков
    header_idx = 0
    for i, row in enumerate(data):
        if any(c for c in row):
            header_idx = i
            break

    headers = data[header_idx]
    body = [r for r in data[header_idx + 1:] if any(c for c in r)]
    return headers, body


# картинки/шрифты/видео не нужны для парсинга — блокируем, чтобы не тратить
# время и трафик; трекеры (Метрика/VK) блокируем отдельно по домену, они и так
# не дают быстро отловить networkidle и просто греют CPU параллельными вкладками
BLOCKED_RESOURCE_TYPES = {"image", "font", "media"}
BLOCKED_HOST_SUBSTRINGS = ("mc.yandex", "yandex.ru/watch", "vk.com", "doubleclick", "google-analytics")


async def _route_filter(route) -> None:
    req = route.request
    if req.resource_type in BLOCKED_RESOURCE_TYPES or any(h in req.url for h in BLOCKED_HOST_SUBSTRINGS):
        await route.abort()
    else:
        await route.continue_()


async def fetch_table(url: str, browser: Browser) -> tuple[list[str], list[list[str]], str, str, Optional[int]]:
    context = await browser.new_context()
    try:
        page = await context.new_page()
        await page.route("**/*", _route_filter)
        # domcontentloaded вместо networkidle: трекеры (Метрика/VK) держат
        # сеть занятой и не дают дождаться networkidle по многу секунд впустую
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        # перебираем все фреймы, включая главный; опрашиваем, пока данные не появятся
        result = None
        for _ in range(15):  # до ~15 сек ожидания данных
            for frame in page.frames:
                try:
                    r = await extract_table_from_frame(frame) or await extract_grid_from_frame(frame)
                except Exception:
                    r = None
                if r and len(r[1]) > 0:
                    result = r
                    break
            if result:
                break
            await page.wait_for_timeout(500)

        if not result:
            raise RuntimeError("Таблица не найдена ни в одном фрейме страницы")

        specialty = await detect_specialty(page, context)
        budget_places = await vuzopedia_budget_places(university_name(url), specialty, context)
    finally:
        await context.close()

    headers, body = result
    return headers, body, university_name(url), specialty, budget_places


def analyze(headers: list[str], body: list[list[str]], my_score: int) -> dict:
    col = detect_columns(headers)
    if "score" not in col:
        raise RuntimeError(f"Колонка баллов не найдена. Заголовки: {headers}")

    above = 0
    above_prio1 = 0
    above_prio1_consent = 0

    for r in body:
        if len(r) <= col["score"]:
            continue
        score = parse_int(r[col["score"]])
        if score is None or score <= my_score:
            continue
        above += 1

        prio = parse_int(r[col["priority"]]) if "priority" in col and len(r) > col["priority"] else None
        consent = is_truthy(r[col["consent"]]) if "consent" in col and len(r) > col["consent"] else False

        if prio == 1:
            above_prio1 += 1
            if consent:
                above_prio1_consent += 1

    return {
        "total": len(body),
        "above": above,
        "above_prio1": above_prio1,
        "above_prio1_consent": above_prio1_consent,
        "headers": headers,
        "col": col,
    }


def print_result(url: str, r: dict) -> None:
    print(f"\n=== {url} ===")
    print(f"Вуз:                                              {r['university']}")
    budget = r["budget_places"]
    print(f"Бюджетных мест на направление:                   {budget if budget is not None else 'не найдено'}")
    print(f"Специальность:                                    {r['specialty']}")
    print(f"Всего в списке:                                  {r['total']}")
    print(f"Выше по баллам:                                  {r['above']}")
    if "priority" in r["col"]:
        print(f"Выше по баллам с 1-м приоритетом:                {r['above_prio1']}")
        if "consent" in r["col"]:
            print(f"Выше по баллам с 1-м приоритетом и согласием:    {r['above_prio1_consent']}")
        else:
            print("(колонка согласия не найдена)")
    else:
        print("(колонка приоритета не найдена)")


async def fetch_and_analyze(url: str, my_score: int, browser: Browser) -> dict:
    headers, body, university, specialty, budget_places = await fetch_table(url, browser)
    r = analyze(headers, body, my_score)
    r["university"] = university
    r["specialty"] = specialty
    r["budget_places"] = budget_places
    return r


async def process_batch(
    urls: list[str],
    my_score: int,
    browser: Browser,
    on_result: Optional[callable] = None,
) -> tuple[dict[str, dict], dict[str, str]]:
    """on_result(url, result_or_None, error_or_None) вызывается сразу же,
    как только отработала очередная ссылка — не дожидаясь всего батча."""
    results: dict[str, dict] = {}
    errors: dict[str, str] = {}

    async def run_one(url: str) -> None:
        try:
            r = await fetch_and_analyze(url, my_score, browser)
            results[url] = r
            if on_result:
                on_result(url, r, None)
        except Exception as e:
            errors[url] = str(e)
            if on_result:
                on_result(url, None, str(e))

    await asyncio.gather(*(run_one(url) for url in urls))
    return results, errors


DEFAULT_LINKS_FILE = "links.txt"


def read_urls_from_file(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


async def main() -> None:
    mode = input("Ссылка вручную или из файла? [1 - вручную, 2 - файл]: ").strip()

    if mode == "2":
        path = Path(input(f"Путь к файлу [{DEFAULT_LINKS_FILE}]: ").strip() or DEFAULT_LINKS_FILE)
        if not path.is_file():
            print(f"Файл не найден: {path}", file=sys.stderr)
            sys.exit(1)
        urls = read_urls_from_file(path)
        if not urls:
            print("В файле нет ссылок", file=sys.stderr)
            sys.exit(1)
    else:
        entry = input("URL списка: ").strip()
        if not entry:
            print("Пустой ввод", file=sys.stderr)
            sys.exit(1)
        urls = [entry]

    try:
        my_score = int(input("Твои баллы: ").strip())
    except ValueError:
        print("Баллы должны быть числом", file=sys.stderr)
        sys.exit(1)

    def stream(url: str, r: Optional[dict], _e: Optional[str]) -> None:
        if r:
            print_result(url, r)

    print(f"\nЗагружаю {len(urls)} ссылок параллельно...", flush=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            results, errors = await process_batch(urls, my_score, browser, on_result=stream)

            if errors:
                print(f"\nПовтор для {len(errors)} упавших ссылок...", flush=True)
                retry_results, errors = await process_batch(list(errors), my_score, browser, on_result=stream)
                results.update(retry_results)
        finally:
            await browser.close()

    if errors:
        if len(errors) > 5:
            print(f"\nОшибка: не удалось загрузить {len(errors)} ссылок из {len(urls)}", file=sys.stderr)
        else:
            for url, e in errors.items():
                print(f"\n=== {url} ===")
                print(f"Ошибка: {e}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())