# -*- coding: utf-8 -*-
"""
mailbox.py — Авито через почтовые уведомления о сохранённых поисках.

Зачем: у Авито нет открытого API поиска, а сайт закрыт антиботом. Зато Авито
сам присылает письма «новые объявления по вашему поиску». Это официальный
канал, работает стабильно и не нарушает правила.

Как включить:
  1. На Авито подпишитесь на поиск (звёздочка) и включите уведомления на e-mail.
  2. В config.json заполните avito_mail (imap-сервер, логин, пароль приложения).
  3. Включите avito_mail.enabled = true.
"""
from __future__ import annotations

import email
import imaplib
import re
from email.header import decode_header

AVITO_URL_RE = re.compile(r"https?://(?:www\.|m\.)?avito\.ru/[^\s\"'<>]+?[/_]\d{6,}[^\s\"'<>]*")
PRICE_RE = re.compile(r"(\d[\d\s\u00a0\u202f]{2,9})\s*(?:₽|руб)")
TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
BLOCK_RE = re.compile(r"</?(?:div|p|tr|td|li|br|h\d)[^>]*>", re.I)
TAGS_RE = re.compile(r"<[^>]+>")


def _decode(value: str) -> str:
    parts = []
    for chunk, enc in decode_header(value or ""):
        if isinstance(chunk, bytes):
            for codec in (enc, "utf-8", "cp1251"):
                try:
                    parts.append(chunk.decode(codec or "utf-8"))
                    break
                except (LookupError, UnicodeDecodeError):
                    continue
            else:
                parts.append(chunk.decode("utf-8", "replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _html_to_text(html: str) -> str:
    html = TAG_RE.sub(" ", html)
    html = BLOCK_RE.sub("\n", html)
    html = TAGS_RE.sub("", html)
    html = (html.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&quot;", '"').replace("&#39;", "'")
                .replace("&laquo;", "«").replace("&raquo;", "»"))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in html.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _body_text(msg) -> str:
    html, plain = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype not in ("text/html", "text/plain"):
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, "replace")
            except LookupError:
                text = payload.decode("utf-8", "replace")
            if ctype == "text/html":
                html += text
            else:
                plain += text
    else:
        payload = msg.get_payload(decode=True) or b""
        text = payload.decode(msg.get_content_charset() or "utf-8", "replace")
        if msg.get_content_type() == "text/html":
            html = text
        else:
            plain = text
    return _html_to_text(html) if html else plain


def parse_message(text: str) -> list[dict]:
    """Достаёт из письма объявления: ссылка, цена, заголовок."""
    lines = text.splitlines()
    urls = []
    for i, line in enumerate(lines):
        for m in AVITO_URL_RE.finditer(line):
            url = m.group(0).rstrip(").,;")
            if url not in urls:
                urls.append(url)

    out = []
    for url in urls:
        idx = next((i for i, ln in enumerate(lines) if url in ln), None)
        if idx is None:
            continue
        window = "\n".join(lines[max(0, idx - 6): idx + 7])
        price = None
        for m in PRICE_RE.finditer(window):
            price = int(re.sub(r"\D", "", m.group(1)))
            if price > 100:
                break
        if not price:
            continue
        title = ""
        for ln in reversed(lines[max(0, idx - 8): idx]):
            if not ln or AVITO_URL_RE.search(ln) or PRICE_RE.search(ln):
                continue
            if len(ln) > 8:
                title = ln
                break
        out.append({"url": url, "price": price, "title": title, "id": url})
    return out


def collect(config: dict, watch: dict) -> list[dict]:
    cfg = config.get("avito_mail", {})
    if not cfg.get("enabled"):
        return []

    host = cfg.get("imap_host", "")
    user = cfg.get("user", "")
    password = cfg.get("password", "")
    if not (host and user and password):
        return []

    keywords = [w.lower() for w in watch.get("mail_keywords", [])] or \
               [w.lower() for w in re.split(r"\s+", watch.get("query", "")) if len(w) > 2]

    offers: list[dict] = []
    try:
        conn = imaplib.IMAP4_SSL(host, int(cfg.get("port", 993)), timeout=30)
        conn.login(user, password)
        conn.select(cfg.get("folder", "INBOX"))
        since = cfg.get("since_days", 3)
        typ, data = conn.search(None, f'(SINCE "{_imap_date(since)}")')
        ids = (data[0] or b"").split()
        for num in ids[-40:]:
            typ, raw = conn.fetch(num, "(RFC822)")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            sender = (_decode(msg.get("From", ""))).lower()
            subject = _decode(msg.get("Subject", ""))
            if cfg.get("sender_filter", "avito") and cfg.get("sender_filter", "avito") not in sender:
                continue
            blob = (subject + "\n" + _body_text(msg)).lower()
            if keywords and not any(k in blob for k in keywords):
                continue
            for item in parse_message(_body_text(msg)):
                item["mail_subject"] = subject
                offers.append(item)
        conn.logout()
    except Exception as e:  # noqa: BLE001
        print(f"  [авито-почта] ошибка: {e}")
    return offers


def _imap_date(days: int) -> str:
    import datetime
    d = datetime.date.today() - datetime.timedelta(days=int(days))
    return d.strftime("%d-%b-%Y")
