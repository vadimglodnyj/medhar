#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для імпорту всіх Excel файлів в SQLite базу даних
"""

import os
import sys
import time
import logging
from datetime import datetime

# Додаємо поточну директорію в шлях
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.database_manager import DatabaseManager

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('import_log.txt', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def main():
    """Головна функція імпорту"""
    print("🚀 Початок імпорту даних в SQLite базу")
    print("=" * 60)
    
    start_time = time.time()
    
    # Ініціалізуємо менеджер бази даних
    db_manager = DatabaseManager()
    
    # Шляхи до файлів
    data_dir = "data"
    
    treatments_file = os.path.join(data_dir, "treatments_2025.xlsx")
    
    payment_files = {
        "may_2025": os.path.join(data_dir, "may_2025.xlsx"),
        "june_2025": os.path.join(data_dir, "june_2025.xlsx"),
        "july_2025": os.path.join(data_dir, "july_2025.xlsx"),
        "august_2025": os.path.join(data_dir, "august_2025.xlsx")
    }
    
    # Перевіряємо існування файлів
    missing_files = []
    
    if not os.path.exists(treatments_file):
        missing_files.append(treatments_file)
    
    for month, file_path in payment_files.items():
        if not os.path.exists(file_path):
            missing_files.append(file_path)
    
    if missing_files:
        print("❌ Відсутні файли:")
        for file_path in missing_files:
            print(f"   - {file_path}")
        return
    
    print("✅ Всі файли знайдені")
    
    # Імпортуємо дані лікування
    print("\n📋 Імпорт даних лікування...")
    treatments_count = db_manager.import_treatments_data(treatments_file)
    print(f"✅ Імпортовано {treatments_count} записів лікування")
    
    # Імпортуємо дані оплат
    print("\n💰 Імпорт даних оплат...")
    payments_count = db_manager.import_payments_data(payment_files)
    print(f"✅ Імпортовано {payments_count} записів оплат")
    
    # Отримуємо статистику
    print("\n📊 Статистика бази даних:")
    stats = db_manager.get_database_stats()
    
    print(f"   Записів лікування: {stats['treatments_count']}")
    print(f"   Записів оплат: {stats['payments_count']}")
    print(f"   Доступні місяці: {', '.join(stats['available_months'])}")
    print(f"   Розмір БД: {stats['database_size'] / 1024 / 1024:.2f} MB")
    print(f"   Шлях до БД: {stats['database_path']}")
    
    total_time = time.time() - start_time
    print(f"\n✅ Імпорт завершено за {total_time:.1f} секунд")
    
    # Тестуємо швидкість пошуку
    print("\n🔍 Тестування швидкості пошуку...")
    test_search_speed(db_manager)

def test_search_speed(db_manager: DatabaseManager):
    """Тестує швидкість пошуку в базі даних"""
    
    # Тест 1: Пошук лікування з фільтрами
    start_time = time.time()
    criteria = {
        'unit_filter': True,
        'diagnosis_keywords': True,
        'combat_status': 'бойова'
    }
    treatments_results = db_manager.search_treatments(criteria)
    treatments_time = time.time() - start_time
    
    print(f"   Пошук лікування: {len(treatments_results)} записів за {treatments_time:.3f} сек")
    
    # Тест 2: Пошук оплат для першого пацієнта
    if treatments_results:
        patient_name = treatments_results[0]['full_name']
        start_time = time.time()
        payment_results = db_manager.search_payments(patient_name)
        payments_time = time.time() - start_time
        
        print(f"   Пошук оплат для '{patient_name}': {payment_results['total_records']} записів за {payments_time:.3f} сек")
    
    print("✅ Тести швидкості завершено")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️ Імпорт перервано користувачем")
    except Exception as e:
        logger.error(f"❌ Помилка імпорту: {e}")
        import traceback
        traceback.print_exc()

