#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
truck-watch — следит за Google-таблицей отгрузок и присылает события в MAX.

События:
    arrived  — в строке появился номер Т/С  (машина прибыла)
    loading  — строка стала зелёной          (начали грузиться)
    departed — строка стала жёлтой           (машина выехала)
    problem  — появилась красная ячейка      (форс-мажор)

Зависимостей нет: только стандартная библиотека Python 3.9+.

Команды:
    python watcher.py --calibrate            какие цвета реально есть в таблице
    python watcher.py --once                 один цикл (проверка)
    python watcher.py --test-notify          тестовое сообщение в MAX
    python watcher.py --file demo.xlsx --once  работа с локальным файлом
    python watcher.py                        работать постоянно
"""

from __future__ import annotations

import argparse
import colorsys
import copy
import io
import json
import logging
import os
import re
import socket
import ssl
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
STATE_PATH = BASE / "state.json"
LOG_PATH = BASE / "watcher.log"
CACHE_DIR = BASE / "cache"
REPORT_PATH = BASE / "colors_report.txt"

MAX_API = "https://platform-api2.max.ru"
TELEGRAM_API = "https://api.telegram.org"
USER_AGENT = "Mozilla/5.0 (truck-watch/1.0)"

# Настройки по умолчанию — работают, даже если config.json нет вовсе.
# Именно поэтому в облако достаточно загрузить один файл скрипта.
DEFAULT_CONFIG: dict = {
    "sheet_url": "",
    "poll_seconds": 60,
    "cities": [],
    "plates": [],
    "filter_mode": "all",
    "max": {"token": "", "chat_id": "", "insecure_tls": False},
    "telegram": {"token": "", "chat_id": "", "share": True},
    "events": {
        "new_row": False,
        "arrived": True,
        "loading": True,
        "departed": True,
        "problem": True,
    },
    "colors": {
        "min_saturation": 0.12,
        "min_value": 0.45,
        "green_hue": [0.18, 0.46],
        "yellow_hue": [0.13, 0.19],
        "red_hue": [[0.0, 0.05], [0.95, 1.0]],
        "status_exclude": [
            "сортировка", "№ мл", "город", "дата", "коментарии", "комментарии",
        ],
        "red_exclude": ["сортировка", "№ мл", "город", "дата"],
    },
    "quiet_hours": [],
}

log = logging.getLogger("truck-watch")

_lock_socket = None


def acquire_single_instance(port: int = 47823) -> bool:
    """Занимает локальный порт. Второй экземпляр не сможет его занять."""
    global _lock_socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        _lock_socket = sock
        return True
    except OSError:
        sock.close()
        return False


# ================================================================ утилиты


def norm(text) -> str:
    """Нормализует текст для сравнения: регистр, пробелы, ё."""
    if text is None:
        return ""
    s = str(text).replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s.replace("ё", "е")


def cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_sheet_url(url: str) -> tuple[str, str]:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise SystemExit(f"Не похоже на ссылку Google Sheets: {url}")
    s = re.search(r"[?#&]gid=(\d+)", url)
    return m.group(1), (s.group(1) if s else "0")


# ================================================================ XLSX


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _col_index(ref: str) -> int:
    """'B12' -> 2 (номер колонки, с единицы)."""
    letters = "".join(ch for ch in ref if ch.isalpha())
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - 64)
    return idx


class Grid:
    """Минимальное представление листа: значения и заливки по (строка, колонка)."""

    def __init__(self, sheet_title: str = ""):  # ← Добавляем параметр
        self.sheet_title = sheet_title  # ← Сохраняем название листа
        self.cells: dict[tuple[int, int], tuple[str, str | None]] = {}
        self.max_row = 0
        self.max_col = 0

    def value(self, row: int, col: int) -> str:
        item = self.cells.get((row, col))
        return item[0] if item else ""

    def fill(self, row: int, col: int) -> str | None:
        item = self.cells.get((row, col))
        return item[1] if item else None


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out = []
    for si in root:
        if _local(si.tag) != "si":
            continue
        parts = [t.text or "" for t in si.iter() if _local(t.tag) == "t"]
        out.append("".join(parts))
    return out


def _read_styles(zf: zipfile.ZipFile) -> list[str | None]:
    """Возвращает список цветов заливки по индексу стиля ячейки (cellXfs)."""
    if "xl/styles.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/styles.xml"))

    fills: list[str | None] = []
    for node in root.iter():
        if _local(node.tag) != "fills":
            continue
        for fill in node:
            color = None
            for sub in fill.iter():
                if _local(sub.tag) in ("fgColor", "bgColor"):
                    rgb = sub.get("rgb")
                    if rgb and len(rgb) == 8 and rgb.upper() not in ("00000000", "FFFFFFFF"):
                        color = rgb[2:].upper()
                        break
            fills.append(color)
        break

    xf_fill: list[str | None] = []
    for node in root.iter():
        if _local(node.tag) != "cellXfs":
            continue
        for xf in node:
            if _local(xf.tag) != "xf":
                continue
            try:
                fid = int(xf.get("fillId", "0"))
            except ValueError:
                fid = 0
            xf_fill.append(fills[fid] if fid < len(fills) else None)
        break
    return xf_fill


def _parse_sheet(xml_bytes: bytes, shared: list[str], styles: list[str | None], sheet_title: str = "") -> Grid:
    grid = Grid(sheet_title=sheet_title)  # ← Передаём название листа
    # Остальной код остаётся без изменений
    root = ET.fromstring(xml_bytes)
    for row_node in root.iter():
        if _local(row_node.tag) != "row":
            continue
        try:
            row_idx = int(row_node.get("r", "0"))
        except ValueError:
            continue
        if row_idx == 0:
            continue
        grid.max_row = max(grid.max_row, row_idx)
        for cell in row_node:
            if _local(cell.tag) != "c":
                continue
            ref = cell.get("r", "")
            col_idx = _col_index(ref)
            if col_idx == 0:
                continue
            grid.max_col = max(grid.max_col, col_idx)

            ctype = cell.get("t", "")
            style_idx = int(cell.get("s", "0") or 0)
            fill = styles[style_idx] if 0 <= style_idx < len(styles) else None

            text = ""
            if ctype == "inlineStr":
                text = "".join(
                    t.text or "" for t in cell.iter() if _local(t.tag) == "t"
                )
            else:
                value_node = None
                for child in cell:
                    if _local(child.tag) == "v":
                        value_node = child
                        break
                raw = value_node.text if value_node is not None else ""
                if raw is None:
                    raw = ""
                if ctype == "s":
                    try:
                        text = shared[int(raw)]
                    except (ValueError, IndexError):
                        text = ""
                elif ctype == "b":
                    text = "да" if raw == "1" else "нет"
                else:
                    text = raw

            grid.cells[(row_idx, col_idx)] = (text.strip(), fill)
    return grid


def xlsx_to_grids(data: bytes) -> list[Grid]:
    """Читает все листы xlsx в список Grid."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    shared = _read_shared_strings(zf)
    styles = _read_styles(zf)

    names = [n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
    names.sort(key=lambda n: int(re.search(r"sheet(\d+)\.xml", n).group(1)))

    # --- НОВЫЙ КОД: Извлекаем названия листов ---
    workbook_xml = zf.read("xl/workbook.xml")
    root = ET.fromstring(workbook_xml)
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    sheet_names = []
    for s in root.findall(".//main:sheet", ns):
        sheet_names.append(s.get("name", f"Sheet{s.get('sheetId', '?')}"))

    result = []
    for i, n in enumerate(names):
        title = sheet_names[i] if i < len(sheet_names) else f"Sheet{i+1}"
        result.append(_parse_sheet(zf.read(n), shared, styles, title))
    # --- КОНЕЦ НОВОГО КОДА ---

    return result


# ================================================================ цвета


def classify_hex(hex_color: str | None, cfg: dict) -> str | None:
    """Раскладывает цвет заливки по корзинам green / yellow / red."""
    if not hex_color or len(hex_color) != 6:
        return None
    try:
        r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None

    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < cfg.get("min_saturation", 0.12) or v < cfg.get("min_value", 0.45):
        return None

    for lo, hi in cfg.get("red_hue", [[0.0, 0.05], [0.95, 1.0]]):
        if lo <= h <= hi:
            return "red"
    grn = cfg.get("green_hue", [0.18, 0.46])
    yel = cfg.get("yellow_hue", [0.08, 0.18])
    if grn[0] <= h <= grn[1]:
        return "green"
    if yel[0] <= h <= yel[1]:
        return "yellow"
    return None


# ================================================================ загрузка



    return data


def pick_grid(grids: list[Grid]) -> tuple[Grid, int]:
    """Выбирает лист с сегодняшней датой и строку заголовков (там, где есть «ГОРОД»)."""
    import datetime

    today_str = datetime.datetime.now().strftime("%d.%m")  # Формат "11.11"

    # 1. Ищем лист с сегодняшней датой в названии
    for grid in grids:
        if hasattr(grid, 'sheet_title') and today_str in grid.sheet_title:
            header = find_header(grid)
            if header:
                return grid, header

    # 2. Если не нашли — берём ПОСЛЕДНИЙ лист
    for grid in reversed(grids):
        header = find_header(grid)
        if header:
            log.info(f"Переключился на лист: {getattr(grid, 'sheet_title', 'unknown')}")
            return grid, header

    raise RuntimeError("Ни на одном листе не нашёл заголовок «ГОРОД».")


def find_header(grid: Grid, max_scan: int = 15) -> int | None:
    for row in range(1, min(max_scan, max(grid.max_row, 1)) + 1):
        for col in range(1, min(max(grid.max_col, 1), 30) + 1):
            if norm(grid.value(row, col)) == "город":
                return row
    return None


ALIASES = {
    "city": ["город"],
    "date": ["дата время", "дата"],
    "ml": ["№ мл", "мл"],
    "volume": ["объем", "объём"],
    "nav": ["навигация"],
    "fio": ["фио"],
    "eto": ["это"],
    "tires": ["шины"],
    "disks": ["диски"],
    "axes": ["аксы"],
    "export": ["вывоз"],
    "pandus": ["пандус"],
    "plate": ["номер т/с", "номер тс", "т/с"],
    "shm": ["ш/м", "шм"],
    "sort": ["сортировка на", "сортировка"],
    "comment": ["комментарии", "комментарий", "коментарии", "коментарий"],
}

DATE_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}\s+\d{1,2}:\d{2}$")


