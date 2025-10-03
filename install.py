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

def install_requirements():
    """Встановлює залежності з requirements.txt"""
    print("📦 Встановлення залежностей...")
    
    if not os.path.exists("requirements.txt"):
        print("❌ Файл requirements.txt не знайдено!")
        return False
    
    try:
        # Оновлюємо pip
        print("🔄 Оновлення pip...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], 
                      check=True, capture_output=True)
        
        # Встановлюємо setuptools та wheel
        print("🔧 Встановлення базових інструментів...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "setuptools", "wheel"], 
                      check=True, capture_output=True)
        
        # Встановлюємо залежності з обробкою помилок
        print("📥 Встановлення пакетів...")
        result = subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--no-cache-dir"], 
                               check=True, capture_output=True, text=True)
        
        print("✅ Залежності встановлено успішно!")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Помилка встановлення: {e}")
        if e.stdout:
            print("STDOUT:", e.stdout)
        if e.stderr:
            print("STDERR:", e.stderr)
        
        # Спробуємо встановити основні пакети окремо
        print("🔄 Спробуємо встановити основні пакети окремо...")
        try:
            core_packages = [
                "numpy", "Flask", "pandas", "python-docx", "docxtpl", 
                "openpyxl", "Werkzeug", "requests", "beautifulsoup4", "python-dotenv"
            ]
            
            for package in core_packages:
                print(f"📦 Встановлення {package}...")
                subprocess.run([sys.executable, "-m", "pip", "install", package], 
                              check=True, capture_output=True)
            
            print("✅ Основні пакети встановлено успішно!")
            return True
            
        except subprocess.CalledProcessError as e2:
            print(f"❌ Помилка встановлення основних пакетів: {e2}")
            return False

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
    
    # Встановлюємо залежності
    if not install_requirements():
        return 1
    
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
