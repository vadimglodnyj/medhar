#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Простий скрипт для запуску медичного додатку
"""

import sys
import os
import subprocess

def main():
    """Запуск медичного додатку"""
    print("🏥 Запуск медичного додатку...")
    print("=" * 50)
    
    # Перевіряємо чи є віртуальне середовище
    if not os.path.exists("venv"):
        print("❌ Віртуальне середовище не знайдено!")
        print("Створіть віртуальне середовище: python -m venv venv")
        return
    
    # Перевіряємо чи є app.py
    if not os.path.exists("app.py"):
        print("❌ Файл app.py не знайдено!")
        return
    
    try:
        # Запускаємо додаток
        print("🚀 Запуск Flask додатку...")
        print("📱 Відкрийте браузер: http://127.0.0.1:5000")
        print("⏹️  Для зупинки натисніть Ctrl+C")
        print("=" * 50)
        
        # Запускаємо через Python
        subprocess.run([sys.executable, "app.py"], check=True)
        
    except KeyboardInterrupt:
        print("\n⏹️  Додаток зупинено користувачем")
    except subprocess.CalledProcessError as e:
        print(f"❌ Помилка запуску: {e}")
    except Exception as e:
        print(f"❌ Неочікувана помилка: {e}")

if __name__ == "__main__":
    main()
