#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Добавление и удаление машин в списке наблюдения.

    python manage_plates.py

Номера берутся из колонки «Номер Т/С» в таблице (например 629, 606, 826/064).
Чтобы удалить номер — начните его с минуса: -629
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"

INVISIBLE = "\ufeff\u200b\u200e\u200f\xa0"


def clean_plate(text: str) -> str:
    """Убирает невидимые символы и лишние пробелы из введённого номера."""
    for ch in INVISIBLE:
        text = text.replace(ch, " ")
    return " ".join(text.split()).strip().upper()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    plates = [str(p).strip() for p in (cfg.get("plates") or []) if str(p).strip()]
    cities = ", ".join(cfg.get("cities") or []) or "все города"

    print("=" * 60)
    print("  МАШИНЫ ПОД НАБЛЮДЕНИЕМ")
    print("=" * 60)
    print(f"Город: {cities}")
    print("Сейчас в списке:", ", ".join(plates) if plates else "пусто (уведомления по всем машинам)")
    print()
    print("Введите номер машины — как в колонке «Номер Т/С» в таблице.")
    print("Например: 629      Или несколько сразу: 629 606 317")
    print("Чтобы удалить номер — начните с минуса: -629")
    print("Пустая строка — закончить.\n")

    changed = False
    while True:
        try:
            raw = input("Номер: ").strip()
        except EOFError:
            break
        if not raw:
            break

        for part in raw.replace(",", " ").split():
            part = part.strip()
            remove = part.startswith("-")
            clean = clean_plate(part.lstrip("-"))
            if not clean:
                continue

            if remove:
                before = len(plates)
                plates = [p for p in plates if p.upper() != clean]
                if len(plates) < before:
                    print(f"  удалено: {clean}")
                    changed = True
                else:
                    print(f"  такого номера нет: {clean}")
            elif clean in [p.upper() for p in plates]:
                print(f"  уже в списке: {clean}")
            else:
                plates.append(clean)
                print(f"  добавлено: {clean}")
                changed = True

    if changed:
        cfg["plates"] = plates
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nСохранено в {CONFIG}")
    else:
        print("\nИзменений нет.")

    print("\nМашины под наблюдением:", ", ".join(plates) if plates else "пусто")
    print("\nЕсли программа уже запущена, перезапускать не нужно —")
    print("новый список подхватится автоматически в течение минуты.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nОтменено.")
