# -*- coding: utf-8 -*-
import os, sys, io, json
sys.path.insert(0, os.path.abspath("dealwatch"))
import browser as B

OUT = io.open("dealwatch/.cache/probe5.txt", "w", encoding="utf-8")
def log(*a):
    line = " ".join(str(x) for x in a); print(line); OUT.write(line + "\n"); OUT.flush()

ch = B.Chrome(port=9225, profile_dir="dealwatch/.cache/probe-profile3", headless=False, verbose=False)
ch.start()
page = ch.new_page()
try:
    page.goto("https://www.ozon.ru/search/?text=iphone+15+128", settle=9)
    page.scroll(steps=2)
    log("title:", page.evaluate("document.title"))
    log("anchors:", page.evaluate("""JSON.stringify(document.querySelectorAll('a[href*="/product/"]').length)"""))
    log("sample cards:", page.evaluate("""(() => {
      const out = [];
      const seen = new Set();
      for (const a of document.querySelectorAll('a[href*="/product/"]')) {
        let n = a, depth = 0;
        while (n && depth < 6) { if (n.getAttribute && n.getAttribute('data-index')) break; n = n.parentElement; depth++; }
        const card = n || a.closest('div');
        if (!card || seen.has(card)) continue;
        seen.add(card);
        const txt = (card.innerText || '').replace(/\\s+/g, ' ').slice(0, 160);
        const alts = [...card.querySelectorAll('img[alt]')].map(i => i.alt).filter(Boolean).slice(0,2);
        out.push({txt, alts, cls: (card.className || '').slice(0, 60)});
        if (out.length >= 3) break;
      }
      return JSON.stringify(out, null, 1);
    })()"""))
finally:
    ch.stop(); OUT.close()
