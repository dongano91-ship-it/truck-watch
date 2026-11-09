# -*- coding: utf-8 -*-
"""notify.py — отправка уведомлений в Telegram и MAX."""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

TELEGRAM_API = "https://api.telegram.org"
MAX_API = "https://platform-api2.max.ru"
CA_BUNDLE = "certs/ca-bundle.pem"


def ssl_context(insecure: bool = False) -> ssl.SSLContext:
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        with open(CA_BUNDLE, "rb"):
            return ssl.create_default_context(cafile=CA_BUNDLE)
    except OSError:
        return ssl.create_default_context()


def _post(url: str, payload: dict | bytes, headers: dict, insecure: bool = False, timeout: int = 25):
    data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context(insecure)) as r:
        return r.status, r.read()


def send_telegram(text: str, cfg: dict) -> bool:
    tg = cfg.get("notify", {}).get("telegram", {})
    token = str(tg.get("token", "")).strip()
    chat_id = str(tg.get("chat_id", "")).strip()
    if not token or not chat_id:
        return False
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in (1, 2, 3):
        try:
            status, _ = _post(url, payload, {"Content-Type": "application/json"}, tg.get("insecure_tls", False))
            return 200 <= status < 300
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            print(f"  [telegram] HTTP {e.code}: {body}")
            if e.code in (400, 401, 403):
                return False
        except Exception as e:  # noqa: BLE001
            print(f"  [telegram] сетевая ошибка ({attempt}/3): {e}")
        time.sleep(2 * attempt)
    return False


def send_max(text: str, cfg: dict) -> bool:
    mx = cfg.get("notify", {}).get("max", {})
    token = str(mx.get("token", "")).strip()
    chat_id = str(mx.get("chat_id", "")).strip()
    if not token or not chat_id:
        return False
    url = f"{MAX_API}/messages?chat_id={urllib.parse.quote(chat_id)}"
    headers = {"Authorization": token, "Content-Type": "application/json; charset=utf-8"}
    for attempt in (1, 2, 3):
        try:
            status, _ = _post(url, {"text": text}, headers, mx.get("insecure_tls", False))
            return 200 <= status < 300
        except urllib.error.HTTPError as e:
            print(f"  [max] HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
            if e.code in (400, 401, 403):
                return False
        except Exception as e:  # noqa: BLE001
            print(f"  [max] сетевая ошибка ({attempt}/3): {e}")
        time.sleep(3 * attempt)
    return False


_warned_once = False


def send(text: str, cfg: dict) -> bool:
    """Отправляет во все настроенные каналы. True — если хоть куда-то ушло."""
    global _warned_once
    ok_tg = send_telegram(text, cfg)
    ok_max = send_max(text, cfg)
    if not (ok_tg or ok_max) and not _warned_once:
        _warned_once = True
        print("  [notify] каналы не настроены (notify.telegram/max в config.json) — "
              "уведомления только в лог")
    return ok_tg or ok_max