def build_columns(grid: Grid, header_row: int) -> dict[str, int]:
    found: dict[str, int] = {}
    for row in (header_row, header_row + 1):
        for col in range(1, min(max(grid.max_col, 1), 40) + 1):
            text = norm(grid.value(row, col))
            if not text:
                continue
            for key, names in ALIASES.items():
                if key in found:
                    continue
                if text in names:
                    found[key] = col
    return found


def columns_by_name(grid: Grid, header_row: int, names: set[str]) -> set[int]:
    """Индексы колонок, у которых заголовок (в шапке или подшапке) входит в names."""
    result = set()
    for col in range(1, max(grid.max_col, 1) + 1):
        for row in (header_row, header_row + 1):
            if norm(grid.value(row, col)) in names:
                result.add(col)
                break
    return result


def guess_date_column(grid: Grid, header_row: int) -> int | None:
    """Ищет колонку с датой вида 05.11.2026 02:30 — у неё может не быть заголовка."""
    for col in range(1, min(max(grid.max_col, 1), 6) + 1):
        hits = sum(
            1
            for r in range(header_row + 1, min(grid.max_row, header_row + 40) + 1)
            if DATE_RE.match(grid.value(r, col).strip())
        )
        if hits >= 3:
            return col
    return None


def load_rows(data: bytes, cfg: dict) -> dict[str, dict]:
    """Превращает xlsx в словарь строк {ключ: запись}."""
    grids = xlsx_to_grids(data)
    grid, header_row = pick_grid(grids)
    cols = build_columns(grid, header_row)
    cfg_colors = cfg.get("colors", {})

    if "date" not in cols:
        guessed = guess_date_column(grid, header_row)
        if guessed:
            cols["date"] = guessed

    # колонки, где цвет означает ответственного, а не статус
    status_skip = columns_by_name(
        grid, header_row, {norm(x) for x in cfg_colors.get("status_exclude", [])}
    )
    red_skip = columns_by_name(
        grid, header_row, {norm(x) for x in cfg_colors.get("red_exclude", [])}
    )

    rows: dict[str, dict] = {}
    for r in range(header_row + 1, grid.max_row + 1):
        def get(key: str) -> str:
            col = cols.get(key)
            return grid.value(r, col) if col else ""

        city = get("city")
        ml = get("ml")
        if not city and not ml:
            continue

        green = yellow = red = 0
        for col in range(1, max(grid.max_col, 1) + 1):
            kind = classify_hex(grid.fill(r, col), cfg_colors)
            if kind == "green":
                if col not in status_skip:
                    green += 1
            elif kind == "yellow":
                if col not in status_skip:
                    yellow += 1
            elif kind == "red":
                if col not in red_skip:
                    red += 1

        plate = get("plate")
        if green == 0 and yellow == 0:
            status = "arrived" if plate else "planned"
        elif green >= yellow:
            status = "loading"
        else:
            status = "departed"

        key = ml or f"{city}|{r}"
        if key in rows:  # один и тот же МЛ в двух строках — разводим по номеру строки
            key = f"{key}#{r}"
        rows[key] = {
            "row": r,
            "city": city,
            "ml": ml,
            "date": get("date"),
            "volume": get("volume"),
            "plate": plate,
            "comment": get("comment"),
            "colors": {"green": green, "yellow": yellow, "red": red},
            "status": status,
            "problem": red > 0,
        }
    return rows


