#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для виправлення проблем з базою даних
"""

import os
import sys
import shutil

def create_directories():
    """Створює необхідні папки"""
    print("📁 Створення необхідних папок...")
    
    directories = [
        "data",
        "database", 
        "templates",
        "static",
        "temp",
        "uploads",
        "output"
    ]
    
    for directory in directories:
        if not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
                print(f"✅ Папка {directory} створена")
            except OSError as e:
                print(f"❌ Помилка створення папки {directory}: {e}")
                return False
        else:
            print(f"✅ Папка {directory} вже існує")
    
    return True

def check_database():
    """Перевіряє наявність бази даних"""
    print("🗄️ Перевірка бази даних...")
    
    # Перевіряємо чи є база даних в корені
    root_db = "medical_new.db"
    database_dir = "database"
    database_file = os.path.join(database_dir, "medical_new.db")
    
    if os.path.exists(root_db):
        print(f"✅ База даних знайдена в корені: {root_db}")
        
        # Переміщуємо в папку database
        if not os.path.exists(database_dir):
            os.makedirs(database_dir, exist_ok=True)
            print(f"✅ Папка {database_dir} створена")
        
        if not os.path.exists(database_file):
            try:
                shutil.move(root_db, database_file)
                print(f"✅ База даних переміщена в {database_file}")
            except Exception as e:
                print(f"❌ Помилка переміщення бази даних: {e}")
                return False
        else:
            print(f"✅ База даних вже існує в {database_file}")
            
    elif os.path.exists(database_file):
        print(f"✅ База даних знайдена в {database_file}")
    else:
        print("⚠️ База даних не знайдена. Створіть її вручну або імпортуйте дані.")
        return False
    
    return True

def main():
    """Головна функція виправлення"""
    print("🔧 ВИПРАВЛЕННЯ ПРОБЛЕМ З БАЗОЮ ДАНИХ")
    print("=" * 50)
    
    # Створюємо необхідні папки
    if not create_directories():
        return 1
    
    # Перевіряємо базу даних
    if not check_database():
        print("⚠️ Проблеми з базою даних. Перевірте вручну.")
        return 1
    
    print("\n🎉 ВИПРАВЛЕННЯ ЗАВЕРШЕНО!")
    print("=" * 50)
    print("📋 Тепер можна запустити додаток:")
    print("python app.py")
    print("=" * 50)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
