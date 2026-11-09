# -*- coding: utf-8 -*-
import os, sys, io
sys.path.insert(0, os.path.abspath("dealwatch"))
import browser as B

OUT = io.open("dealwatch/.cache/probe4.txt", "w", encoding="utf-8")
def log(*a):
    line = " ".join(str(x) for x in a); print(line); OUT.write(line + "\n"); OUT.flush()

ch = B.Chrome(port=9224, profile_dir="dealwatch/.cache/probe-profile2", headless=False, verbose=False)
ch.start()
page = ch.new_page()
try:
    # --- Яндекс: что вообще за ссылки
    page.goto("https://market.yandex.ru/search?text=iphone+15+128", settle=8)
    log("YM title:", page.evaluate("document.title"))
    log("YM href samples:", page.evaluate("""JSON.stringify([...document.querySelectorAll('a[href]')]
        .map(a=>a.getAttribute('href')).filter(h=>/product|card|offer/i.test(h)).slice(0,8))"""))
    log("YM counts:", page.evaluate("""JSON.stringify({
        p:document.querySelectorAll('a[href*="/product"]').length,
        art:document.querySelectorAll('[data-zone-name="offerCard"]').length,
        art2:document.querySelectorAll('article').length,
        txt:document.body.innerText.length})"""))

    # --- Авито: прогрев + общий запрос
    page.goto("https://www.avito.ru/", settle=4)
    for name, url in (("rossiya?q", "https://www.avito.ru/rossiya?q=iphone+15"),
                      ("all?q", "https://www.avito.ru/all?q=iphone+15")):
        page.goto(url, settle=8)
        log("AVITO", name, "->", page.evaluate("""JSON.stringify({title:document.title,
            items:document.querySelectorAll('[data-marker="item"]').length})"""))
finally:
    ch.stop(); OUT.close()