# ================================================================ фильтр


TOKEN_RE = re.compile(r"[0-9a-zа-я]+")


def plate_tokens(value: str) -> set[str]:
    """Разбивает номер на части: '826/064' -> {'826', '064'}.

    Числовые части дополнительно приводятся к форме без ведущих нулей,
    чтобы «058» и «58» считались одним номером.
    """
    tokens = set(TOKEN_RE.findall(str(value).lower().replace("ё", "е")))
    tokens |= {str(int(t)) for t in tokens if t.isdigit()}
    return tokens


def city_matches(rec: dict, cfg: dict) -> bool:
    cities = [norm(c) for c in cfg.get("cities", []) if c]
    return True if not cities else any(c in norm(rec["city"]) for c in cities)


def is_mine(rec: dict, cfg: dict) -> bool:
    """Город и/или номер машины, в зависимости от filter_mode.

    filter_mode = "all" (по умолчанию): нужен и город, и номер из списка.
    filter_mode = "any": достаточно совпадения по одному условию.
    Пустой список означает «не фильтровать по этому признаку».
    """
    cities = [norm(c) for c in cfg.get("cities", []) if c]
    plates = [plate_tokens(p) for p in cfg.get("plates", []) if str(p).strip()]

    if not cities and not plates:
        return True

    city_ok = city_matches(rec, cfg)

    plate_ok = True
    if plates:
        mine = plate_tokens(rec.get("plate", ""))
        plate_ok = any(mine & wanted for wanted in plates)

    if cfg.get("filter_mode", "all") == "any":
        return city_ok or plate_ok
    return city_ok and plate_ok


