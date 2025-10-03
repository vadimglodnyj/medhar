#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Імпорт всіх Excel файлів в нормалізовану медичну базу даних
"""

import os
import sys
import time
import logging
import pandas as pd
from datetime import datetime
import re

# Додаємо поточну директорію в шлях
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.medical_database import MedicalDatabase

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('medical_import_log.txt', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class MedicalDataImporter:
    """Імпортер даних в медичну базу даних"""
    
    def __init__(self):
        self.db = MedicalDatabase()
        self.imported_patients = 0
        self.imported_treatments = 0
        self.imported_payments = 0
        self.errors = []
    
    def parse_date(self, date_value):
        """Парсить дату з різних форматів"""
        if pd.isna(date_value) or date_value is None:
            return None
        
        try:
            if isinstance(date_value, str):
                # Спробуємо різні формати дат
                date_formats = ['%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%Y.%m.%d']
                for fmt in date_formats:
                    try:
                        return datetime.strptime(date_value.strip(), fmt).strftime('%Y-%m-%d')
                    except ValueError:
                        continue
                return None
            else:
                # Якщо це pandas Timestamp
                return date_value.strftime('%Y-%m-%d')
        except:
            return None
    
    def parse_treatment_days(self, days_value):
        """Парсить кількість днів лікування"""
        if pd.isna(days_value) or days_value is None:
            return 0.0
        
        try:
            days_str = str(days_value)
            if not days_str or days_str.strip() == '':
                return 0.0
            
            # Знаходимо всі числа в рядку
            numbers = re.findall(r'\d+(?:\.\d+)?', days_str)
            
            if not numbers:
                return 0.0
            
            # Повертаємо найбільше число
            return max(float(num) for num in numbers)
        except (ValueError, TypeError):
            return 0.0
    
    def import_treatments_2025(self):
        """Імпортує дані лікування з treatments_2025.xlsx"""
        file_path = "data/treatments_2025.xlsx"
        
        if not os.path.exists(file_path):
            logger.error(f"Файл не знайдено: {file_path}")
            return False
        
        logger.info(f"Імпорт даних лікування з {file_path}")
        
        try:
            df = pd.read_excel(file_path)
            logger.info(f"Завантажено {len(df)} записів")
            
            for index, row in df.iterrows():
                try:
                    # Створюємо або знаходимо пацієнта
                    patient_id = self.db.find_or_create_patient(
                        surname=self.safe_get(row, 'Прізвище'),
                        first_name=self.safe_get(row, 'Ім\'я'),
                        patronymic=self.safe_get(row, 'По батькові'),
                        phone=self.safe_get(row, 'Номер телефону'),
                        birth_date=self.parse_date(self.safe_get(row, 'Дата народження')),
                        unit_name=self.safe_get(row, 'Підрозділ'),
                        position=self.safe_get(row, 'Посада'),
                        rank=self.safe_get(row, 'Військове звання'),
                        category=self.safe_get(row, 'Категорія')
                    )
                    
                    if not patient_id:
                        continue
                    
                    # Створюємо діагноз
                    diagnosis_id = self.db.create_diagnosis(
                        preliminary=self.safe_get(row, 'Попередній діагноз'),
                        final=self.safe_get(row, 'Заключний діагноз'),
                        circumstances=self.safe_get(row, 'Обставини отримання поранення/ травмування'),
                        clarification=self.safe_get(row, 'Уточнення'),
                        is_combat=self.safe_get(row, 'Бойова/ небойова') == 'бойова',
                        result=self.safe_get(row, 'Результат лікування')
                    )
                    
                    # Створюємо лікування
                    treatment_data = {
                        'injury_date': self.parse_date(self.safe_get(row, 'Дата отримання поранення (контузії, травми, каліцтва)')),
                        'primary_hospitalization_date': self.parse_date(self.safe_get(row, 'Дата первинної госпіталізації')),
                        'current_hospital_admission_date': self.parse_date(self.safe_get(row, 'Дата надходження в поточний Л/З')),
                        'discharge_date': self.parse_date(self.safe_get(row, 'Дата виписки')),
                        'ambulatory_date': self.parse_date(self.safe_get(row, 'Дата амбулаторного')),
                        'follow_up_date': self.parse_date(self.safe_get(row, 'Дата обдзвону')),
                        'hospital_place': self.safe_get(row, 'Місце госпіталізації'),
                        'hospital_category': self.safe_get(row, 'Категорія закладу'),
                        'hospital_location': self.safe_get(row, 'Локалізація закладу'),
                        'treatment_type': self.safe_get(row, 'Вид лікування'),
                        'discharge_data': self.safe_get(row, 'Дані по виписці'),
                        'vlk_start_date': self.parse_date(self.safe_get(row, 'Дата початку ВЛК')),
                        'vlk_registration_date': self.safe_get(row, 'Дата реєстрації та номер направлення ВЛК'),
                        'vlk_received_date': self.parse_date(self.safe_get(row, 'Дата отримання ВЛК')),
                        'vlk_conclusion_date': self.safe_get(row, 'Дата та номер висновку ВЛК'),
                        'vlk_issued_by': self.safe_get(row, 'Ким видано рішення ВЛК'),
                        'vlk_conclusion': self.safe_get(row, 'Заключення ВЛК'),
                        'vlk_decision_classification': self.safe_get(row, 'Класифікація рішення ВЛК'),
                        'msek_direction_date': self.safe_get(row, 'Дата та номер направлення на МСЕК'),
                        'msek_decision': self.safe_get(row, 'Рішення МСЕК'),
                        'payment_period_from': self.parse_date(self.safe_get(row, 'Виплачений період з')),
                        'payment_period_to': self.parse_date(self.safe_get(row, 'Виплачений період по')),
                        'payment_note': self.safe_get(row, 'Примітка оплати'),
                        'bed_days': self.safe_get(row, 'Ліжко-дні'),
                        'is_combat': self.safe_get(row, 'Бойова/ небойова') == 'бойова',
                        'needs_prosthetics': self.safe_get(row, 'Потреба протезування') == 'Так',
                        'has_exit_status': self.safe_get(row, 'Exitus') == 'Так',
                        'has_certificate_5': self.safe_get(row, 'наявність довідки №5') == 'Так',
                        'mkh_code': self.safe_get(row, 'Шифр МКХ'),
                        'patient_data': self.safe_get(row, 'Дані по хворим'),
                        'follow_up_note': self.safe_get(row, 'Примітка обдзвону'),
                        'detachment_circumstances': self.safe_get(row, 'Обставини відриву'),
                        'affiliation': self.safe_get(row, 'Приналежність')
                    }
                    
                    self.db.create_treatment(patient_id, diagnosis_id, **treatment_data)
                    self.imported_treatments += 1
                    
                    if index % 100 == 0:
                        logger.info(f"Оброблено {index}/{len(df)} записів")
                
                except Exception as e:
                    error_msg = f"Помилка в рядку {index}: {e}"
                    logger.error(error_msg)
                    self.errors.append(error_msg)
            
            logger.info(f"Імпорт лікувань завершено: {self.imported_treatments} записів")
            return True
            
        except Exception as e:
            logger.error(f"Критична помилка імпорту лікувань: {e}")
            return False
    
    def import_payment_files(self):
        """Імпортує дані оплат з місячних файлів"""
        payment_files = {
            "may": "data/may_2025.xlsx",
            "june": "data/june_2025.xlsx", 
            "july": "data/july_2025.xlsx",
            "august": "data/august_2025.xlsx"
        }
        
        for month, file_path in payment_files.items():
            if not os.path.exists(file_path):
                logger.warning(f"Файл не знайдено: {file_path}")
                continue
            
            logger.info(f"Імпорт оплат з {file_path}")
            
            try:
                df = pd.read_excel(file_path)
                logger.info(f"Завантажено {len(df)} записів з {month}")
                
                for index, row in df.iterrows():
                    try:
                        # Отримуємо ім'я пацієнта
                        patient_name = self.get_patient_name_from_row(row)
                        if not patient_name:
                            continue
                        
                        # Розбиваємо ім'я на частини
                        name_parts = patient_name.split()
                        surname = name_parts[0] if len(name_parts) > 0 else ""
                        first_name = name_parts[1] if len(name_parts) > 1 else ""
                        patronymic = name_parts[2] if len(name_parts) > 2 else None
                        
                        # Знаходимо пацієнта
                        patients = self.db.search_patients({'name': patient_name})
                        if not patients:
                            # Створюємо нового пацієнта
                            patient_id = self.db.find_or_create_patient(
                                surname=surname,
                                first_name=first_name,
                                patronymic=patronymic,
                                unit_name=self.safe_get(row, 'Підрозділ'),
                                rank=self.safe_get(row, 'Військове звання')
                            )
                        else:
                            patient_id = patients[0]['id']
                        
                        if not patient_id:
                            continue
                        
                        # Створюємо оплату
                        payment_data = {
                            'injury_date': self.parse_date(self.safe_get(row, 'Дата отримання поранення (контузії, травми, каліцтва)')),
                            'total_treatment_days': self.parse_treatment_days(self.safe_get(row, 'Сумарна кількість днів лікування')),
                            'payment_date': self.parse_date(self.safe_get(row, 'Дата отримання поранення (контузії, травми, каліцтва)')),
                            'diagnosis': self.safe_get(row, 'Діагноз'),
                            'payment_dates': self.safe_get(row, 'Дати загал'),
                            'raw_data': str(row.to_dict())
                        }
                        
                        self.db.create_payment(
                            patient_id=patient_id,
                            month=month,
                            year=2025,
                            **payment_data
                        )
                        
                        self.imported_payments += 1
                        
                    except Exception as e:
                        error_msg = f"Помилка в рядку {index} файлу {month}: {e}"
                        logger.error(error_msg)
                        self.errors.append(error_msg)
                
                logger.info(f"Імпорт оплат {month} завершено")
                
            except Exception as e:
                logger.error(f"Критична помилка імпорту оплат {month}: {e}")
        
        logger.info(f"Імпорт всіх оплат завершено: {self.imported_payments} записів")
    
    def get_patient_name_from_row(self, row):
        """Отримує ім'я пацієнта з рядка даних оплат"""
        name_columns = ['ПІБ', 'Прізвище, власне ім\'я, по батькові (за наявності)']
        
        for col in name_columns:
            if col in row.index and pd.notna(row[col]):
                name = str(row[col]).strip()
                if name and len(name) > 2:
                    return name
        
        return None
    
    def safe_get(self, row, column, default=None):
        """Безпечно отримує значення з рядка"""
        if column in row.index and pd.notna(row[column]):
            return row[column]
        return default
    
    def run_import(self):
        """Запускає повний імпорт"""
        start_time = time.time()
        
        logger.info("🚀 Початок імпорту в медичну базу даних")
        logger.info("=" * 60)
        
        # Імпортуємо лікування
        if self.import_treatments_2025():
            logger.info("✅ Імпорт лікувань успішний")
        else:
            logger.error("❌ Імпорт лікувань не вдався")
        
        # Імпортуємо оплати
        self.import_payment_files()
        logger.info("✅ Імпорт оплат завершено")
        
        # Показуємо статистику
        stats = self.db.get_database_stats()
        
        logger.info("\n📊 Статистика бази даних:")
        logger.info(f"   Пацієнтів: {stats['patients_count']}")
        logger.info(f"   Лікувань: {stats['treatments_count']}")
        logger.info(f"   Оплат: {stats['payments_count']}")
        logger.info(f"   Підрозділів: {stats['units_count']}")
        logger.info(f"   Розмір БД: {stats['database_size'] / 1024 / 1024:.2f} MB")
        
        total_time = time.time() - start_time
        logger.info(f"\n✅ Імпорт завершено за {total_time:.1f} секунд")
        
        if self.errors:
            logger.warning(f"\n⚠️ Помилки ({len(self.errors)}):")
            for error in self.errors[:10]:  # Показуємо перші 10 помилок
                logger.warning(f"   {error}")

def main():
    """Головна функція"""
    importer = MedicalDataImporter()
    importer.run_import()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️ Імпорт перервано користувачем")
    except Exception as e:
        logger.error(f"❌ Критична помилка: {e}")
        import traceback
        traceback.print_exc()

