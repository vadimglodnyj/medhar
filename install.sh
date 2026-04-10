#!/bin/bash
# Встановлення лише requirements.txt (мінімум для app.py)

set -e

echo "🏥 ВСТАНОВЛЕННЯ МЕДХАР"
echo "=================================================="
echo

if ! command -v python3 &>/dev/null; then
    echo "❌ Python3 не знайдено! Встановіть Python 3.8+"
    exit 1
fi

python3 --version

python_version=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
required_version="3.8"
if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "❌ Потрібен Python 3.8+ (зараз: $python_version)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo
echo "📦 pip install -r requirements.txt"
echo

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

echo
echo "🎉 ГОТОВО"
echo "1. Покладіть Excel у data/"
echo "2. python3 app.py"
echo "3. http://127.0.0.1:5000/"
echo "=================================================="