# ================================================================ события


STATUS_RANK = {"planned": 0, "arrived": 1, "loading": 2, "departed": 3}
STATUS_LABEL = {"arrived": "прибыла", "loading": "начала грузиться", "departed": "выехала"}
STATUS_ICON = {"arrived": "🚛", "loading": "📦", "departed": "✅"}
STATUS_RU = {"planned": "в плане", "arrived": "прибыла", "loading": "грузится", "departed": "выехала"}


def list_current(cfg: dict, local_file: str | None = None) -> None:
    """Печатает строки по городам и отмечает, какие машины попадают под фильтр."""
    rows = load_rows(fetch(cfg, local_file), cfg)
    shown = [r for r in rows.values() if city_matches(r, cfg)]
    shown.sort(key=lambda r: (r.get("date") or "", r["row"]))
    mine_count = sum(1 for r in shown if is_mine(r, cfg))

    cities = ", ".join(cfg.get("cities") or []) or "все"
    plates = cfg.get("plates") or []
    print(f"Всего строк: {len(rows)} | в городе: {len(shown)} | ваших машин: {mine_count}")
    print(f"Города: {cities}")
    print("Машины: " + (", ".join(str(p) for p in plates) if plates else "не заданы — берутся все") + "\n")

    header = (
        f"{'МОЯ':<5}{'СТАТУС':<10}{'ФОРС':<5}{'ДАТА':<18}{'Т/С':<13}{'МЛ':<11}КОММЕНТАРИЙ"
    )
    print(header)
    print("-" * (len(header) + 20))
    for r in shown:
        print(
            f"{'✔' if is_mine(r, cfg) else '':<5}"
            f"{STATUS_RU.get(r['status'], r['status']):<10}"
            f"{'ЕСТЬ' if r['problem'] else '':<5}"
            f"{(r['date'] or '')[:17]:<18}"
            f"{(r['plate'] or '-')[:12]:<13}"
            f"{r['ml']:<11}"
            f"{r['comment'][:34]}"
        )


def describe(rec: dict) -> str:
    parts = [rec["city"] or "—"]
    if rec.get("date"):
        parts.append(rec["date"])
    if rec["plate"]:
        parts.append(f"Т/С {rec['plate']}")
    if rec["ml"]:
        parts.append(f"МЛ {rec['ml']}")
    if rec["volume"]:
        parts.append(f"объём {rec['volume']}")
    return " · ".join(parts)


def detect_events(old: dict, new: dict, cfg: dict) -> list[str]:
    flags = cfg.get("events", {})
    messages: list[str] = []

    for key, rec in new.items():
        if not is_mine(rec, cfg):
            continue
        prev = old.get(key)

        if prev is None:
            if flags.get("new_row"):
                messages.append(f"🆕 Новая машина в плане\n{describe(rec)}")
            # новая строка сразу с красной ячейкой — тоже форс-мажор
            if flags.get("problem") and rec["problem"]:
                note = f"\n💬 {rec['comment']}" if rec["comment"] else ""
                messages.append(f"🛑 ФОРС-МАЖОР\n{describe(rec)}{note}")
            continue

        if flags.get("problem") and rec["problem"] and not prev.get("problem"):
            note = f"\n💬 {rec['comment']}" if rec["comment"] else ""
            messages.append(f"🛑 ФОРС-МАЖОР\n{describe(rec)}{note}")

        rank_old = STATUS_RANK.get(prev.get("status"), 0)
        rank_new = STATUS_RANK.get(rec["status"], 0)
        if rank_new > rank_old:
            steps = [
                s for s in ("arrived", "loading", "departed")
                if rank_new >= STATUS_RANK[s] > rank_old and flags.get(s)
            ]
            if len(steps) == 1:
                messages.append(
                    f"{STATUS_ICON[steps[0]]} Машина {STATUS_LABEL[steps[0]]}\n{describe(rec)}"
                )
            elif steps:
                chain = " → ".join(STATUS_LABEL[s] for s in steps)
                messages.append(f"🚛 Машина: {chain}\n{describe(rec)}")
    return messages


