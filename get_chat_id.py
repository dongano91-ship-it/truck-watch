#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Помощник: показывает chat_id, чтобы вписать его в config.json.

    python get_chat_id.py            # спросит токен и сам определит сервис
    python get_chat_id.py <ТОКЕН>    # MAX или Telegram

Telegram-токен содержит двоеточие (123456:AAE...), токен MAX — нет.
"""

import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CERTS = Path(__file__).resolve().parent / "certs"
MAX_API = "https://platform-api2.max.ru"
TG_API = "https://api.telegram.org"


def ssl_context() -> ssl.SSLContext:
    """Стандартные УЦ + сертификат Минцифры, на котором работает MAX."""
    ctx = ssl.create_default_context()
    for path in sorted(CERTS.glob("*.crt")):
        try:
            ctx.load_verify_locations(cafile=str(path))
        except Exception:
            pass
    return ctx


def watch_telegram(token: str) -> None:
    print("Telegram: напишите своему боту любое сообщение. Ctrl+C — выход.\n")
    offset = None
    while True:
        params = {"timeout": 30}
        if offset is not None:
            params["offset"] = offset
        url = f"{TG_API}/bot{token}/getUpdates?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=70, context=ssl_context()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            print(f"Telegram ответил {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"Ошибка запроса: {exc}")
            return

        for update in data.get("result", []):
            offset = update.get("update_id", 0) + 1
            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            if not chat:
                continue
            title = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
            print(f"chat_id = {chat.get('id')}   тип: {chat.get('type')}   {title}")


def watch_max(token: str) -> None:
    print("MAX: напишите своему боту любое сообщение. Ctrl+C — выход.\n")
    marker = None
    while True:
        params = {"timeout": 30, "limit": 100}
        if marker:
            params["marker"] = marker
        url = MAX_API + "/updates?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": token})
        try:
            with urllib.request.urlopen(req, timeout=70, context=ssl_context()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"Ошибка запроса: {exc}")
            return

        marker = data.get("marker", marker)
        for update in data.get("updates", []):
            raw = json.dumps(update, ensure_ascii=False)
            ids = re.findall(r'"chat_id"\s*:\s*(-?\d+)', raw)
            kind = update.get("update_type", "?")
            if ids:
                print(f"[{kind}] chat_id = {ids[0]}\n          {raw[:300]}\n")
            else:
                print(f"[{kind}] {raw[:300]}\n")


def main() -> int:
    token = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    if not token:
        token = input("Токен бота (MAX или Telegram): ").strip()
    if not token:
        print("Без токена не получится.")
        return 1

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if ":" in token:
        watch_telegram(token)
    else:
        watch_max(token)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nВыход.")
