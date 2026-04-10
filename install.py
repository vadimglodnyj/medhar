#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для встановлення залежностей медичного додатку
"""

import sys
import os
import subprocess
import platform

def check_python_version():
    """Перевіряє версію Python"""
    if sys.version_info < (3, 8):
        print("❌ Потрібен Python 3.8 або новіший!")
        print(f"Поточна версія: {sys.version}")
        return False
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    return True

def install_core_packages():
    """Встановлює основні пакети по одному"""
    print("📦 Встановлення основних пакетів...")
    
    # Список основних пакетів в порядку залежностей
    packages = [
        "setuptools",
        "wheel", 
        "pip",
        "numpy",
        "Flask",
        "pandas",
        "python-docx",
        "docxtpl",
        "openpyxl",
        "pymorphy3",
        "pymorphy3-dicts-uk",
        "Werkzeug",
        "requests",
        "beautifulsoup4",
        "python-dotenv"
    ]
    
    failed_packages = []
    
    for package in packages:
        try:
            print(f"📦 Встановлення {package}...")
            result = subprocess.run([
                sys.executable, "-m", "pip", "install", 
                package, "--upgrade", "--no-cache-dir"
            ], check=True, capture_output=True, text=True)
            print(f"✅ {package} встановлено успішно")
            
        except subprocess.CalledProcessError as e:
            print(f"❌ Помилка встановлення {package}: {e}")
            if e.stderr:
                print(f"STDERR: {e.stderr}")
            failed_packages.append(package)
    
    if failed_packages:
        print(f"⚠️  Не вдалося встановити: {', '.join(failed_packages)}")
        return False
    
    print("✅ Всі основні пакети встановлено успішно!")
    return True

def install_optional_packages():
    """Встановлює опціональні пакети"""
    print("📦 Встановлення опціональних пакетів...")
    
    optional_packages = [
        "pdf2image",
        "pypdf", 
        "Pillow",
        "easyocr",
        "pytesseract"
    ]
    
    for package in optional_packages:
        try:
            print(f"📦 Встановлення {package}...")
            subprocess.run([
                sys.executable, "-m", "pip", "install", 
                package, "--upgrade", "--no-cache-dir"
            ], check=True, capture_output=True)
            print(f"✅ {package} встановлено успішно")
            
        except subprocess.CalledProcessError as e:
            print(f"⚠️  Не вдалося встановити {package} (опціональний): {e}")
            print("Продовжуємо без цього пакета...")

def check_system_requirements():
    """Перевіряє системні вимоги"""
    print("🔍 Перевірка системних вимог...")
    
    # Перевіряємо операційну систему
    system = platform.system()
    print(f"🖥️  ОС: {system}")
    
    # Перевіряємо архітектуру
    arch = platform.machine()
    print(f"🏗️  Архітектура: {arch}")
    
    # Перевіряємо наявність git
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        print("✅ Git встановлено")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("⚠️  Git не знайдено (не обов'язково)")
    
    return True

def create_virtual_environment():
    """Створює віртуальне середовище"""
    print("🐍 Створення віртуального середовища...")
    
    if os.path.exists("venv"):
        print("✅ Віртуальне середовище вже існує")
        return True
    
    try:
        subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
        print("✅ Віртуальне середовище створено")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Помилка створення віртуального середовища: {e}")
        return False

def main():
    """Головна функція встановлення"""
    print("🏥 ВСТАНОВЛЕННЯ МЕДИЧНОГО ДОДАТКУ")
    print("=" * 50)
    
    # Перевіряємо Python
    if not check_python_version():
        return 1
    
    # Перевіряємо системні вимоги
    if not check_system_requirements():
        return 1
    
    # Створюємо віртуальне середовище
    if not create_virtual_environment():
        return 1
    
    # Оновлюємо pip
    try:
        print("🔄 Оновлення pip...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], 
                      check=True, capture_output=True)
        print("✅ pip оновлено")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Не вдалося оновити pip: {e}")
    
    # Встановлюємо основні пакети
    if not install_core_packages():
        print("❌ Не вдалося встановити основні пакети")
        return 1
    
    # Встановлюємо опціональні пакети
    install_optional_packages()
    
    print("\n🎉 ВСТАНОВЛЕННЯ ЗАВЕРШЕНО!")
    print("=" * 50)
    print("📋 Наступні кроки:")
    print("1. Додайте папку 'data/' з Excel файлами")
    print("2. Запустіть додаток: python app.py")
    print("3. Відкрийте браузер: http://127.0.0.1:5000")
    print("=" * 50)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