# ================================================================ MAX


def _ssl_context(cfg: dict):
    """Контекст TLS: доверяем стандартным УЦ + сертификатам из папки certs.

    MAX использует сертификат Минцифры, которого нет в стандартном наборе,
    поэтому он лежит рядом с проектом.
    """
    if cfg.get("max", {}).get("insecure_tls"):
        log.warning("Проверка TLS отключена (insecure_tls) — это небезопасно")
        return ssl._create_unverified_context()

    ctx = ssl.create_default_context()
    for path in sorted((BASE / "certs").glob("*.crt")):
        try:
            ctx.load_verify_locations(cafile=str(path))
        except Exception as exc:  # noqa: BLE001
            log.warning("Сертификат %s не загружен: %s", path.name, exc)
    return ctx


SUBSCRIBERS_PATH = BASE / "subscribers.json"


def load_subscribers() -> dict:
    """Список тех, кто написал боту и хочет получать уведомления."""
    if SUBSCRIBERS_PATH.exists():
        try:
            data = json.loads(SUBSCRIBERS_PATH.read_text(encoding="utf-8"))
            data.setdefault("offset", 0)
            data.setdefault("chats", {})
            return data
        except Exception:
            log.warning("subscribers.json повреждён — создаю заново")
    return {"offset": 0, "chats": {}}


def save_subscribers(data: dict) -> None:
    SUBSCRIBERS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def tg_call(token: str, method: str, params: dict | None = None) -> dict:
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=70) as resp:
        return json.loads(resp.read().decode("utf-8"))


def tg_send_one(token: str, chat_id: str, text: str) -> str:
    """Отправляет в один чат. Возвращает ok / blocked / error."""
    payload = json.dumps(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        ensure_ascii=False,
    ).encode("utf-8")

    for attempt in (1, 2, 3):
        try:
            req = urllib.request.Request(
                f"{TELEGRAM_API}/bot{token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                if json.loads(resp.read().decode("utf-8")).get("ok"):
                    return "ok"
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            log.error("Telegram ответил %s для %s: %s", exc.code, chat_id, body[:200])
            if exc.code == 403:
                return "blocked"
            if exc.code in (400, 401):
                return "error"
        except Exception as exc:  # noqa: BLE001
            log.error("Telegram: сетевая ошибка (%s/3) для %s: %s", attempt, chat_id, exc)
        time.sleep(3 * attempt)
    return "error"


def send_telegram(text: str, cfg: dict) -> None:
    tg = cfg.get("telegram", {})
    token = str(tg.get("token", "")).strip()
    if not token:
        return

    targets: list[str] = []
    main = str(tg.get("chat_id", "")).strip()
    if main:
        targets.append(main)

    if tg.get("share", True):
        group = main if main.startswith("-") else ""
        for chat_id in load_subscribers().get("chats", {}):
            if chat_id not in targets:
                # кто и так сидит в группе — не должен получать вторую копию
                if group and not chat_id.startswith("-") and is_group_member(token, group, chat_id):
                    continue
                targets.append(chat_id)

    blocked: list[str] = []
    for chat_id in targets:
        result = tg_send_one(token, chat_id, text)
        if result == "ok":
            log.info("Telegram: отправлено (%s)", chat_id)
        elif result == "blocked":
            blocked.append(chat_id)

    if blocked:
        data = load_subscribers()
        for chat_id in blocked:
            data.get("chats", {}).pop(chat_id, None)
            log.info("Отписан (заблокировал бота): %s", chat_id)
        save_subscribers(data)


_member_cache: dict[tuple[str, str], tuple[bool, float]] = {}


def is_group_member(token: str, group_id: str, user_id: str) -> bool:
    """Проверяет, состоит ли человек в группе (с кэшем на час)."""
    key = (group_id, user_id)
    cached = _member_cache.get(key)
    if cached and time.time() - cached[1] < 3600:
        return cached[0]

    member = False
    try:
        response = tg_call(token, "getChatMember", {"chat_id": group_id, "user_id": user_id})
        status = str(response.get("result", {}).get("status", ""))
        member = status in ("creator", "administrator", "member", "restricted")
    except Exception:  # noqa: BLE001
        member = False

    _member_cache[key] = (member, time.time())
    return member


def subscribe_listener(get_cfg) -> None:
    """Слушает бота: кто написал — подписывается, кто /stop — отписывается."""
def absorb_updates(data: dict, response: dict) -> bool:
    """Разбирает ответ getUpdates: подписки и отписки. Возвращает «были изменения»."""
    chats = data.get("chats", {})
    changed = False

    for update in response.get("result", []):
        data["offset"] = int(update.get("update_id", 0)) + 1
        changed = True

        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        key = str(chat_id)
        text = (message.get("text") or "").strip().lower()
        name = (
            chat.get("title")
            or chat.get("username")
            or chat.get("first_name")
            or "без имени"
        )

        if text in ("/stop", "/off", "/стоп", "стоп", "отписаться"):
            if chats.pop(key, None) is not None:
                log.info("Отписан: %s (%s)", name, key)
        elif key not in chats:
            chats[key] = {"name": name, "added": time.strftime("%d.%m.%Y %H:%M")}
            log.info("Новый получатель: %s (%s)", name, key)

    data["chats"] = chats
    return changed


def process_updates_once(cfg: dict) -> None:
    """Один проход по сообщениям боту — используется при запуске в облаке."""
    token = str(cfg.get("telegram", {}).get("token", "")).strip()
    if not token:
        return

    data = load_subscribers()
    params: dict = {"timeout": 0}
    offset = int(data.get("offset") or 0)
    if offset:
        params["offset"] = offset

    try:
        response = tg_call(token, "getUpdates", params)
    except Exception as exc:  # noqa: BLE001
        log.debug("getUpdates: %s", exc)
        return

    if absorb_updates(data, response):
        save_subscribers(data)


def subscribe_listener(get_cfg) -> None:
    """Слушает бота: кто написал — подписывается, кто /stop — отписывается."""
    log.info("Слушаю личные сообщения боту (подписка на уведомления)")
    while True:
        try:
            cfg = get_cfg()
            token = str(cfg.get("telegram", {}).get("token", "")).strip()
            if not token:
                time.sleep(15)
                continue

            data = load_subscribers()
            params: dict = {"timeout": 25}
            offset = int(data.get("offset") or 0)
            if offset:
                params["offset"] = offset

            response = tg_call(token, "getUpdates", params)
            if absorb_updates(data, response):
                save_subscribers(data)

        except Exception as exc:  # noqa: BLE001
            log.debug("Слушатель подписок: %s", exc)
            time.sleep(5)


def notify(text: str, cfg: dict) -> None:
    """Рассылает сообщение во все настроенные каналы (MAX и/или Telegram)."""
    delivered = False

    for channel, sender in (("max", send_max), ("telegram", send_telegram)):
        block = cfg.get(channel, {})
        if str(block.get("token", "")).strip() and str(block.get("chat_id", "")).strip():
            sender(text, cfg)
            delivered = True

    if not delivered:
        log.warning("Каналы не настроены — сообщение только в лог:\n%s", text)


def send_max(text: str, cfg: dict) -> None:
    token = str(cfg.get("max", {}).get("token", "")).strip()
    chat_id = str(cfg.get("max", {}).get("chat_id", "")).strip()
    if not token or not chat_id:
        return

    url = f"{MAX_API}/messages?chat_id={urllib.parse.quote(chat_id)}"
    payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")

    for attempt in (1, 2, 3):
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Authorization": token,
                    "Content-Type": "application/json; charset=utf-8",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30, context=_ssl_context(cfg)) as resp:
                log.info("MAX: отправлено (%s)", resp.status)
                return
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            log.error("MAX ответил %s: %s", exc.code, body)
            if exc.code in (400, 401, 403):
                return
        except Exception as exc:  # noqa: BLE001
            log.error("MAX: сетевая ошибка (%s/3): %s", attempt, exc)
        time.sleep(5 * attempt)


