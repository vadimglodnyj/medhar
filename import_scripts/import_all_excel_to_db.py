#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для імпорту всіх Excel файлів в SQLite БД
"""

import os
import sys
import pandas as pd
import logging
from datetime import datetime

# Додаємо батьківську папку до шляху
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.new_medical_database import NewMedicalDatabase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def analyze_excel_files():
    """Аналізує всі Excel файли в папці data/"""
    data_dir = "data"
    excel_files = []
    
    for file in os.listdir(data_dir):
        if file.endswith('.xlsx') and not file.startswith('~'):
            file_path = os.path.join(data_dir, file)
            try:
                df = pd.read_excel(file_path)
                excel_files.append({
                    'file': file,
                    'path': file_path,
                    'rows': len(df),
                    'cols': len(df.columns),
                    'columns': list(df.columns)
                })
                logger.info(f"📊 {file}: {len(df)} рядків, {len(df.columns)} колонок")
            except Exception as e:
                logger.error(f"❌ Помилка читання {file}: {e}")
    
    return excel_files

def import_treatments_data():
    """Імпортує дані лікувань з Excel в БД"""
    logger.info("🔄 Починаємо імпорт даних лікувань...")
    
    db = NewMedicalDatabase("database/medical_new.db")
    
    # Основні файли для імпорту
    treatment_files = [
        "data/treatments_2024.xlsx",
        "data/treatments_2025.xlsx", 
        "data/old_treatments.xlsx"
    ]
    
    total_imported = 0
    
    for file_path in treatment_files:
        if not os.path.exists(file_path):
            logger.warning(f"⚠️ Файл не знайдено: {file_path}")
            continue
            
        logger.info(f"📥 Імпортуємо {file_path}...")
        
        try:
            df = pd.read_excel(file_path)
            logger.info(f"📊 Завантажено {len(df)} записів з {file_path}")
            
            # Імпортуємо дані
            result = db.import_excel_data(df, {})
            
            imported = result.get('inserted', 0) + result.get('updated', 0)
            total_imported += imported
            
            logger.info(f"✅ Імпортовано {imported} записів з {file_path}")
            
        except Exception as e:
            logger.error(f"❌ Помилка імпорту {file_path}: {e}")
    
    logger.info(f"🎉 Загалом імпортовано {total_imported} записів")
    return total_imported

def import_personnel_data():
    """Імпортує дані персоналу з Excel в БД"""
    logger.info("🔄 Починаємо імпорт даних персоналу...")
    
    # Шукаємо файли з персоналом
    personnel_files = [
        "data/allsoldiers.xlsx",
        "data/payment.xlsx"
    ]
    
    total_imported = 0
    
    for file_path in personnel_files:
        if not os.path.exists(file_path):
            logger.warning(f"⚠️ Файл не знайдено: {file_path}")
            continue
            
        logger.info(f"📥 Імпортуємо персонал з {file_path}...")
        
        try:
            df = pd.read_excel(file_path)
            logger.info(f"📊 Завантажено {len(df)} записів персоналу")
            
            # Тут потрібно буде додати логіку імпорту персоналу
            # Поки що просто логуємо
            logger.info(f"✅ Персонал з {file_path} готовий до імпорту")
            
        except Exception as e:
            logger.error(f"❌ Помилка імпорту персоналу {file_path}: {e}")
    
    return total_imported

def cleanup_processed_files():
    """Очищає проміжні Excel файли після імпорту"""
    logger.info("🧹 Очищаємо проміжні файли...")
    
    # Файли для видалення (проміжні обробки)
    files_to_remove = [
        "data/treatments_adapted.xlsx",
        "data/treatments_cleaned.xlsx", 
        "data/treatments_final_clean.xlsx",
        "data/treatments_final_correct.xlsx",
        "data/treatments_final_perfect.xlsx",
        "data/treatments_final_selective.xlsx"
    ]
    
    removed_count = 0
    
    for file_path in files_to_remove:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"🗑️ Видалено {file_path}")
                removed_count += 1
            except Exception as e:
                logger.error(f"❌ Помилка видалення {file_path}: {e}")
    
    logger.info(f"✅ Видалено {removed_count} проміжних файлів")

def main():
    """Головна функція імпорту"""
    logger.info("🚀 Починаємо повний імпорт Excel → SQLite БД")
    
    # 1. Аналізуємо файли
    logger.info("📋 Аналізуємо Excel файли...")
    excel_files = analyze_excel_files()
    
    # 2. Імпортуємо дані лікувань
    treatments_imported = import_treatments_data()
    
    # 3. Імпортуємо дані персоналу
    personnel_imported = import_personnel_data()
    
    # 4. Очищаємо проміжні файли
    cleanup_processed_files()
    
    # 5. Підсумок
    logger.info("🎯 ПІДСУМОК ІМПОРТУ:")
    logger.info(f"📊 Проаналізовано Excel файлів: {len(excel_files)}")
    logger.info(f"🏥 Імпортовано лікувань: {treatments_imported}")
    logger.info(f"👥 Імпортовано персоналу: {personnel_imported}")
    logger.info("✅ Імпорт завершено! Тепер можна використовувати SQLite БД замість Excel")

if __name__ == "__main__":
    main()

