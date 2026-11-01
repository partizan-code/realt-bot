#!/bin/bash
set -e

echo "=== Деплой realt-bot ==="

cd "$(dirname "$0")/.."

echo "--- Pull с GitHub ---"
git pull origin main

echo "--- Установка зависимостей ---"
uv sync --locked --no-dev

echo "--- Перезапуск сервиса ---"
# sudo systemctl restart realt-bot

echo "=== Деплой завершен ==="
