# -*- coding: utf-8 -*-
import os, sys, time, json, subprocess, io
sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("dealwatch"))
import browser as B

OUT = io.open("dealwatch/.cache/probe3.txt", "w", encoding="utf-8")
def log(*a):
    line = " ".join(str(x) for x in a); print(line); OUT.write(line + "\n"); OUT.flush()

ch = B.Chrome(port=9223, profile_dir="dealwatch/.cache/probe-profile", headless=False, verbose=False)
ch.start()
page = ch.new_page()
try:
    tests = [
        ("A rossiya?q", "https://www.avito.ru/rossiya?q=iphone+15"),
        ("B telefony",  "https://www.avito.ru/rossiya/telefony?q=iphone+15"),
        ("C all",       "https://www.avito.ru/all/telefony/mobilnye_telefony?q=iphone+15"),
    ]
    for name, url in tests:
        page.goto(url, settle=8)
        info = page.evaluate("""JSON.stringify({title:document.title,url:location.href,
          items:document.querySelectorAll('[data-marker="item"]').length,
          links:document.querySelectorAll('a[data-marker="item-title"]').length,
          body:document.body.innerText.length})""")
        log(name, "->", info)
        if name.startswith("A"):
            log("  body:", page.evaluate("document.body.innerText.slice(0,300)").replace("\n", " | "))

    # Яндекс: две попытки
    for i in (1, 2):
        page.goto("https://market.yandex.ru/search?text=iphone+15+128", settle=6 + i * 2)
        info = page.evaluate("""JSON.stringify({title:document.title,
          links:document.querySelectorAll('a[href*="/product--"]').length,
          body:document.body.innerText.length,
          head:document.body.innerText.slice(0,120)})""")
        log("YM try%d ->" % i, info)
finally:
    ch.stop(); OUT.close()
