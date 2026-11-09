# === Бот 1: Truck Watch ===
# Мониторинг логистики из Google Таблицы с уведомлениями в Telegram
# Документация: C:\Users\donga\OneDrive\Documents\deepseek-harness\default-workspace\README.md

# Для деплоя на Railway:
# 1. Скопируйте файлы из папки railway/ в корень репозитория
# 2. Установите переменные окружения в Railway:
#    - TGW_TELEGRAM_TOKEN=<токен бота>
#    - TGW_TELEGRAM_CHAT_IDS=<список ID через запятую>
#    - TGW_SHEET_URL=<ссылка на таблицу>
# 3. Или используйте GitHub Actions (файл .github/workflows/watch.yml)

# Локальный запуск:
python watcher.py --once    # тестовый запуск
python watcher.py           # постоянный мониторинг

