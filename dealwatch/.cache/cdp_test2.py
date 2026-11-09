# -*- coding: utf-8 -*-
"""Проверка №2: видимый Chrome, dedicated-профиль, длинные ожидания."""
import json, os, sys, time, io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_test import CHROME, UA, http_get, Page  # noqa: E402
import subprocess

PORT = 9334
PROFILE = os.path.abspath("dealwatch/.cache/chrome-profile2")
OUT = io.open("dealwatch/.cache/probe2.txt", "w", encoding="utf-8")


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    OUT.write(line + "\n")
    OUT.flush()


def main():
    os.makedirs(PROFILE, exist_ok=True)
    proc = subprocess.Popen([
        CHROME, "--remote-debugging-port=%d" % PORT,
        "--user-data-dir=%s" % PROFILE, "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-crash-reporter", "--disable-gpu",
        "--window-size=1400,900", "--lang=ru-RU",
        "--user-agent=" + UA, "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    globals()["PORT"] = PORT
    import cdp_test
    cdp_test.PORT = PORT
    try:
        for _ in range(60):
            try:
                http_get("/json/version")
                break
            except Exception:
                time.sleep(0.5)
        log("Chrome:", http_get("/json/version")["Browser"])
        target = http_get("/json/new?about:blank", method="PUT")
        page = Page(target["webSocketDebuggerUrl"])

        tests = [
            ("AVITO", "https://www.avito.ru/rossiya/telefony?q=iphone+15",
             "JSON.stringify({title:document.title,items:document.querySelectorAll('[data-marker=\"item\"]').length,"
             "prices:[...document.querySelectorAll('[itemprop=\"price\"]')].slice(0,5).map(e=>e.getAttribute('content')),"
             "url:location.href,bodyLen:document.body.innerText.length})"),
            ("WB", "https://www.wildberries.ru/catalog/0/search.aspx?search=iphone%2015",
             "JSON.stringify({title:document.title,cards:document.querySelectorAll('article.product-card').length,"
             "url:location.href,bodyLen:document.body.innerText.length})"),
            ("OZON", "https://www.ozon.ru/search/?text=iphone+15",
             "JSON.stringify({title:document.title,url:location.href,body:document.body.innerText.slice(0,160)})"),
        ]
        for name, url, expr in tests:
            try:
                page.goto(url, wait=14)
                log(name, "->", page.evaluate(expr))
            except Exception as e:
                log(name, "-> ERR", str(e)[:150])
        OUT.write("\nAVITO_BODY:\n" + (page.evaluate("document.body.innerText") or "")[:1500])
    finally:
        proc.terminate()
        OUT.close()


if __name__ == "__main__":
    main()
