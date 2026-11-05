#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Мастер настройки Telegram: спросит токен, сам найдёт chat_id и запишет config.json.

    python setup.py

Руками ничего править не нужно.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
TG_API = "https://api.telegram.org"


def api(token: str, method: str, params: dict | None = None) -> dict:
    url = f"{TG_API}/bot{token}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        print("\nВвод закрыт — выходим.")
        raise SystemExit(1)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 66)
    print("  НАСТРОЙКА УВЕДОМЛЕНИЙ В TELEGRAM")
    print("=" * 66)
    print("1. Откройте Telegram и найдите @BotFather")
    print("2. Отправьте ему: /newbot")
    print("3. Придумайте имя и username бота (username оканчивается на 'bot')")
    print("4. BotFather пришлёт токен вида 8123456:AAE-xxxxxxxxxxxx")
    print()

    token = ""
    while True:
        token = ask("Вставьте токен и нажмите Enter: ").strip(' "\'')
        if not token:
            print("Токен пустой. Попробуйте ещё раз.")
            continue
        if ":" not in token:
            print("Это не похоже на токен Telegram (в нём должно быть двоеточие).")
            continue
        try:
            me = api(token, "getMe")
        except urllib.error.HTTPError as exc:
            print(f"Telegram не принял токен (код {exc.code}). Проверьте и вставьте заново.\n")
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"Нет связи с Telegram: {exc}")
            return 1
        if me.get("ok"):
            print(f"\nТокен принят. Бот: @{me['result'].get('username')}\n")
            break

    print(f"Теперь откройте в Telegram бота @{me['result'].get('username')}")
    print("и отправьте ему любое сообщение — например «привет».")
    ask("\nКогда отправите — нажмите Enter здесь: ")

    chat_id = None
    offset = None
    print("Ищу сообщение от вас...")
    deadline = time.time() + 120
    while time.time() < deadline and chat_id is None:
        try:
            data = api(token, "getUpdates", {"timeout": 25, "offset": offset} if offset else {"timeout": 25})
        except Exception as exc:  # noqa: BLE001
            print(f"Ошибка опроса: {exc}")
            return 1
        for update in data.get("result", []):
            offset = update.get("update_id", 0) + 1
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            if chat.get("id"):
                chat_id = chat["id"]
                who = chat.get("username") or chat.get("first_name") or chat.get("title") or ""
                print(f"Нашёл: chat_id = {chat_id}  ({who})")
        if chat_id is None:
            time.sleep(2)

    if chat_id is None:
        print("\nСообщение не пришло. Проверьте, что вы написали именно своему боту,")
        print("и запустите мастер ещё раз: python setup.py")
        return 1

    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    cfg.setdefault("telegram", {})["token"] = token
    cfg["telegram"]["chat_id"] = str(chat_id)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nЗаписано в {CONFIG}")

    try:
        api(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "🔔 truck-watch: связь настроена. Теперь события по вашим машинам будут приходить сюда.",
            },
        )
        print("Отправил проверочное сообщение — проверьте Telegram.")
    except Exception as exc:  # noqa: BLE001
        print(f"Не смог отправить проверочное сообщение: {exc}")

    print("\nГотово. Запускайте слежение:  python watcher.py")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nОтменено.")