# ================================================================ калибровка


def calibrate(data: bytes, cfg: dict) -> None:
    grids = xlsx_to_grids(data)
    grid, header_row = pick_grid(grids)
    cols = build_columns(grid, header_row)
    headers = {
        col: (grid.value(header_row, col) or f"col{col}")
        for col in range(1, grid.max_col + 1)
    }

    buckets: dict[tuple, list] = {}
    for r in range(header_row + 1, grid.max_row + 1):
        for col in range(1, grid.max_col + 1):
            hexcol = grid.fill(r, col)
            if not hexcol:
                continue
            kind = classify_hex(hexcol, cfg.get("colors", {})) or "?"
            key = (headers.get(col, f"col{col}"), hexcol, kind)
            bucket = buckets.setdefault(key, [])
            if len(bucket) < 3:
                bucket.append(grid.value(r, col)[:40])

    lines = [
        f"Строка заголовков: {header_row}",
        f"Колонки найдены: {json.dumps(cols, ensure_ascii=False)}",
        "",
        f"{'КОЛОНКА':<26}{'ЦВЕТ':<10}{'КЛАСС':<9}{'ПРИМЕРЫ'}",
        "-" * 100,
    ]
    for (col_name, hexcol, kind), samples in sorted(
        buckets.items(), key=lambda kv: (kv[0][0], -len(kv[1]))
    ):
        joined = " | ".join(s for s in samples if s)
        lines.append(f"{col_name[:25]:<26}{'#' + hexcol:<10}{kind:<9}{joined}")

    report = "\n".join(lines)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nОтчёт сохранён: {REPORT_PATH}")


# ================================================================ состояние


def _merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    return [part.strip() for part in raw.split(",") if part.strip()]


