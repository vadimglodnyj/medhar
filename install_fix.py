#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для виправлення проблем з встановленням залежностей
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

def main():
    """Головна функція встановлення"""
    print("🔧 ВИПРАВЛЕННЯ ПРОБЛЕМ З ВСТАНОВЛЕННЯМ")
    print("=" * 50)
    
    # Перевіряємо Python
    if not check_python_version():
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
