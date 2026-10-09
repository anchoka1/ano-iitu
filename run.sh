#!/usr/bin/env bash
# =====================================================================
#  Запуск ANO IITU на macOS / Linux.
#    ./run.sh            — подготовить и запустить
#    ./run.sh --setup    — только подготовить
#    ./run.sh --test     — подготовить и прогнать тесты
#  Первый раз: chmod +x run.sh
# =====================================================================
set -euo pipefail                 # останавливаться на любой ошибке
cd "$(dirname "$0")"              # работаем из папки проекта
export PYTHONUTF8=1

say()  { printf '\033[36m[ANO IITU]\033[0m %s\n' "$1"; }
fail() { printf '\033[31m[ANO IITU] ОШИБКА:\033[0m %s\n' "$1"; exit 1; }

# 1. Виртуальное окружение
if [ ! -x .venv/bin/python ]; then
  PY=""
  for c in python3.12 python3.11 python3.13 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      PY="$c"; break
    fi
  done
  [ -n "$PY" ] || fail "Не найден Python 3.11+. Установите его (brew install python@3.12 или apt install python3)."
  say "Создаю .venv на $($PY --version) ..."
  "$PY" -m venv .venv
fi
VENV_PY=.venv/bin/python

# 2. Зависимости (только если requirements.txt изменился)
HASH=$("$VENV_PY" -c 'import hashlib;print(hashlib.sha256(open("requirements.txt","rb").read()).hexdigest().upper())')
if [ ! -f .venv-installed.txt ] || [ "$(cat .venv-installed.txt)" != "$HASH" ]; then
  say "Устанавливаю зависимости ..."
  "$VENV_PY" -m pip install --disable-pip-version-check -q --upgrade pip
  "$VENV_PY" -m pip install --disable-pip-version-check -q -r requirements.txt || fail "Не удалось установить зависимости."
  echo "$HASH" > .venv-installed.txt
fi

# 3. .env
if [ ! -f .env ]; then cp .env.example .env; say "Создан .env (демо-режим)."; fi

# 4. База
"$VENV_PY" scripts/init_db.py

case "${1:-}" in
  --setup) say "Подготовка завершена."; exit 0 ;;
  --test)  exec "$VENV_PY" -m pytest ;;
esac

say "Запускаю сервер: http://localhost:8000"
exec "$VENV_PY" -m backend.app.main
