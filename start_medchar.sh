#!/bin/bash
# Легкий запуск (Linux/macOS): venv + requirements.txt при потребі, потім app.py
set -e
cd "$(dirname "$0")"

if ! command -v python3 &>/dev/null; then
  echo "Потрібен Python 3.8+ (python3)."
  exit 1
fi

if [[ ! -x venv/bin/python ]]; then
  echo "[MedChar] Створення venv..."
  python3 -m venv venv
fi

PY=venv/bin/python
if ! "$PY" -c "import flask, docxtpl, pymorphy3" 2>/dev/null; then
  echo "[MedChar] Встановлення залежностей (лише ядро)..."
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -r requirements.txt
fi

echo ""
echo "[MedChar] http://127.0.0.1:5000/"
echo "[MedChar] Зупинка: Ctrl+C"
echo ""
"$PY" app.py
