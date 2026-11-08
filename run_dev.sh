#!/usr/bin/env sh
# Запуск экрана copilot: http://127.0.0.1:8710. Приложение само читает .env.local из папки данных.
cd "$(dirname "$0")" || exit 1
exec python -m uvicorn app.main:app --host 127.0.0.1 --port "${COPILOT_PORT:-8710}"
