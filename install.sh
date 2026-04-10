#!/bin/bash
# Скрипт для встановлення залежностей медичного додатку (Linux/macOS)

echo "🏥 ВСТАНОВЛЕННЯ МЕДИЧНОГО ДОДАТКУ"
echo "=================================================="
echo

# Перевіряємо чи є Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 не знайдено!"
    echo "Встановіть Python 3.8+ та спробуйте знову"
    exit 1
fi

echo "✅ Python знайдено"
python3 --version

# Перевіряємо версію Python
python_version=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
required_version="3.8"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "❌ Потрібен Python 3.8 або новіший!"
    echo "Поточна версія: $python_version"
    exit 1
fi

echo
echo "📦 Встановлення залежностей..."
echo

# Оновлюємо pip
echo "🔄 Оновлення pip..."
python3 -m pip install --upgrade pip

# Встановлюємо залежності
echo "📥 Встановлення пакетів..."
python3 -m pip install -r requirements.txt

if [ $? -ne 0 ]; then
    echo "❌ Помилка встановлення залежностей!"
    exit 1
fi

echo
echo "🎉 ВСТАНОВЛЕННЯ ЗАВЕРШЕНО!"
echo "=================================================="
echo "📋 Наступні кроки:"
echo "1. Додайте папку 'data/' з Excel файлами"
echo "2. Запустіть додаток: python3 app.py"
echo "3. Відкрийте браузер: http://127.0.0.1:5000"
echo "=================================================="
echo

