#!/usr/bin/env python3
"""
GUI для priem_check.py: нативное окно на компьютере (pywebview) — можно
перетащить links.txt, выбрать файл или ввести одну ссылку, указать баллы и
нажать "Начать". Прогресс и результаты обновляются в реальном времени,
результаты можно сохранить таблицей (CSV).

Установка:
    pip install playwright pywebview
    playwright install chromium

Запуск:
    python priem_gui.py
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import webview
from playwright.async_api import async_playwright

from priem_check import process_batch

STATE_LOCK = threading.Lock()
STATE: dict = {"status": "idle", "total": 0, "done": 0, "items": []}


def _reset_state(total: int) -> None:
    with STATE_LOCK:
        STATE["status"] = "running"
        STATE["total"] = total
        STATE["done"] = 0
        STATE["items"] = []


def _add_item(url: str, ok: bool, data: Optional[dict] = None, error: Optional[str] = None) -> None:
    with STATE_LOCK:
        STATE["items"].append({"url": url, "ok": ok, "data": data, "error": error})
        STATE["done"] += 1


def _finish() -> None:
    with STATE_LOCK:
        STATE["status"] = "done"


def run_batch_thread(urls: list[str], score: int) -> None:
    async def runner() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                def on_first(url: str, r: Optional[dict], _e: Optional[str]) -> None:
                    # ошибки первого прохода не показываем сразу — ждём retry,
                    # чтобы каждая ссылка попала в вывод ровно один раз
                    if r is not None:
                        _add_item(url, True, data=r)

                results, errors = await process_batch(urls, score, browser, on_result=on_first)

                if errors:
                    def on_retry(url: str, r: Optional[dict], e: Optional[str]) -> None:
                        _add_item(url, r is not None, data=r, error=e)

                    await process_batch(list(errors), score, browser, on_result=on_retry)
            finally:
                await browser.close()

    _reset_state(len(urls))
    try:
        asyncio.run(runner())
    finally:
        _finish()


HTML_PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>КОНКУРС — проверка списков</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #ffffff; --surface: #ffffff; --ink: #0a0a0a; --soft: #6f6f6f;
    --line: #dcdcdc; --line-strong: #0a0a0a;
    --invert-bg: #0a0a0a; --invert-ink: #ffffff;
    --row-hover: #0a0a0a; --row-hover-ink: #ffffff;
    --shadow: 0 1px 0 rgba(0,0,0,.04);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #050505; --surface: #0c0c0c; --ink: #f2f2f2; --soft: #8d8d8d;
      --line: #232323; --line-strong: #f2f2f2;
      --invert-bg: #f2f2f2; --invert-ink: #050505;
      --row-hover: #f2f2f2; --row-hover-ink: #050505;
      --shadow: none;
    }
  }
  * { box-sizing: border-box; }
  ::selection { background: var(--ink); color: var(--bg); }
  html, body { background: var(--bg); }
  body {
    margin: 0; padding: 48px 40px 80px; color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    background-image: radial-gradient(var(--line) 1px, transparent 1px);
    background-size: 22px 22px;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1080px; margin: 0 auto; }

  .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
  .mark { width: 22px; height: 22px; position: relative; flex: none; }
  .mark::before, .mark::after {
    content: ''; position: absolute; inset: 0; border: 2px solid var(--ink); border-radius: 3px;
  }
  .mark::after { background: var(--ink); transform: translate(6px, 6px); }
  .kicker {
    font: 700 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .18em;
    text-transform: uppercase; color: var(--soft);
  }
  h1 {
    font-size: clamp(34px, 5vw, 52px); line-height: 1; font-weight: 800; letter-spacing: -.02em;
    margin: 10px 0 6px;
  }
  .sub { color: var(--soft); font-size: 14px; margin: 0 0 34px; max-width: 520px; }

  .panel {
    background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
    padding: 26px; margin-bottom: 22px; box-shadow: var(--shadow);
  }

  .dropzone {
    border: 1.5px dashed var(--line-strong); border-radius: 10px; padding: 34px 20px;
    text-align: center; cursor: pointer; transition: background .15s, color .15s;
    color: var(--soft); font-size: 14px;
  }
  .dropzone .glyph {
    font-size: 20px; display: block; margin-bottom: 8px; transition: transform .15s;
  }
  .dropzone.drag { background: var(--invert-bg); color: var(--invert-ink); }
  .dropzone.drag .glyph { transform: translateY(-3px); }
  .dropzone .fname { margin-top: 10px; color: var(--ink); font-weight: 700; }
  .dropzone.drag .fname { color: inherit; }

  .divider {
    display: flex; align-items: center; gap: 12px; color: var(--soft);
    font: 700 10px/1 ui-monospace, monospace; letter-spacing: .16em; text-transform: uppercase;
    margin: 22px 0;
  }
  .divider::before, .divider::after { content: ''; flex: 1; height: 1px; background: var(--line); }

  .field { margin-bottom: 16px; }
  .field label {
    display: block; font: 700 10px/1 ui-monospace, monospace; letter-spacing: .14em;
    text-transform: uppercase; color: var(--soft); margin-bottom: 8px;
  }
  input[type=text], input[type=number] {
    width: 100%; padding: 12px 14px; border-radius: 8px; border: 1px solid var(--line);
    background: transparent; color: var(--ink); font-size: 15px; font-family: inherit;
  }
  input[type=number] { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  input::placeholder { color: var(--soft); opacity: .7; }
  input:focus { outline: none; border-color: var(--line-strong); }

  button {
    width: 100%; padding: 14px; border-radius: 8px; cursor: pointer;
    font: 800 12px/1 ui-monospace, monospace; letter-spacing: .12em; text-transform: uppercase;
    background: var(--invert-bg); color: var(--invert-ink); border: 1.5px solid var(--invert-bg);
    transition: background .12s, color .12s;
  }
  button:hover:not(:disabled) { background: transparent; color: var(--ink); }
  button:disabled { opacity: .4; cursor: default; }

  #csvBtn {
    width: auto; background: transparent; color: var(--ink); border: 1.5px solid var(--line-strong);
    padding: 10px 16px; margin: 0 0 14px auto;
  }
  #csvBtn:not([hidden]) { display: block; }
  #csvBtn:hover:not(:disabled) { background: var(--invert-bg); color: var(--invert-ink); }

  .progress-wrap { margin-top: 22px; }
  .progress-bar { height: 3px; background: var(--line); overflow: hidden; position: relative; }
  .progress-fill { height: 100%; width: 0%; background: var(--ink); transition: width .25s ease; }
  .progress-fill.busy {
    background-image: repeating-linear-gradient(-45deg, var(--ink) 0 8px, var(--soft) 8px 16px);
    background-size: 200% 100%; animation: stripes 1s linear infinite;
  }
  @keyframes stripes { to { background-position: -32px 0; } }
  .progress-text {
    font: 700 11px/1 ui-monospace, monospace; letter-spacing: .1em; color: var(--soft);
    margin-top: 10px; text-transform: uppercase;
  }

  .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: var(--line);
    border: 1px solid var(--line); border-radius: 14px; overflow: hidden; margin-bottom: 22px; }
  .stat { background: var(--surface); padding: 20px 22px; }
  .stat .n { font: 800 34px/1 ui-monospace, monospace; letter-spacing: -.02em; }
  .stat .l { font: 700 10px/1 ui-monospace, monospace; letter-spacing: .14em; text-transform: uppercase;
    color: var(--soft); margin-top: 8px; }

  .table-panel { border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
  .table-scroll { overflow: auto; max-height: 560px; }
  /* border-collapse:collapse ломает position:sticky на th в WebKit (наезжает на
     строки при скролле) — separate + border-spacing:0 держит и залипание, и вид хairline-границ */
  table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 13.5px; }
  thead th {
    position: sticky; top: 0; z-index: 1; background: var(--surface); text-align: left; padding: 12px 16px;
    font: 700 10px/1 ui-monospace, monospace; letter-spacing: .12em; text-transform: uppercase;
    color: var(--soft); border-bottom: 1px solid var(--line); white-space: nowrap;
  }
  tbody td {
    padding: 12px 16px; border-bottom: 1px solid var(--line); vertical-align: top;
  }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr { transition: background .1s, color .1s; }
  tbody tr:hover { background: var(--row-hover); color: var(--row-hover-ink); }
  tbody tr:hover .soft { color: inherit; opacity: .65; }
  .num { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; text-align: right; white-space: nowrap; }
  .num.hero { font-weight: 800; font-size: 15px; }
  .soft { color: var(--soft); }
  .ellipsis { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge {
    display: inline-block; font: 700 9px/1 ui-monospace, monospace; letter-spacing: .08em;
    padding: 4px 6px; border-radius: 4px; border: 1px solid var(--line-strong); text-transform: uppercase;
  }
  .badge.err { background: var(--invert-bg); color: var(--invert-ink); border-color: var(--invert-bg); }
  tr.enter { opacity: 0; transform: translateY(6px); }
  tr.enter-active { opacity: 1; transform: translateY(0); transition: opacity .25s ease, transform .25s ease; }

  .empty { padding: 60px 20px; text-align: center; color: var(--soft); font-size: 13px; }
  .foot { margin-top: 16px; font: 700 11px/1 ui-monospace, monospace; letter-spacing: .08em;
    text-transform: uppercase; color: var(--soft); }
</style>
</head>
<body>
<div class="wrap">
  <div class="brand"><div class="mark"></div><span class="kicker">Admission Radar</span></div>
  <h1>Конкурсные списки</h1>
  <p class="sub">Проверка позиции по баллам сразу по всем направлениям — параллельно, с бюджетными местами и экспортом в таблицу.</p>

  <div class="panel">
    <div class="dropzone" id="dropzone">
      <span class="glyph">↑</span>
      <div>Перетащите <b>links.txt</b> сюда или нажмите, чтобы выбрать файл</div>
      <div class="fname" id="fname"></div>
    </div>
    <input type="file" id="fileInput" accept=".txt" hidden>

    <div class="divider">или ссылка вручную</div>

    <div class="field">
      <label for="urlInput">Ссылка на список</label>
      <input type="text" id="urlInput" placeholder="https://...">
    </div>
    <div class="field">
      <label for="scoreInput">Твои баллы</label>
      <input type="number" id="scoreInput" placeholder="200">
    </div>

    <button id="startBtn">Начать проверку</button>

    <div class="progress-wrap" id="progressWrap" hidden>
      <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
      <div class="progress-text" id="progressText"></div>
    </div>
  </div>

  <div id="statsRow" hidden></div>
  <button id="csvBtn" hidden>Экспорт CSV</button>

  <div class="table-panel" id="tablePanel" hidden>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>#</th><th>Вуз</th><th>Код</th><th>Специальность</th>
            <th style="text-align:right">Бюджет</th><th style="text-align:right">Всего</th>
            <th style="text-align:right">Выше</th><th style="text-align:right">+П1</th>
            <th style="text-align:right">+Согл</th><th>Источник</th>
          </tr>
        </thead>
        <tbody id="resultsBody"></tbody>
      </table>
    </div>
  </div>
  <div class="empty" id="emptyState">Загрузите список и нажмите «Начать проверку»</div>
</div>

<script>
let droppedUrls = [];
let rendered = 0;
let poller = null;
let allItems = [];
let rowIndex = 0;

const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const fname = document.getElementById('fname');
const urlInput = document.getElementById('urlInput');
const scoreInput = document.getElementById('scoreInput');
const startBtn = document.getElementById('startBtn');
const progressWrap = document.getElementById('progressWrap');
const progressFill = document.getElementById('progressFill');
const progressText = document.getElementById('progressText');
const resultsBody = document.getElementById('resultsBody');
const tablePanel = document.getElementById('tablePanel');
const emptyState = document.getElementById('emptyState');
const statsRow = document.getElementById('statsRow');
const csvBtn = document.getElementById('csvBtn');

function parseLinksText(text) {
  return text.split(/\r?\n/).map(s => s.trim()).filter(s => s && !s.startsWith('#'));
}

function loadFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    droppedUrls = parseLinksText(reader.result);
    fname.textContent = file.name + ' — ' + droppedUrls.length + ' ссылок';
  };
  reader.readAsText(file, 'utf-8');
}

dropzone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => { if (fileInput.files[0]) loadFile(fileInput.files[0]); });
['dragover', 'dragenter'].forEach(ev => dropzone.addEventListener(ev, e => {
  e.preventDefault(); dropzone.classList.add('drag');
}));
['dragleave', 'dragend'].forEach(ev => dropzone.addEventListener(ev, () => dropzone.classList.remove('drag')));
dropzone.addEventListener('drop', e => {
  e.preventDefault(); dropzone.classList.remove('drag');
  const file = e.dataTransfer.files[0];
  if (file) loadFile(file);
});

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
}

function renderRow(item) {
  rowIndex += 1;
  const tr = document.createElement('tr');
  tr.className = 'enter';

  if (item.ok) {
    const d = item.data;
    const hasPrio = d.col && ('priority' in d.col);
    const hasConsent = d.col && ('consent' in d.col);
    tr.innerHTML = `
      <td class="num soft">${rowIndex}</td>
      <td><b>${esc(d.university)}</b></td>
      <td class="num soft">${d.specialty_code || '—'}</td>
      <td class="ellipsis" title="${esc(d.specialty)}">${esc(d.specialty)}</td>
      <td class="num">${d.budget_places ?? '—'}</td>
      <td class="num">${d.total}</td>
      <td class="num hero">${d.above}</td>
      <td class="num">${hasPrio ? d.above_prio1 : '—'}</td>
      <td class="num">${hasConsent ? d.above_prio1_consent : '—'}</td>
      <td class="ellipsis soft" title="${esc(item.url)}">${esc(hostOf(item.url))}</td>
    `;
  } else {
    tr.innerHTML = `
      <td class="num soft">${rowIndex}</td>
      <td colspan="8" title="${esc(item.error || '')}">
        <span class="badge err">ошибка</span>&nbsp; ${esc(item.error || 'неизвестно')}
      </td>
      <td class="ellipsis soft" title="${esc(item.url)}">${esc(hostOf(item.url))}</td>
    `;
  }

  resultsBody.appendChild(tr);
  requestAnimationFrame(() => requestAnimationFrame(() => tr.classList.add('enter-active')));
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function updateStats(items) {
  const ok = items.filter(i => i.ok).length;
  const err = items.length - ok;
  statsRow.innerHTML = `
    <div class="stats">
      <div class="stat"><div class="n">${items.length}</div><div class="l">Проверено</div></div>
      <div class="stat"><div class="n">${ok}</div><div class="l">Успешно</div></div>
      <div class="stat"><div class="n">${err}</div><div class="l">Ошибок</div></div>
    </div>`;
  statsRow.hidden = items.length === 0;
}

async function poll() {
  const res = await fetch('/status');
  const state = await res.json();

  const busy = state.status === 'running';
  const pct = state.total ? Math.round(100 * state.done / state.total) : 0;
  progressFill.style.width = pct + '%';
  progressFill.classList.toggle('busy', busy);
  progressText.textContent = `${busy ? 'ИДЁТ ПРОВЕРКА' : 'ГОТОВО'} — ${state.done} / ${state.total}`;

  for (; rendered < state.items.length; rendered++) {
    renderRow(state.items[rendered]);
    allItems.push(state.items[rendered]);
  }
  if (allItems.length) {
    emptyState.hidden = true;
    tablePanel.hidden = false;
  }
  updateStats(allItems);

  if (state.status === 'done') {
    clearInterval(poller);
    poller = null;
    startBtn.disabled = false;
    startBtn.textContent = 'Начать проверку';
    if (allItems.length) csvBtn.hidden = false;
  }
}

function csvField(v) {
  const s = (v === undefined || v === null) ? '' : String(v);
  return '"' + s.replace(/"/g, '""') + '"';
}

function buildCsv(items) {
  const header = [
    'Ссылка', 'Вуз', 'Номер специальности', 'Бюджетных мест на направление', 'Специальность',
    'Всего в списке', 'Выше по баллам', 'Выше с 1-м приоритетом',
    'Выше с 1-м приоритетом и согласием', 'Ошибка',
  ];
  const lines = [header.map(csvField).join(',')];
  for (const it of items) {
    const d = it.data || {};
    lines.push([
      it.url,
      it.ok ? d.university : '',
      it.ok ? (d.specialty_code || '') : '',
      it.ok ? (d.budget_places ?? '') : '',
      it.ok ? d.specialty : '',
      it.ok ? d.total : '',
      it.ok ? d.above : '',
      it.ok && d.col && ('priority' in d.col) ? d.above_prio1 : '',
      it.ok && d.col && ('consent' in d.col) ? d.above_prio1_consent : '',
      it.ok ? '' : (it.error || 'ошибка'),
    ].map(csvField).join(','));
  }
  return '﻿' + lines.join('\r\n');
}

csvBtn.addEventListener('click', async () => {
  const csv = buildCsv(allItems);
  if (window.pywebview && window.pywebview.api) {
    const status = await window.pywebview.api.save_csv(csv);
    if (status === 'ok') csvBtn.textContent = 'Сохранено ✓';
    setTimeout(() => { csvBtn.textContent = 'Экспорт CSV'; }, 2000);
  } else {
    const blob = new Blob([csv], {type: 'text/csv;charset=utf-8'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'conkurs.csv';
    a.click();
  }
});

startBtn.addEventListener('click', async () => {
  const urls = droppedUrls.length ? droppedUrls : (urlInput.value.trim() ? [urlInput.value.trim()] : []);
  const score = parseInt(scoreInput.value, 10);

  if (!urls.length) { alert('Загрузите links.txt или введите ссылку'); return; }
  if (!Number.isFinite(score)) { alert('Введите баллы числом'); return; }

  resultsBody.innerHTML = '';
  rendered = 0;
  rowIndex = 0;
  allItems = [];
  emptyState.hidden = false;
  tablePanel.hidden = true;
  statsRow.hidden = true;
  csvBtn.hidden = true;
  csvBtn.textContent = 'Экспорт CSV';
  progressWrap.hidden = false;
  progressFill.classList.add('busy');
  progressFill.style.width = '0%';
  progressText.textContent = `ИДЁТ ПРОВЕРКА — 0 / ${urls.length}`;
  startBtn.disabled = true;
  startBtn.textContent = 'Загружаю...';

  const resp = await fetch('/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({urls, score}),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    alert('Не удалось запустить: ' + (err.error || resp.status));
    startBtn.disabled = false;
    startBtn.textContent = 'Начать проверку';
    return;
  }

  poller = setInterval(poll, 400);
});
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002 — глушим access-лог
        pass

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — имя метода задано BaseHTTPRequestHandler
        if self.path in ("/", "/index.html"):
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/status":
            with STATE_LOCK:
                snapshot = json.loads(json.dumps(STATE, ensure_ascii=False))
            self._send_json(snapshot)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/start":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length))
            urls = [u.strip() for u in payload.get("urls", []) if u.strip()]
            score = int(payload.get("score"))
        except Exception:
            self._send_json({"error": "bad request"}, 400)
            return

        with STATE_LOCK:
            running = STATE["status"] == "running"
        if running:
            self._send_json({"error": "already running"}, 409)
            return
        if not urls:
            self._send_json({"error": "no urls"}, 400)
            return

        threading.Thread(target=run_batch_thread, args=(urls, score), daemon=True).start()
        self._send_json({"ok": True})


class Api:
    """Мост JS -> Python: нативный диалог "Сохранить как" для CSV."""

    def save_csv(self, csv_text: str) -> str:
        path = webview.windows[0].create_file_dialog(
            webview.SAVE_DIALOG, save_filename="conkurs.csv"
        )
        if not path:
            return "cancelled"
        target = path[0] if isinstance(path, (list, tuple)) else path
        with open(target, "w", encoding="utf-8-sig", newline="") as f:
            f.write(csv_text)
        return "ok"


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    webview.create_window(
        "Конкурс — проверка списков",
        f"http://127.0.0.1:{port}/",
        js_api=Api(),
        width=1180,
        height=880,
        min_size=(760, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