def load_config() -> dict:
    """Настройки: по умолчанию → config.json → config.local.json → переменные среды.

    Благодаря умолчаниям в облаке config.json вообще не нужен.
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)

    if CONFIG_PATH.exists():
        try:
            _merge(cfg, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            log.warning("config.json не прочитан: %s", exc)

    # Секреты держим отдельно: этот файл не попадает в git
    local_path = BASE / "config.local.json"
    if local_path.exists():
        try:
            _merge(cfg, json.loads(local_path.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            log.warning("config.local.json не прочитан: %s", exc)

    # В облаке всё приходит из секретов
    token = os.environ.get("TGW_TELEGRAM_TOKEN", "").strip()
    chat = os.environ.get("TGW_TELEGRAM_CHAT_ID", "").strip()
    sheet = os.environ.get("TGW_SHEET_URL", "").strip()
    if token:
        cfg["telegram"]["token"] = token
    if chat:
        cfg["telegram"]["chat_id"] = chat
    if sheet:
        cfg["sheet_url"] = sheet

    plates = _env_list("TGW_PLATES")
    if plates:
        cfg["plates"] = plates
    cities = _env_list("TGW_CITIES")
    if cities:
        cfg["cities"] = cities

    return cfg


def save_config(cfg: dict) -> None:
    """Пишет конфиг, вырезая токены, чтобы они не попали в репозиторий."""
    clean = copy.deepcopy(cfg)
    for channel in ("telegram", "max"):
        block = clean.get(channel)
        if isinstance(block, dict):
            block["token"] = ""
    CONFIG_PATH.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def show_plates(cfg: dict) -> None:
    plates = [p for p in (cfg.get("plates") or []) if str(p).strip()]
    cities = [c for c in (cfg.get("cities") or []) if str(c).strip()]
    print("Города:", ", ".join(cities) if cities else "все")
    if not plates:
        print("Машины: список пуст — приходят уведомления по всем машинам в этих городах")
        return
    print(f"Машины под наблюдением ({len(plates)}):")
    for plate in plates:
        print("  •", plate)


INVISIBLE = "\ufeff\u200b\u200e\u200f\xa0"


def clean_plate(text: str) -> str:
    """Убирает невидимые символы и лишние пробелы из номера машины."""
    for ch in INVISIBLE:
        text = text.replace(ch, " ")
    return " ".join(text.split()).strip().upper()


def change_plates(cfg: dict, add: list[str] | None, remove: list[str] | None) -> None:
    plates = [str(p) for p in (cfg.get("plates") or []) if str(p).strip()]

    for raw in add or []:
        clean = clean_plate(raw)
        if not clean:
            continue
        if clean in [p.upper() for p in plates]:
            print(f"  уже в списке: {clean}")
        else:
            plates.append(clean)
            print(f"  добавлено: {clean}")

    for raw in remove or []:
        clean = clean_plate(raw)
        before = len(plates)
        plates = [p for p in plates if p.upper() != clean]
        print(f"  удалено: {clean}" if len(plates) < before else f"  не найдено: {clean}")

    cfg["plates"] = plates
    save_config(cfg)
    print()
    show_plates(cfg)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            log.warning("state.json повреждён — начинаю заново")
    return {}


def save_state(rows: dict) -> None:
    slim = {k: {"status": v["status"], "problem": v["problem"]} for k, v in rows.items()}
    STATE_PATH.write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")


# ================================================================ цикл
def download_sheet(url: str) -> bytes:
    """Скачивает Google Таблицу в формате xlsx."""
    import re
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if not match:
        raise ValueError(f"Не удалось извлечь ID таблицы из URL: {url}")
    
    sheet_id = match.group(1)
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    
    req = urllib.request.Request(export_url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    
    # SSL-контекст: отключаем проверку hostname для обхода ошибок типа EOF violation
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    with urlopen(req, timeout=60, context=ctx) as resp:
        return resp.read()
def download_sheet(url: str) -> bytes:
    """Скачивает Google Таблицу в формате xlsx."""
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if not match:
        raise ValueError(f"Не удалось извлечь ID таблицы из URL: {url}")
    
    sheet_id = match.group(1)
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    
    req = urllib.request.Request(export_url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        return resp.read()

def fetch(cfg: dict, local_file: str | None) -> bytes:
    if local_file:
        return Path(local_file).read_bytes()

    url = str(cfg.get("sheet_url", "")).strip()
    if not url:
        raise RuntimeError(
            "Не задана ссылка на таблицу. В облаке добавьте секрет TGW_SHEET_URL, "
            "локально — поле sheet_url в config.json."
        )
    return download_sheet(url)


def poll_once(cfg: dict, local_file: str | None = None) -> dict:
    new_rows = load_rows(fetch(cfg, local_file), cfg)

    state = load_state()
    old_rows = {
        k: {"status": v.get("status", "planned"), "problem": v.get("problem", False)}
        for k, v in state.items()
    }

    if not state:
        log.info("Первый запуск: запоминаю текущее состояние (%d строк)", len(new_rows))
    else:
        messages = detect_events(old_rows, new_rows, cfg)
        mine = sum(1 for r in new_rows.values() if is_mine(r, cfg))
        log.info("Строк: %d, моих: %d, событий: %d", len(new_rows), mine, len(messages))
        for msg in messages:
            log.info("СОБЫТИЕ: %s", msg.replace("\n", " / "))
            notify(msg, cfg)

    save_state(new_rows)
    return new_rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Слежение за таблицей отгрузок")
    ap.add_argument("--calibrate", action="store_true", help="показать цвета таблицы")
    ap.add_argument("--list", action="store_true", help="показать мои строки и статусы")
    ap.add_argument("--once", action="store_true", help="один цикл и выход")
    ap.add_argument("--test-notify", action="store_true", help="тест сообщения в MAX")
    ap.add_argument("--file", help="локальный xlsx вместо скачивания")
    ap.add_argument("--interval", type=int, help="период опроса, сек")
    ap.add_argument("--reset", action="store_true", help="сбросить state.json")
    ap.add_argument("--plates", action="store_true", help="показать список машин")
    ap.add_argument("--subscribers", action="store_true", help="показать получателей в Telegram")
    ap.add_argument("--unsubscribe", metavar="CHAT_ID", help="убрать получателя по chat_id")
    ap.add_argument("--add-plate", nargs="+", metavar="НОМЕР", help="добавить машины")
    ap.add_argument("--remove-plate", nargs="+", metavar="НОМЕР", help="убрать машины")
    args = ap.parse_args()

    # консоль Windows по умолчанию cp1251 — иначе кириллица и эмодзи падают
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # если процесс убьют или он упадёт на уровне C — traceback попадёт в crash.log
    try:
        import faulthandler

        _crash = open(BASE / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(_crash)
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%d.%m %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
        ],
    )

    cfg = load_config()

    if args.reset and STATE_PATH.exists():
        STATE_PATH.unlink()
        log.info("state.json удалён")

    if args.plates:
        show_plates(cfg)
        return 0

    if args.add_plate or args.remove_plate:
        change_plates(cfg, args.add_plate, args.remove_plate)
        return 0

    if args.subscribers:
        data = load_subscribers()
        chats = data.get("chats", {})
        if not chats:
            print("Пока никто не подписан. Отправьте людям ссылку на бота —")
            print("как только они напишут ему /start, они появятся здесь.")
        else:
            print(f"Получателей: {len(chats)}\n")
            for chat_id, info in chats.items():
                print(f"  {chat_id:<16} {info.get('name', '')}  (с {info.get('added', '?')})")
        return 0

    if args.unsubscribe:
        data = load_subscribers()
        chats = data.get("chats", {})
        if chats.pop(str(args.unsubscribe), None) is not None:
            save_subscribers(data)
            print("Получатель удалён:", args.unsubscribe)
        else:
            print("Такого chat_id нет в списке:", args.unsubscribe)
        return 0

    if args.test_notify:
        notify("🔔 truck-watch: тестовое сообщение. Связь есть.", cfg)
        return 0

    try:
        if args.calibrate:
            calibrate(fetch(cfg, args.file), cfg)
            return 0

        if args.list:
            list_current(cfg, args.file)
            return 0

        if args.once:
            process_updates_once(cfg)
            poll_once(cfg, args.file)
            return 0

        # защита от второго экземпляра: два наблюдателя = дубли и конфликт в Telegram
        if not acquire_single_instance():
            log.warning(
                "Уже работает другой экземпляр слежения — этот запуск не нужен."
            )
            return 0

        interval = args.interval or int(cfg.get("poll_seconds", 60))
        log.info("Старт. Опрос каждые %s сек. Ctrl+C — остановить.", interval)
        log.info("Процесс %s, Python %s", os.getpid(), sys.version.split()[0])
        log.info("Правки config.json подхватываются без перезапуска.")

        if str(cfg.get("telegram", {}).get("token", "")).strip():
            threading.Thread(
                target=subscribe_listener, args=(load_config,), daemon=True
            ).start()

        cycle = 0
        while True:
            cycle += 1
            try:
                # перечитываем конфиг: добавленные машины работают сразу
                cfg = load_config()
                log.debug("цикл %s", cycle)
                poll_once(cfg, args.file)
                interval = args.interval or int(cfg.get("poll_seconds", 60))
            except Exception:
                log.error("Ошибка цикла:\n%s", traceback.format_exc())
            time.sleep(interval)

    except KeyboardInterrupt:
        log.info("Остановлено пользователем")
        return 0
    except BaseException as exc:  # noqa: BLE001
        log.error("Фатальная ошибка: %s\n%s", exc, traceback.format_exc())
        raise


if __name__ == "__main__":
    sys.exit(main())
