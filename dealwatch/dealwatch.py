# -*- coding: utf-8 -*-
"""
dealwatch.py — поиск выгодных предложений на маркетплейсах и Авито.

Что делает:
  1. Открывает поиск на площадках через реальный Chrome (обход антибота).
  2. Собирает предложения, считает медиану по всем площадкам.
  3. Присылает в Telegram/MAX те, что дешевле медианы на N процентов.
  4. Запоминает отправленное, чтобы не спамить одним и тем же.

Запуск:
  python dealwatch.py --check          проверить, какие площадки сейчас отвечают
  python dealwatch.py --once           один проход и выход
  python dealwatch.py                  цикл (каждые poll_seconds)
  python dealwatch.py --respond URL    открыть объявление Авито и подготовить
                                       сообщение продавцу (без отправки)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mailbox as mailbox_mod  # noqa: E402
import notify  # noqa: E402
from browser import BrowserError, Chrome  # noqa: E402
from sources import SourceError, search  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
LOG_PATH = os.path.join(BASE_DIR, "dealwatch.log")

log = logging.getLogger("dealwatch")


# ---------------------------------------------------------------- утилиты

def setup_logging(quiet: bool = False) -> None:
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if not quiet:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)


def money(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{int(round(value)):,}".replace(",", " ") + " ₽"


def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        log.error("Файл %s повреждён: %s", path, e)
        return default


def save_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- фильтры

STOP_TOKENS = {"гб", "gb", "тб", "мм", "шт", "для", "или", "все", "the", "and"}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zа-яё0-9]+", (text or "").lower())
            if len(t) >= 2 and t not in STOP_TOKENS]


def matches(offer: dict, watch: dict, rules: dict | None = None) -> bool:
    """Отсекает чехлы, аксессуары, другие модели и мусор."""
    rules = rules or {}
    title = offer.get("title", "")
    low = title.lower()
    price = offer.get("price") or 0

    for word in watch.get("must_exclude", []):
        if word.lower() in low:
            return False

    include = watch.get("must_include", [])
    if include and not all(w.lower() in low for w in include):
        return False

    # Авто-сверка с запросом: цифры (модель, объём) должны совпасть обязательно,
    # слова — в большинстве. Без этого в медиану лезут Pro Max и аксессуары.
    if not include and rules.get("auto_match", True):
        query = watch.get("query", "")
        wanted_nums = [t for t in _tokens(query) if t.isdigit()]
        if wanted_nums and not all(n in low for n in wanted_nums):
            return False
        wanted_words = [t for t in _tokens(query) if not t.isdigit()]
        if wanted_words:
            hit = sum(1 for w in wanted_words if w in low)
            if hit / len(wanted_words) < float(rules.get("match_ratio", 0.6)):
                return False

    if watch.get("min_price") and price < watch["min_price"]:
        return False
    if watch.get("max_price") and price > watch["max_price"]:
        return False
    return price > 0


def analyze(watch: dict, offers: list[dict], rules: dict) -> dict:
    """
    Считает медиану и выделяет выгодные предложения.

    median_mode = "platform" (по умолчанию) — медиана считается внутри каждой
    площадки. Это принципиально: б/у Авито и новый товар на WB нельзя сравнивать
    напрямую, иначе все объявления Авито выглядят «сделками».
    median_mode = "global" — медиана по всем площадкам сразу (для арбитража).
    """
    threshold = float(watch.get("threshold_percent", rules.get("threshold_percent", 25)))
    min_offers = int(watch.get("min_offers", rules.get("min_offers", 3)))
    mode = str(watch.get("median_mode", rules.get("median_mode", "platform")))

    buckets: dict[str, list[dict]] = {}
    for o in offers:
        buckets.setdefault(o["platform"], []).append(o)

    per_platform = {
        p: statistics.median([x["price"] for x in items])
        for p, items in buckets.items() if len(items) >= min_offers
    }
    all_prices = [o["price"] for o in offers]
    global_median = statistics.median(all_prices) if len(all_prices) >= min_offers else None

    result = {
        "watch": watch,
        "offers": offers,
        "median": global_median,
        "medians": per_platform,
        "threshold": threshold,
        "mode": mode,
        "deals": [],
        "note": "",
    }
    if not per_platform and global_median is None:
        result["note"] = f"мало предложений (< {min_offers}) — медиана не считается"
        return result

    reference = {p: (global_median if mode == "global" else None) for p in buckets}
    reference.update({p: m for p, m in per_platform.items() if mode != "global"})

    for platform, items in buckets.items():
        base = reference.get(platform)
        if not base:
            continue
        cutoff = base * (1 - threshold / 100.0)
        floor = base * 0.25  # защита от мусора: «чехол за 300 ₽» не должен быть «сделкой»
        for o in items:
            if o["price"] > cutoff or o["price"] < floor:
                continue
            d = dict(o)
            d["discount_percent"] = round((1 - o["price"] / base) * 100, 1)
            d["base"] = base
            result["deals"].append(d)

    result["deals"].sort(key=lambda x: -x["discount_percent"])
    return result


# ---------------------------------------------------------------- state

def should_alert(state: dict, watch_id: str, offer: dict) -> bool:
    key = f"{watch_id}|{offer['platform']}|{offer['id']}"
    prev = state.get("seen", {}).get(key)
    now = time.time()
    if not prev:
        return True
    if offer["price"] <= prev.get("price", 0) * 0.95:
        return True
    return now - prev.get("ts", 0) > 3 * 86400


def mark_seen(state: dict, watch_id: str, offer: dict) -> None:
    key = f"{watch_id}|{offer['platform']}|{offer['id']}"
    state.setdefault("seen", {})[key] = {"price": offer["price"], "ts": time.time()}
    history = state.setdefault("prices", {}).setdefault(key, [])
    history.append({"t": int(time.time()), "p": offer["price"]})
    del history[:-60]


def prune_state(state: dict, days: int = 30) -> None:
    cutoff = time.time() - days * 86400
    seen = state.get("seen", {})
    for key in [k for k, v in seen.items() if v.get("ts", 0) < cutoff]:
        seen.pop(key, None)


# ---------------------------------------------------------------- вывод

def format_alert(watch: dict, offer: dict) -> str:
    platform_names = {"wb": "Wildberries", "ozon": "Ozon", "ym": "Яндекс Маркет",
                      "avito": "Авито", "mm": "Мегамаркет", "mail": "Авито (почта)"}
    name = platform_names.get(offer["platform"], offer["platform"])
    title = offer.get("title") or watch.get("name") or watch.get("query", "")
    base = offer.get("base")
    lines = [
        f"🔥 <b>{watch.get('name') or watch.get('query')}</b> — {name}",
        f"<b>{money(offer['price'])}</b>  (медиана площадки {money(base)}, "
        f"−{offer.get('discount_percent', 0)}%)",
        f"{title[:140]}",
        offer.get("url", ""),
    ]
    if watch.get("note"):
        lines.append(f"<i>{watch['note']}</i>")
    return "\n".join(x for x in lines if x)


def print_report(results: list[dict]) -> None:
    for r in results:
        watch = r["watch"]
        print(f"\n=== {watch.get('name') or watch.get('query')} ===")
        if r["medians"]:
            parts = ", ".join(f"{p}: {money(m)}" for p, m in r["medians"].items())
            print(f"медианы по площадкам: {parts}")
        if r["median"]:
            print(f"общая медиана: {money(r['median'])}   порог: −{r['threshold']:.0f}%   "
                  f"предложений: {len(r['offers'])}")
        else:
            print(f"медиана: —   предложений: {len(r['offers'])}   {r['note']}")
        if r["deals"]:
            for d in r["deals"][:10]:
                print(f"  🔥 {money(d['price']):>12}  −{d['discount_percent']:>4.0f}%  "
                      f"{d['platform']:<6} {d.get('title', '')[:60]}")
                print(f"     {d.get('url', '')}")
        else:
            print("  выгодных предложений нет")


# ---------------------------------------------------------------- сбор

def collect(chrome: Chrome, page, config: dict, watch: dict, platforms: list[dict]) -> list[dict]:
    """Собирает предложения по одному отслеживаемому товару."""
    offers: list[dict] = []
    limit = int(config.get("rules", {}).get("max_per_platform", 40))

    for platform in platforms:
        key = platform.get("key", "")
        query = watch.get("queries", {}).get(key) or watch.get("query")
        if not query:
            continue
        try:
            found, _ = search(page, platform, query, limit=limit)
        except (SourceError, BrowserError) as e:
            log.warning("  %s — %s", platform.get("name", key), e)
            continue
        except Exception as e:  # noqa: BLE001
            log.warning("  %s — неожиданная ошибка: %s", platform.get("name", key), e)
            continue

        kept = 0
        for o in found:
            o["platform"] = key
            if matches(o, watch, config.get("rules", {})):
                offers.append(o)
                kept += 1
        log.info("  %-14s найдено %2d, после фильтров %2d", platform.get("name", key), len(found), kept)

    mail_offers = mailbox_mod.collect(config, watch)
    if mail_offers:
        for o in mail_offers:
            o["platform"] = "mail"
            if matches(o, watch, config.get("rules", {})):
                offers.append(o)
        log.info("  %-14s найдено %2d", "Авито (почта)", len(mail_offers))

    return offers


def run_once(chrome: Chrome, config: dict, state: dict, watch_ids: list[str] | None = None,
             notify_enabled: bool = True) -> list[dict]:
    rules = config.get("rules", {})
    platforms = [p for p in config.get("platforms", []) if p.get("enabled", True)]
    results = []
    page = chrome.new_page()

    for watch in config.get("watches", []):
        wid = watch.get("id") or watch.get("query")
        if watch_ids and wid not in watch_ids:
            continue
        log.info("Проверяю: %s", watch.get("name") or watch.get("query"))
        wanted = set(watch.get("platforms") or [p.get("key") for p in platforms])
        active = [p for p in platforms if p.get("key") in wanted]

        offers = collect(chrome, page, config, watch, active)
        result = analyze(watch, offers, rules)
        results.append(result)

        if not notify_enabled:
            continue

        limit = int(watch.get("max_alerts", rules.get("max_alerts", 5)))
        sent = 0
        for deal in result["deals"]:
            if sent >= limit:
                break
            if should_alert(state, wid, deal):
                if notify.send(format_alert(watch, deal), config):
                    log.info("  🔥 %s %s (−%.0f%%)", deal["platform"], money(deal["price"]),
                             deal["discount_percent"])
                    sent += 1
                mark_seen(state, wid, deal)

    save_json(STATE_PATH, state)
    return results


# ---------------------------------------------------------------- проверка

def check(config: dict) -> None:
    platforms = [p for p in config.get("platforms", []) if p.get("enabled", True)]
    watch = (config.get("watches") or [{"query": "iphone 15"}])[0]
    query = watch.get("query", "iphone 15")
    chrome = Chrome(port=config.get("browser", {}).get("port", 9222),
                    profile_dir=os.path.join(BASE_DIR, config.get("browser", {}).get("profile_dir", ".chrome-profile")),
                    headless=config.get("browser", {}).get("headless", False))
    chrome.start()
    page = chrome.new_page()
    try:
        print(f"\nПроверка площадок по запросу «{query}»\n")
        for p in platforms:
            name = p.get("name", p.get("key"))
            try:
                found, diag = search(page, p, query, limit=10)
                prices = sorted(o["price"] for o in found)
                med = statistics.median(prices) if prices else 0
                print(f"  {name:<16} OK    {len(found):>2} предложений, медиана {money(med)}")
                if found:
                    print(f"  {'':<16}       пример: {found[0]['title'][:60]} — {money(found[0]['price'])}")
            except Exception as e:  # noqa: BLE001
                print(f"  {name:<16} СБОЙ  {e}")
        print("\nЕсли площадка в СБОЕ — откройте её в запущенном Chrome вручную, "
              "пройдите проверку и запустите --check снова.\n")
    finally:
        chrome.stop()


# ---------------------------------------------------------------- отклик на Авито

def respond(config: dict, url: str, text: str) -> None:
    """
    Открывает объявление и печатает текст в поле сообщения, НО НЕ ОТПРАВЛЯЕТ.
    Автоматическая отправка нарушает правила Авито и приводит к блокировке
    аккаунта — поэтому последний шаг остаётся за человеком.
    """
    chrome = Chrome(port=config.get("browser", {}).get("port", 9222),
                    profile_dir=os.path.join(BASE_DIR, config.get("browser", {}).get("profile_dir", ".chrome-profile")),
                    headless=False)
    chrome.start()
    page = chrome.new_page()
    page.goto(url, settle=5)
    js = """
    (function (text) {
      var btns = [...document.querySelectorAll('button, a')];
      var b = btns.find(function (x) { return /написать/i.test(x.textContent || ''); });
      if (b) { b.click(); return 'clicked'; }
      return 'no-button';
    })(__TEXT__)
    """.replace("__TEXT__", json.dumps(text, ensure_ascii=False))
    print("  открываю чат:", page.evaluate(js))
    time.sleep(3)
    fill = """
    (function (text) {
      var ta = document.querySelector('textarea');
      if (!ta) return 'no-textarea';
      var setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
      setter.call(ta, text);
      ta.dispatchEvent(new Event('input', {bubbles: true}));
      ta.focus();
      return 'filled';
    })(__TEXT__)
    """.replace("__TEXT__", json.dumps(text, ensure_ascii=False))
    print("  текст в поле:", page.evaluate(fill))
    print("\n  Осталось нажать «Отправить» в открытом окне Chrome.")
    print("  Браузер оставлен открытым — закройте его вручную, когда закончите.\n")
    # не закрываем Chrome: пользователь должен увидеть и дослать сообщение


# ---------------------------------------------------------------- main

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Мониторинг выгодных предложений")
    parser.add_argument("--check", action="store_true", help="диагностика площадок")
    parser.add_argument("--once", action="store_true", help="один проход и выход")
    parser.add_argument("--daemon", action="store_true", help="цикл (по умолчанию)")
    parser.add_argument("--watch", help="id одного отслеживаемого товара")
    parser.add_argument("--test-notify", action="store_true", help="проверить Telegram/MAX")
    parser.add_argument("--respond", metavar="URL", help="подготовить сообщение продавцу на Авито")
    parser.add_argument("--text", default="", help="текст сообщения для --respond")
    parser.add_argument("--config", default=CONFIG_PATH, help="путь к config.json")
    args = parser.parse_args()

    setup_logging(quiet=args.check)
    config = load_json(args.config, {})
    if not config:
        print(f"Не найден или пуст {args.config}")
        return 1

    if args.test_notify:
        ok = notify.send("<b>dealwatch</b>: проверка связи ✅", config)
        print("Отправлено" if ok else "Каналы не настроены (проверьте notify в config.json)")
        return 0 if ok else 1

    if args.check:
        check(config)
        return 0

    if args.respond:
        text = args.text or config.get("respond_template",
                                       "Здравствуйте! Готов купить сейчас, оплата сразу. Актуально?")
        respond(config, args.respond, text)
        return 0

    state = load_json(STATE_PATH, {})
    prune_state(state)

    browser_cfg = config.get("browser", {})
    chrome = Chrome(
        port=browser_cfg.get("port", 9222),
        profile_dir=os.path.join(BASE_DIR, browser_cfg.get("profile_dir", ".chrome-profile")),
        headless=browser_cfg.get("headless", False),
    )
    chrome.start()
    try:
        if args.once:
            results = run_once(chrome, config, state, [args.watch] if args.watch else None)
            print_report(results)
            return 0

        poll = int(config.get("poll_seconds", 900))
        log.info("Старт. Опрос каждые %d с. Ctrl+C — остановить.", poll)
        while True:
            started = datetime.now()
            try:
                results = run_once(chrome, config, state, [args.watch] if args.watch else None)
                deals = sum(len(r["deals"]) for r in results)
                log.info("Проход завершён за %.0f с, выгодных: %d",
                         (datetime.now() - started).total_seconds(), deals)
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("Ошибка прохода: %s", e)
            time.sleep(poll)
    except KeyboardInterrupt:
        log.info("Остановлено пользователем")
        return 0
    finally:
        chrome.stop()


if __name__ == "__main__":
    sys.exit(main())
