#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для налаштування авторизації OneDrive
"""

import getpass
from onedrive_config import update_config, save_config_to_file, get_config

def setup_authentication():
    """Налаштовуємо авторизацію OneDrive"""
    print("=== НАЛАШТУВАННЯ АВТОРИЗАЦІЇ ONEDRIVE ===")
    print()
    
    # Отримуємо поточну конфігурацію
    config = get_config()
    print(f"Поточний URL: {config['url']}")
    print()
    
    # Запитуємо дані авторизації
    print("Введіть дані для авторизації в OneDrive:")
    username = input("Логін (email): ").strip()
    
    if not username:
        print("Логін не може бути порожнім!")
        return False
    
    password = getpass.getpass("Пароль: ").strip()
    
    if not password:
        print("Пароль не може бути порожнім!")
        return False
    
    # Оновлюємо конфігурацію
    update_config(username=username, password=password)
    
    # Зберігаємо в файл
    if save_config_to_file():
        print("✅ Конфігурація збережена успішно!")
        print(f"Логін: {username}")
        print("Пароль: ********")
        return True
    else:
        print("❌ Помилка збереження конфігурації!")
        return False

def test_connection():
    """Тестуємо з'єднання з OneDrive"""
    print("\n=== ТЕСТУВАННЯ З'ЄДНАННЯ ===")
    
    try:
        from utils.onedrive_sync import OneDriveSync
        
        sync = OneDriveSync()
        print("Спроба завантаження файлу...")
        
        success, data, file_hash = sync.download_excel_file()
        
        if success:
            print("✅ З'єднання успішне!")
            print(f"Розмір файлу: {len(data)} байт")
            print(f"Хеш файлу: {file_hash[:16]}...")
            return True
        else:
            print("❌ Помилка завантаження файлу")
            return False
            
    except Exception as e:
        print(f"❌ Помилка: {e}")
        return False

def main():
    """Основна функція"""
    print("OneDrive Синхронізація - Налаштування авторизації")
    print("=" * 50)
    
    # Налаштовуємо авторизацію
    if not setup_authentication():
        print("Налаштування не завершено.")
        return
    
    # Тестуємо з'єднання
    if test_connection():
        print("\n🎉 Налаштування завершено успішно!")
        print("Тепер ви можете використовувати синхронізацію OneDrive.")
    else:
        print("\n⚠️  Налаштування завершено, але з'єднання не працює.")
        print("Перевірте правильність логіну та пароля.")

if __name__ == "__main__":
    main()
