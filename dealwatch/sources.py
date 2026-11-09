# -*- coding: utf-8 -*-
"""
sources.py — извлечение предложений со страниц площадок.

Логика универсальная и настраивается из config.json:
  mode = "cards"   — карточки товаров (Wildberries, Авито)
  mode = "anchors" — ссылки на товар, цена ищется рядом (Ozon, Яндекс Маркет, Мегамаркет)

Если селекторы перестали работать (площадки часто меняют вёрстку), правится
только config.json — код трогать не нужно.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse

EXTRACT_JS = r"""
(function (cfg) {
  var out = [];
  var limit = cfg.limit || 40;

  function clean(s) { return (s || '').replace(/\s+/g, ' ').trim(); }

  function digits(el) {
    if (!el) return null;
    var t = (el.textContent || '').replace(/[\s\u00a0\u202f\u2009]/g, '');
    var m = t.match(/(\d{2,9})/);
    return m ? parseInt(m[1], 10) : null;
  }

  function priceFromElement(el) {
    if (!el) return null;
    var nodes = el.querySelectorAll('[class*="price" i], [data-price], [itemprop="price"]');
    for (var i = 0; i < nodes.length; i++) {
      var p = digits(nodes[i]);
      if (p) return p;
    }
    var t = clean(el.textContent);
    if (t.length < 160 && /[\u20bd\u20ac$]|руб/i.test(t)) {
      return digits(el);
    }
    return null;
  }

  function findPrice(start) {
    var node = start;
    for (var d = 0; d < 7 && node; d++, node = node.parentElement) {
      var p = priceFromElement(node);
      if (p) return p;
      if ((node.textContent || '').length > 4000) break;
    }
    return null;
  }

  function pickTitle(root) {
    var best = '', bestScore = -1;
    var nodes = root.querySelectorAll('img[alt], [title], span, div, a, p');
    var limitNodes = Math.min(nodes.length, 250);
    for (var k = 0; k < limitNodes; k++) {
      var e = nodes[k];
      var t = '';
      if (e.tagName === 'IMG') t = e.getAttribute('alt') || '';
      else if (e.hasAttribute && e.hasAttribute('title')) t = e.getAttribute('title');
      if (!t) t = clean(e.textContent);
      if (!t) continue;
      if (/\d+\s*[\u20bd\u20ac$]|руб\.?$/i.test(t)) continue;      // цена
      if (!/[A-Za-z\u0410-\u044f]{2}/.test(t)) continue;            // нужны буквы
      if (t.length < 12 || t.length > 150) continue;
      if (e.children && e.children.length > 6) continue;            // не целая карточка
      var score = t.length + (e.tagName === 'IMG' ? 30 : 0);
      if (score > bestScore) { bestScore = score; best = t; }
    }
    return best;
  }

  function titleOf(card, a) {
    var t = '';
    if (cfg.title_sel) { var e = card.querySelector(cfg.title_sel); if (e) t = clean(e.textContent); }
    if (!t && cfg.title_sel_attr) {
      var e2 = card.querySelector(cfg.title_sel_attr.sel);
      if (e2) t = clean(e2.getAttribute(cfg.title_sel_attr.attr) || e2.textContent);
    }
    if (!t && a) t = clean(a.getAttribute('title') || a.getAttribute('aria-label') || '');
    if (!t && a) t = pickTitle(a);
    if (!t) { var img = card.querySelector('img[alt]'); if (img) t = clean(img.getAttribute('alt')); }
    return t;
  }

  function idOf(card, url) {
    if (cfg.id_attr) {
      var v = card.getAttribute(cfg.id_attr);
      if (v) return v;
    }
    var m = (url || '').match(cfg.id_re || /(\d{5,})/);
    return m ? m[1] : url;
  }

  function push(url, title, price, id) {
    if (!url || !price || price < (cfg.min_price || 0)) return;
    if (out.some(function (o) { return o.url === url; })) return;
    out.push({ url: url, title: title, price: price, id: String(id) });
  }

  if (cfg.mode === 'cards') {
    var cards = document.querySelectorAll(cfg.selector);
    for (var i = 0; i < cards.length && out.length < limit; i++) {
      var card = cards[i];
      var a = cfg.link_sel ? card.querySelector(cfg.link_sel) : card.querySelector('a[href]');
      var price = null;
      if (cfg.price_sel_attr) {
        var pe = card.querySelector(cfg.price_sel_attr.sel);
        if (pe) price = parseInt(String(pe.getAttribute(cfg.price_sel_attr.attr)).replace(/\D/g, ''), 10);
      }
      if (!price) price = digits(cfg.price_sel ? card.querySelector(cfg.price_sel) : null);
      if (!price) price = findPrice(card);
      if (!price) continue;
      var url = a ? a.href : '';
      push(url, titleOf(card, a), price, idOf(card, url));
    }
  } else {
    var links = document.querySelectorAll(cfg.selector);
    var seen = {};
    for (var j = 0; j < links.length && out.length < limit; j++) {
      var link = links[j];
      var href = link.href;
      if (!href || seen[href]) continue;
      seen[href] = 1;
      var p = findPrice(link);
      if (!p) continue;
      push(href, titleOf(link, link), p, idOf(link, href));
    }
  }
  return JSON.stringify(out);
})(__CFG__)
"""

DIAG_JS = r"""
JSON.stringify({
  title: document.title,
  url: location.href,
  body_len: (document.body && document.body.innerText || '').length,
  text: (document.body && document.body.innerText || '').slice(0, 400)
})
"""


class SourceError(RuntimeError):
    pass


def _looks_blocked(diag: dict) -> str | None:
    text = (diag.get("text") or "").lower()
    title = (diag.get("title") or "").lower()
    if "captcha" in title or "captcha" in text or "робот" in text:
        return "капча/антибот"
    if "доступ ограничен" in text or "access denied" in text or "403" in title:
        return "доступ заблокирован"
    if "проверка" in text and "браузер" in text:
        return "проверка браузера"
    if diag.get("body_len", 0) < 300:
        return "пустая страница"
    return None


def search(page, platform: dict, query: str, limit: int = 40) -> tuple[list[dict], dict]:
    """Возвращает (предложения, диагностика). Делает до attempts попыток."""
    attempts = int(platform.get("attempts", 2))

    # Прогрев: некоторые площадки блокируют первый заход в новой сессии.
    warm = platform.get("warmup")
    if warm:
        done = getattr(page, "_warm", None)
        if done is None:
            done = set()
            page._warm = done
        if warm not in done:
            done.add(warm)
            try:
                page.goto(warm, settle=2.5, timeout=platform.get("timeout", 60))
            except Exception:
                pass

    url = platform["url"].replace("{q}", urllib.parse.quote_plus(query))
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        page.goto(url, settle=platform.get("settle", 4.0), timeout=platform.get("timeout", 60))
        if platform.get("scroll", True):
            page.scroll(steps=platform.get("scroll_steps", 3))

        cfg = {k: v for k, v in platform.items()
               if k in ("mode", "selector", "link_sel", "title_sel", "title_sel_attr", "price_sel",
                        "price_sel_attr", "id_attr", "id_re", "min_price")}
        cfg["limit"] = limit
        js = EXTRACT_JS.replace("__CFG__", json.dumps(cfg, ensure_ascii=False))

        offers = []
        try:
            raw = page.evaluate(js)
            if raw:
                offers = json.loads(raw)
        except Exception as e:  # noqa: BLE001
            last_error = SourceError(f"{platform.get('name', '?')}: ошибка разбора ({e})")
            time.sleep(3)
            continue

        try:
            diag = json.loads(page.evaluate(DIAG_JS) or "{}")
        except Exception:
            diag = {}

        for o in offers:
            o["title"] = o.get("title") or ""
            o["price"] = int(o.get("price") or 0)

        if offers:
            return offers, diag

        reason = _looks_blocked(diag) or "предложений не найдено (селекторы устарели или пустая выдача)"
        last_error = SourceError(f"{platform.get('name', '?')}: {reason}")
        if attempt < attempts:
            time.sleep(4)

    raise last_error or SourceError(f"{platform.get('name', '?')}: нет данных")
