#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор для аналізу виплат хворим з пораненнями
"""

import os
import sys
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Додаємо батьківську папку до шляху для імпорту
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.base_generator import BaseDocumentGenerator
from config import *
from utils.database_manager import DatabaseManager

def convert_to_json_serializable(obj):
    """Конвертує об'єкти pandas/numpy в JSON-сумісні типи"""
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif pd.isna(obj):
        return None
    else:
        return obj

def parse_treatment_days(days_value):
    """Парсить кількість днів лікування з очищенням від зайвих символів"""
    if days_value is None:
        return 0
    try:
        # Очищаємо від переносів рядків та зайвих символів
        days_str = str(days_value).strip()
        
        # Якщо є переноси рядків, це означає детальну інформацію про лікування
        if '\n' in days_str or '\r' in days_str:
            # Розбиваємо по рядках
            lines = days_str.replace('\r', '\n').split('\n')
            
            # Шукаємо рядок з загальною кількістю днів
            # Зазвичай це перший рядок або рядок з найбільшим числом
            total_days = 0
            for line in lines:
                line = line.strip()
                if line:
                    # Шукаємо числа в рядку
                    import re
                    numbers = re.findall(r'\d+(?:\.\d+)?', line)
                    if numbers:
                        # Беремо перше число як потенційну загальну кількість днів
                        try:
                            days = float(numbers[0])
                            if days > total_days:  # Беремо найбільше число як загальну кількість
                                total_days = days
                        except (ValueError, TypeError):
                            continue
            
            return total_days
        else:
            # Якщо немає переносів рядків, просто парсимо число
            days_str = days_str.replace('\n', ' ').replace('\r', ' ')
            days_str = days_str.split()[0] if days_str.split() else '0'
            return float(days_str)
    except (ValueError, TypeError, IndexError):
        return 0

logger = logging.getLogger(__name__)

class PaymentAnalyzer:
    """Аналізатор виплат хворим з пораненнями"""
    
    def __init__(self):
        self.data_dir = Path("data")
        self.treatments_data = None
        self.payment_data = None
        self.monthly_data = {}
        self.db_manager = None
        self.use_database = True  # Флаг для використання БД
        
    def init_database(self):
        """Ініціалізує базу даних"""
        if self.use_database and self.db_manager is None:
            try:
                self.db_manager = DatabaseManager()
                logger.info("База даних ініціалізована")
                return True
            except Exception as e:
                logger.error(f"Помилка ініціалізації БД: {e}")
                self.use_database = False
                return False
        return self.db_manager is not None
    
    def load_all_data(self):
        """Завантажує всі необхідні дані"""
        # Спочатку намагаємося використати БД
        if self.init_database():
            logger.info("Використовуємо базу даних для швидкого доступу")
            return True
        
        # Якщо БД недоступна, використовуємо Excel файли
        logger.info("Використовуємо Excel файли")
        try:
            # Завантажуємо основні дані лікування
            if os.path.exists(TREATMENTS_2025_FILE):
                self.treatments_data = pd.read_excel(TREATMENTS_2025_FILE)
                logger.info(f"Завантажено даних лікування: {len(self.treatments_data)} записів")
            else:
                logger.warning(f"Файл {TREATMENTS_2025_FILE} не знайдено")
                
            # Завантажуємо дані виплат
            payment_file = self.data_dir / "payment.xlsx"
            if payment_file.exists():
                self.payment_data = pd.read_excel(payment_file)
                logger.info(f"Завантажено даних виплат: {len(self.payment_data)} записів")
            else:
                logger.warning("Файл payment.xlsx не знайдено")
                
            # Завантажуємо місячні дані
            monthly_files = ["may_2025.xlsx", "june_2025.xlsx", "july_2025.xlsx", "august_2025.xlsx"]
            for filename in monthly_files:
                filepath = self.data_dir / filename
                if filepath.exists():
                    try:
                        df = pd.read_excel(filepath)
                        
                        # Видаляємо рядки з некоректними даними (nan, числа в стовпці з іменами)
                        name_columns = ['ПІБ', 'Прізвище, власне ім\'я, по батькові (за наявності)']
                        name_column = None
                        for col in name_columns:
                            if col in df.columns:
                                name_column = col
                                break
                        
                        if name_column:
                            # Видаляємо рядки де ім'я є NaN або число
                            df = df.dropna(subset=[name_column])
                            df = df[~df[name_column].astype(str).str.match(r'^\d+$')]
                            df = df[df[name_column].astype(str).str.len() > 2]  # Імена довші за 2 символи
                        
                        month_name = filename.replace("_2025.xlsx", "")
                        self.monthly_data[month_name] = df
                        logger.info(f"Завантажено {month_name}: {len(df)} записів (після очищення)")
                    except Exception as e:
                        logger.error(f"Помилка завантаження {filename}: {e}")
                        
            return True
            
        except Exception as e:
            logger.error(f"Помилка завантаження даних: {e}")
            return False
    
    def analyze_payments_by_unit(self, target_units=None):
        """Аналізує виплати за підрозділами"""
        if target_units is None:
            target_units = ["2 БОП", "6 БОП"]  # За замовчуванням
        
        results = {}
        
        for unit in target_units:
            logger.info(f"Аналіз виплат для підрозділу: {unit}")
            
            # Аналізуємо дані лікування для цього підрозділу
            unit_treatments = self._get_unit_treatments(unit)
            
            # Аналізуємо дані виплат
            unit_payments = self._get_unit_payments(unit)
            
            # Об'єднуємо дані
            combined_data = self._combine_treatment_payment_data(unit_treatments, unit_payments)
            
            results[unit] = {
                'treatments_count': len(unit_treatments),
                'payments_count': len(unit_payments),
                'combined_data': combined_data,
                'analysis_summary': self._generate_analysis_summary(combined_data)
            }
            
        return results
    
    def _get_unit_treatments(self, unit):
        """Отримує дані лікування для конкретного підрозділу"""
        if self.treatments_data is None:
            return pd.DataFrame()
            
        # Фільтруємо за підрозділом
        unit_data = self.treatments_data[
            self.treatments_data['Підрозділ'].str.contains(unit, case=False, na=False)
        ].copy()
        
        # Обробляємо дати
        date_columns = ['Дата надходження в поточний Л/З', 'Дата виписки', 'Дата народження']
        for col in date_columns:
            if col in unit_data.columns:
                unit_data[col] = pd.to_datetime(unit_data[col], errors='coerce')
        
        # Додаємо ПІБ
        unit_data['ПІБ'] = (
            unit_data['Прізвище'].fillna('').astype(str) + ' ' +
            unit_data['Ім\'я'].fillna('').astype(str) + ' ' +
            unit_data['По батькові'].fillna('').astype(str)
        )
        unit_data['ПІБ_чисте'] = unit_data['ПІБ'].str.replace(r'\s+', ' ', regex=True).str.strip().str.lower()
        
        return unit_data
    
    def _get_unit_payments(self, unit):
        """Отримує дані виплат для конкретного підрозділу"""
        if self.payment_data is None:
            return pd.DataFrame()
            
        # Фільтруємо за підрозділом
        unit_payments = self.payment_data[
            self.payment_data['Підрозділ'].str.contains(unit, case=False, na=False)
        ].copy()
        
        # Обробляємо дати
        date_columns = ['Дата отримання поранення (контузії, травми, каліцтва)']
        for col in date_columns:
            if col in unit_payments.columns:
                unit_payments[col] = pd.to_datetime(unit_payments[col], errors='coerce')
        
        return unit_payments
    
    def _combine_treatment_payment_data(self, treatments, payments):
        """Об'єднує дані лікування та виплат"""
        if treatments.empty and payments.empty:
            return pd.DataFrame()
            
        # Якщо є тільки дані лікування
        if payments.empty:
            return treatments
            
        # Якщо є тільки дані виплат
        if treatments.empty:
            return payments
            
        # Об'єднуємо за ПІБ
        combined = pd.merge(
            treatments, 
            payments, 
            on='ПІБ', 
            how='outer', 
            suffixes=('_treatment', '_payment')
        )
        
        return combined
    
    def _generate_analysis_summary(self, data):
        """Генерує підсумок аналізу"""
        if data.empty:
            return {
                'total_patients': 0,
                'total_treatment_days': 0,
                'avg_treatment_days': 0,
                'injured_patients': 0,
                'most_common_diagnoses': [],
                'treatment_periods': []
            }
        
        # Підраховуємо загальну кількість пацієнтів
        total_patients = 0
        name_columns = ['ПІБ', 'Прізвище, власне ім\'я, по батькові (за наявності)']
        name_column = None
        
        for col in name_columns:
            if col in data.columns:
                name_column = col
                break
        
        # Додаткова перевірка - шукаємо стовпці з "Прізвище"
        if not name_column:
            for col in data.columns:
                if 'Прізвище' in str(col):
                    name_column = col
                    break
        
        if name_column:
            total_patients = data[name_column].nunique()
        
        summary = {
            'total_patients': convert_to_json_serializable(total_patients),
            'total_treatment_days': 0,
            'avg_treatment_days': 0,
            'injured_patients': 0,
            'most_common_diagnoses': [],
            'treatment_periods': []
        }
        
        # Аналізуємо дні лікування
        if 'Сумарна кількість днів лікування' in data.columns:
            treatment_days = pd.to_numeric(data['Сумарна кількість днів лікування'], errors='coerce')
            total_days = treatment_days.sum()
            avg_days = treatment_days.mean()
            
            # Перевіряємо на NaN та замінюємо на 0
            summary['total_treatment_days'] = convert_to_json_serializable(int(total_days) if not pd.isna(total_days) else 0)
            summary['avg_treatment_days'] = convert_to_json_serializable(round(avg_days, 1) if not pd.isna(avg_days) else 0)
        
        # Аналізуємо поранених
        if 'Бойова/ небойова' in data.columns:
            injured_mask = data['Бойова/ небойова'].str.contains('бойова', case=False, na=False)
            injured_count = injured_mask.sum()
            summary['injured_patients'] = convert_to_json_serializable(int(injured_count) if not pd.isna(injured_count) else 0)
        
        # Найпоширеніші діагнози
        if 'Попередній діагноз' in data.columns:
            diagnoses = data['Попередній діагноз'].value_counts().head(5)
            # Перетворюємо в звичайний словник та очищаємо від NaN
            diagnoses_dict = {}
            for key, value in diagnoses.items():
                if not pd.isna(key) and not pd.isna(value):
                    diagnoses_dict[str(key)] = int(value)
            summary['most_common_diagnoses'] = diagnoses_dict
        
        return summary
    
    def generate_payment_report(self, target_units=None, output_format='excel'):
        """Генерує звіт по виплатах"""
        if not self.load_all_data():
            raise Exception("Не вдалося завантажити дані")
        
        analysis_results = self.analyze_payments_by_unit(target_units)
        
        if output_format == 'excel':
            return self._generate_excel_report(analysis_results)
        else:
            # Для JSON формату перетворюємо DataFrame в словники
            json_results = {}
            for unit, data in analysis_results.items():
                json_results[unit] = {
                    'treatments_count': data['treatments_count'],
                    'payments_count': data['payments_count'],
                    'analysis_summary': data['analysis_summary']
                    # Не включаємо combined_data, оскільки це DataFrame
                }
            return json_results
    
    def _generate_excel_report(self, analysis_results):
        """Генерує Excel звіт"""
        output_file = self.data_dir / f"payment_analysis_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            # Створюємо звіт для кожного підрозділу
            for unit, data in analysis_results.items():
                if not data['combined_data'].empty:
                    # Основні дані
                    data['combined_data'].to_excel(
                        writer, 
                        sheet_name=f'{unit}_Дані', 
                        index=False
                    )
                    
                    # Підсумкова інформація
                    summary_df = pd.DataFrame([
                        ['Загальна кількість пацієнтів', data['analysis_summary']['total_patients']],
                        ['Загальна кількість днів лікування', data['analysis_summary']['total_treatment_days']],
                        ['Середня кількість днів лікування', round(data['analysis_summary']['avg_treatment_days'], 1)],
                        ['Кількість поранених', data['analysis_summary']['injured_patients']]
                    ], columns=['Показник', 'Значення'])
                    
                    summary_df.to_excel(
                        writer, 
                        sheet_name=f'{unit}_Підсумок', 
                        index=False
                    )
        
        logger.info(f"Звіт збережено: {output_file}")
        return output_file
    
    def search_patient_payment_history(self, patient_name):
        """Пошук історії виплат для конкретного пацієнта"""
        if not self.load_all_data():
            return {}
        
        patient_name_clean = patient_name.strip().lower()
        results = {}
        
        # Змінні для підрахунку загальної статистики
        total_paid_days = 0
        payment_count = 0
        last_payment_date = None
        all_payment_records = []
        treatment_records = []
        
        # Пошук в основних даних виплат
        if self.payment_data is not None:
            # Перевіряємо різні можливі назви стовпців
            name_columns = ['ПІБ', 'Прізвище, власне ім\'я, по батькові (за наявності)']
            name_column = None
            
            for col in name_columns:
                if col in self.payment_data.columns:
                    name_column = col
                    break
            
            if name_column:
                payment_matches = self.payment_data[
                    self.payment_data[name_column].str.contains(
                        patient_name_clean, case=False, na=False
                    )
                ]
                if not payment_matches.empty:
                    # Конвертуємо DataFrame в список словників з обробкою NaN
                    records = []
                    for _, row in payment_matches.iterrows():
                        record = {}
                        for col, value in row.items():
                            record[col] = convert_to_json_serializable(value)
                        records.append(record)
                        all_payment_records.append(record)
                        
                        # Підраховуємо дні лікування
                        if 'Сумарна кількість днів лікування' in record:
                            try:
                                days = parse_treatment_days(record['Сумарна кількість днів лікування'])
                                total_paid_days += days
                                payment_count += 1
                            except (ValueError, TypeError):
                                pass
                        
                        # Перевіряємо дату оплати
                        date_col = 'Дата отримання поранення (контузії, травми, каліцтва)'
                        if date_col in record and record[date_col]:
                            # Перевіряємо чи дата є datetime об'єктом
                            if isinstance(record[date_col], str):
                                try:
                                    # Спробуємо конвертувати рядок в дату
                                    date_obj = pd.to_datetime(record[date_col])
                                    if last_payment_date is None or date_obj > last_payment_date:
                                        last_payment_date = date_obj
                                except:
                                    # Якщо не вдалося конвертувати, пропускаємо
                                    pass
                            else:
                                # Якщо це вже datetime об'єкт
                                if last_payment_date is None or record[date_col] > last_payment_date:
                                    last_payment_date = record[date_col]
                    
                    results['main_payments'] = records
        
        # Пошук в даних лікування
        if self.treatments_data is not None:
            # У даних лікування імена розділені на окремі стовпці
            name_column = None
            
            # Перевіряємо чи є окремі стовпці для імен
            if 'Прізвище' in self.treatments_data.columns and 'Ім\'я' in self.treatments_data.columns:
                # Комбінуємо прізвище, ім'я та по батькові
                if 'ПІБ_combined' not in self.treatments_data.columns:
                    self.treatments_data['ПІБ_combined'] = (
                        self.treatments_data['Прізвище'].astype(str) + ' ' +
                        self.treatments_data['Ім\'я'].astype(str) + ' ' +
                        self.treatments_data['По батькові'].fillna('').astype(str)
                    ).str.strip()
                name_column = 'ПІБ_combined'
            else:
                # Шукаємо готовий стовпець з ПІБ
                name_columns = ['ПІБ', 'Прізвище, власне ім\'я, по батькові (за наявності)']
                for col in name_columns:
                    if col in self.treatments_data.columns:
                        name_column = col
                        break
                
                # Додаткова перевірка - шукаємо стовпці з "Прізвище"
                if not name_column:
                    for col in self.treatments_data.columns:
                        if 'Прізвище' in str(col):
                            name_column = col
                            break
            
            if name_column:
                treatment_matches = self.treatments_data[
                    self.treatments_data[name_column].str.contains(
                        patient_name_clean, case=False, na=False
                    )
                ]
                
                if not treatment_matches.empty:
                    # Конвертуємо DataFrame в список словників з обробкою NaN
                    records = []
                    for _, row in treatment_matches.iterrows():
                        record = {}
                        for col, value in row.items():
                            record[col] = convert_to_json_serializable(value)
                        records.append(record)
                        treatment_records.append(record)
                    
                    results['treatment_data'] = records
        
        # Пошук в місячних даних
        for month, data in self.monthly_data.items():
            # Перевіряємо різні можливі назви стовпців
            name_columns = ['ПІБ', 'Прізвище, власне ім\'я, по батькові (за наявності)']
            name_column = None
            
            for col in name_columns:
                if col in data.columns:
                    name_column = col
                    break
            
            # Додаткова перевірка - шукаємо стовпці з "Прізвище"
            if not name_column:
                for col in data.columns:
                    if 'Прізвище' in str(col):
                        name_column = col
                        break
            
            if name_column:
                month_matches = data[
                    data[name_column].str.contains(
                        patient_name_clean, case=False, na=False
                    )
                ]
                if not month_matches.empty:
                    # Конвертуємо DataFrame в список словників з обробкою NaN
                    records = []
                    for _, row in month_matches.iterrows():
                        record = {}
                        for col, value in row.items():
                            record[col] = convert_to_json_serializable(value)
                        records.append(record)
                        all_payment_records.append(record)
                        
                        # Підраховуємо дні лікування
                        if 'Сумарна кількість днів лікування' in record:
                            try:
                                days = parse_treatment_days(record['Сумарна кількість днів лікування'])
                                total_paid_days += days
                                payment_count += 1
                            except (ValueError, TypeError):
                                pass
                        
                        # Перевіряємо дату оплати
                        date_col = 'Дата отримання поранення (контузії, травми, каліцтва)'
                        if date_col in record and record[date_col]:
                            # Перевіряємо чи дата є datetime об'єктом
                            if isinstance(record[date_col], str):
                                try:
                                    # Спробуємо конвертувати рядок в дату
                                    date_obj = pd.to_datetime(record[date_col])
                                    if last_payment_date is None or date_obj > last_payment_date:
                                        last_payment_date = date_obj
                                except:
                                    # Якщо не вдалося конвертувати, пропускаємо
                                    pass
                            else:
                                # Якщо це вже datetime об'єкт
                                if last_payment_date is None or record[date_col] > last_payment_date:
                                    last_payment_date = record[date_col]
                    
                    results[f'{month}_payments'] = records
        
        # Додаємо загальну статистику, якщо знайдені записи
        if all_payment_records or treatment_records:
            # Знаходимо дати початку та закінчення лікування
            treatment_start_date = None
            treatment_end_date = None
            treatment_location = None
            
            if treatment_records:
                # Шукаємо дати лікування в даних лікування
                for record in treatment_records:
                    # Шукаємо стовпці з датами
                    for col in record.keys():
                        if 'початок' in col.lower() or 'початку' in col.lower():
                            if record[col] and (treatment_start_date is None or record[col] < treatment_start_date):
                                treatment_start_date = record[col]
                        elif 'кінець' in col.lower() or 'закінчен' in col.lower():
                            if record[col] and (treatment_end_date is None or record[col] > treatment_end_date):
                                treatment_end_date = record[col]
                        elif 'місце' in col.lower() or 'заклад' in col.lower() or 'лікарня' in col.lower():
                            if record[col] and not treatment_location:
                                treatment_location = record[col]
            
            results['payment_summary'] = {
                'total_paid_days': round(total_paid_days, 1),
                'payment_count': payment_count,
                'last_payment_date': last_payment_date,
                'total_records': len(all_payment_records),
                'treatment_start_date': treatment_start_date,
                'treatment_end_date': treatment_end_date,
                'treatment_location': treatment_location,
                'has_treatment_data': len(treatment_records) > 0
            }
        
        return results
    
    def compare_treatments_with_payments(self):
        """Порівнює дані лікування з оплатами з травня місяця"""
        if not self.load_all_data():
            return {}
        
        logger.info("Порівняння даних лікування з оплатами")
        
        results = {
            'total_treatments': 0,
            'paid_treatments': 0,
            'unpaid_treatments': 0,
            'missing_payments': [],
            'comparison_summary': {}
        }
        
        if self.treatments_data is None:
            logger.warning("Немає даних лікування для порівняння")
            return results
        
        # Знаходимо стовпець з іменами в даних лікування
        # У даних лікування імена розділені на окремі стовпці
        name_column = None
        
        # Перевіряємо чи є окремі стовпці для імен
        if 'Прізвище' in self.treatments_data.columns and 'Ім\'я' in self.treatments_data.columns:
            # Комбінуємо прізвище, ім'я та по батькові
            self.treatments_data['ПІБ_combined'] = (
                self.treatments_data['Прізвище'].astype(str) + ' ' +
                self.treatments_data['Ім\'я'].astype(str) + ' ' +
                self.treatments_data['По батькові'].fillna('').astype(str)
            ).str.strip()
            name_column = 'ПІБ_combined'
        else:
            # Шукаємо готовий стовпець з ПІБ
            name_columns = ['ПІБ', 'Прізвище, власне ім\'я, по батькові (за наявності)']
            for col in name_columns:
                if col in self.treatments_data.columns:
                    name_column = col
                    break
            
            # Додаткова перевірка - шукаємо стовпці з "Прізвище"
            if not name_column:
                for col in self.treatments_data.columns:
                    if 'Прізвище' in str(col):
                        name_column = col
                        break
        
        if not name_column:
            logger.error("Не знайдено стовпець з іменами в даних лікування")
            return results
        
        # Отримуємо список всіх пацієнтів з лікування
        treatment_patients = set()
        treatment_records = []
        
        for _, row in self.treatments_data.iterrows():
            patient_name = row[name_column]
            if pd.notna(patient_name) and str(patient_name).strip():
                patient_name_clean = str(patient_name).strip().lower()
                treatment_patients.add(patient_name_clean)
                
                # Зберігаємо запис для подальшого аналізу
                record = {}
                for col, value in row.items():
                    record[col] = convert_to_json_serializable(value)
                treatment_records.append({
                    'patient_name': patient_name,
                    'patient_name_clean': patient_name_clean,
                    'data': record
                })
        
        results['total_treatments'] = len(treatment_patients)
        
        # Збираємо всіх пацієнтів з оплатами з травня
        paid_patients = set()
        
        # Перевіряємо місячні дані (травень та пізніше)
        monthly_files = ['may', 'june', 'july', 'august']
        
        for month in monthly_files:
            if month in self.monthly_data:
                data = self.monthly_data[month]
                
                # Знаходимо стовпець з іменами
                name_columns = ['ПІБ', 'Прізвище, власне ім\'я, по батькові (за наявності)']
                name_column_payment = None
                for col in name_columns:
                    if col in data.columns:
                        name_column_payment = col
                        break
                
                if not name_column_payment:
                    for col in data.columns:
                        if 'Прізвище' in str(col):
                            name_column_payment = col
                            break
                
                if name_column_payment:
                    for _, row in data.iterrows():
                        patient_name = row[name_column_payment]
                        if pd.notna(patient_name) and str(patient_name).strip():
                            patient_name_clean = str(patient_name).strip().lower()
                            paid_patients.add(patient_name_clean)
        
        # Знаходимо пацієнтів без оплат
        unpaid_patients = treatment_patients - paid_patients
        results['unpaid_treatments'] = len(unpaid_patients)
        results['paid_treatments'] = len(treatment_patients - unpaid_patients)
        
        # Створюємо список записів без оплат
        for record in treatment_records:
            if record['patient_name_clean'] in unpaid_patients:
                results['missing_payments'].append({
                    'patient_name': record['patient_name'],
                    'data': record['data']
                })
        
        # Створюємо підсумок по підрозділах
        if results['missing_payments']:
            unit_summary = {}
            for record in results['missing_payments']:
                unit = record['data'].get('Підрозділ', 'Невідомий')
                if unit not in unit_summary:
                    unit_summary[unit] = 0
                unit_summary[unit] += 1
            
            results['comparison_summary'] = {
                'by_unit': unit_summary,
                'percentage_paid': round((results['paid_treatments'] / results['total_treatments']) * 100, 1) if results['total_treatments'] > 0 else 0,
                'percentage_unpaid': round((results['unpaid_treatments'] / results['total_treatments']) * 100, 1) if results['total_treatments'] > 0 else 0
            }
        
        logger.info(f"Порівняння завершено: {results['paid_treatments']}/{results['total_treatments']} пацієнтів мають оплати")
        
        return results
    
    def advanced_search_treatments(self, search_criteria):
        """Розширений пошук в даних лікування по критеріях"""
        if not self.load_all_data():
            return {}
        
        logger.info(f"Розширений пошук з критеріями: {search_criteria}")
        
        # Якщо використовуємо БД, використовуємо швидкий пошук
        if self.use_database and self.db_manager:
            return self.advanced_search_treatments_db(search_criteria)
        
        # Інакше використовуємо старий метод з Excel
        if self.treatments_data is None:
            logger.warning("Немає даних лікування для пошуку")
            return {}
        
        # Ключові слова для діагнозів поранень
        injury_keywords = [
            'ВОСП', 'ВТ', 'МВТ', 'наслідки МВТ', 'наслідки ВОСП',
            'огнепальне поранення', 'осколкове поранення', 'контузія',
            'травма', 'поранення', 'каліцтво', 'опік', 'обмороження',
            'комбіноване поранення', 'множинне поранення', 'пневмоторакс',
            'гемоторакс', 'черепно-мозкова травма', 'ЧМТ', 'спинальна травма',
            'перелом', 'вивих', 'розтягнення', 'розрив', 'ампутація',
            'втрата кінцівки', 'порушення слуху', 'порушення зору',
            'посттравматичний стресовий розлад', 'ПТСР'
        ]
        
        # Підрозділи 2 БОП
        unit_2bop = [
            '1 РОП 2 БОП', '2 РОП 2 БОП', '3 РОП 2 БОП',
            'ВІ 2 БОП', 'ВЗ 2 БОП', 'ВМТЗ 2 БОП', 'ВТО 2 БОП',
            'ВБК СП 2 БОП', 'ВРСП 2 БОП', 'МБ(120 мм) 2 БОП',
            'МБ(60 мм(82 мм)) 2 БОП', 'ІСВ 2 БОП', 'РВП 2 БОП',
            'МП 2 БОП', 'Штаб 2 БОП'
        ]
        
        # Виключаємо типи лікування, які не оплачуються
        excluded_treatment_types = [
            'Стабілізаційний пункт',
            'Амбулаторно', 
            'Лазарет',
            'ВЛК'
        ]
        
        # Виключаємо місця госпіталізації, які не оплачуються
        excluded_hospital_places = [
            'медична рота 3029',
            'Медична рота 3029',
            'Медична рота 3029 (ОТУ Харків)',
            'Медична рота 3029 (ОТУ Донецьк)',
            'Медичний пункт бригади в/ч 3029'
        ]
        
        # Створюємо комбінований стовпець з іменами якщо потрібно
        if 'Прізвище' in self.treatments_data.columns and 'Ім\'я' in self.treatments_data.columns:
            if 'ПІБ_combined' not in self.treatments_data.columns:
                self.treatments_data['ПІБ_combined'] = (
                    self.treatments_data['Прізвище'].astype(str) + ' ' +
                    self.treatments_data['Ім\'я'].astype(str) + ' ' +
                    self.treatments_data['По батькові'].fillna('').astype(str)
                ).str.strip()
            name_column = 'ПІБ_combined'
        else:
            name_column = 'ПІБ'
        
        # Починаємо з усіх даних
        filtered_data = self.treatments_data.copy()
        
        # Фільтр 1: Тільки бойові/небойові (якщо вказано)
        if search_criteria.get('combat_status'):
            combat_status = search_criteria['combat_status']
            if 'Бойова/ небойова' in filtered_data.columns:
                if combat_status == 'бойова':
                    filtered_data = filtered_data[
                        filtered_data['Бойова/ небойова'].str.contains('бойова', case=False, na=False)
                    ]
                elif combat_status == 'небойова':
                    filtered_data = filtered_data[
                        filtered_data['Бойова/ небойова'].str.contains('небойова', case=False, na=False)
                    ]
        
        # Фільтр 2: Тільки підрозділи 2 БОП (якщо вказано)
        if search_criteria.get('unit_filter', True):  # За замовчуванням True
            if 'Підрозділ' in filtered_data.columns:
                unit_mask = filtered_data['Підрозділ'].isin(unit_2bop)
                filtered_data = filtered_data[unit_mask]
        
        # Фільтр 2.1: Виключаємо типи лікування, які не оплачуються
        if 'Вид лікування' in filtered_data.columns:
            treatment_mask = ~filtered_data['Вид лікування'].isin(excluded_treatment_types)
            filtered_data = filtered_data[treatment_mask]
        
        # Фільтр 2.2: Виключаємо місця госпіталізації, які не оплачуються
        if 'Місце госпіталізації' in filtered_data.columns:
            hospital_mask = ~filtered_data['Місце госпіталізації'].isin(excluded_hospital_places)
            filtered_data = filtered_data[hospital_mask]
        
        # Фільтр 3: По ключових словах в діагнозі (якщо вказано)
        if search_criteria.get('diagnosis_keywords', True):  # За замовчуванням True
            diagnosis_columns = ['Попередній діагноз', 'Заключний діагноз', 'Діагноз']
            diagnosis_mask = pd.Series([False] * len(filtered_data), index=filtered_data.index)
            
            for col in diagnosis_columns:
                if col in filtered_data.columns:
                    for keyword in injury_keywords:
                        mask = filtered_data[col].astype(str).str.contains(
                            keyword, case=False, na=False
                        )
                        diagnosis_mask = diagnosis_mask | mask
            
            filtered_data = filtered_data[diagnosis_mask]
        
        # Фільтр 4: Пошук по імені (якщо вказано)
        if search_criteria.get('patient_name'):
            patient_name = search_criteria['patient_name'].strip().lower()
            if name_column in filtered_data.columns:
                name_mask = filtered_data[name_column].astype(str).str.contains(
                    patient_name, case=False, na=False
                )
                filtered_data = filtered_data[name_mask]
        
        # Підготовка результатів
        results = {
            'total_found': len(filtered_data),
            'search_criteria': search_criteria,
            'patients': []
        }
        
        # Конвертуємо результат в список словників
        for _, row in filtered_data.iterrows():
            patient_record = {}
            for col, value in row.items():
                patient_record[col] = convert_to_json_serializable(value)
            
            results['patients'].append({
                'patient_name': patient_record.get(name_column, 'Невідомий'),
                'data': patient_record
            })
        
        # Додаємо статистику по підрозділах
        if 'Підрозділ' in filtered_data.columns:
            unit_stats = filtered_data['Підрозділ'].value_counts().to_dict()
            results['unit_statistics'] = unit_stats
        
        # Додаємо статистику по бойовому статусі
        if 'Бойова/ небойова' in filtered_data.columns:
            combat_stats = filtered_data['Бойова/ небойова'].value_counts().to_dict()
            results['combat_statistics'] = combat_stats
        
        logger.info(f"Знайдено {results['total_found']} пацієнтів відповідно до критеріїв")
        
        # Додаємо перевірку оплат для всіх знайдених пацієнтів
        if search_criteria.get('check_payments', False):
            payment_check_results = self.check_payments_for_patients(results['patients'])
            results['payment_verification'] = payment_check_results
        
        return results
    
    def check_payments_for_patients(self, patients_list):
        """Перевіряє оплати для списку пацієнтів"""
        if not self.load_all_data():
            return {}
        
        logger.info(f"Перевірка оплат для {len(patients_list)} пацієнтів")
        
        results = {
            'total_patients': len(patients_list),
            'patients_with_payments': 0,
            'patients_without_payments': 0,
            'patient_details': []
        }
        
        for patient in patients_list:
            patient_name = patient.get('patient_name', '')
            patient_data = patient.get('data', {})
            
            # Перевіряємо оплати для цього пацієнта
            payment_results = self.search_patient_payment_history(patient_name)
            
            has_payments = False
            payment_summary = {}
            
            if payment_results and payment_results.get('payment_summary'):
                payment_summary = payment_results['payment_summary']
                has_payments = payment_summary.get('total_records', 0) > 0
                
                if has_payments:
                    results['patients_with_payments'] += 1
                else:
                    results['patients_without_payments'] += 1
            else:
                results['patients_without_payments'] += 1
            
            # Додаємо детальну інформацію про пацієнта
            patient_detail = {
                'patient_name': patient_name,
                'unit': patient_data.get('Підрозділ', 'Невідомий'),
                'rank': patient_data.get('Військове звання', 'Невідомий'),
                'combat_status': patient_data.get('Бойова/ небойова', 'Невідомий'),
                'diagnosis': patient_data.get('Попередній діагноз', 'Невідомий'),
                'treatment_type': patient_data.get('Вид лікування', 'Невідомий'),
                'hospital_place': patient_data.get('Місце госпіталізації', 'Невідомий'),
                'has_payments': has_payments,
                'payment_summary': payment_summary
            }
            
            results['patient_details'].append(patient_detail)
        
        logger.info(f"Перевірка завершена: {results['patients_with_payments']} з оплатами, {results['patients_without_payments']} без оплат")
        
        return results


class PaymentReportGenerator(BaseDocumentGenerator):
    """Генератор звітів по виплатах"""
    
    def __init__(self):
        template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                   'templates', 'payment_report_template.docx')
        super().__init__(template_path)
        self.analyzer = PaymentAnalyzer()
    
    def prepare_data(self, form_data):
        """Підготовка даних для звіту по виплатах"""
        target_units = form_data.get('target_units', ['2 БОП', '6 БОП'])
        if isinstance(target_units, str):
            target_units = [unit.strip() for unit in target_units.split(',')]
        
        # Завантажуємо та аналізуємо дані
        if not self.analyzer.load_all_data():
            raise Exception("Не вдалося завантажити дані для аналізу")
        
        analysis_results = self.analyzer.analyze_payments_by_unit(target_units)
        
        # Підготовка контексту для шаблону
        context = {
            'report_date': datetime.now().strftime('%d.%m.%Y'),
            'target_units': ', '.join(target_units),
            'units_data': {}
        }
        
        for unit, data in analysis_results.items():
            context['units_data'][unit] = {
                'total_patients': data['analysis_summary']['total_patients'],
                'total_treatment_days': data['analysis_summary']['total_treatment_days'],
                'avg_treatment_days': round(data['analysis_summary']['avg_treatment_days'], 1),
                'injured_patients': data['analysis_summary']['injured_patients'],
                'most_common_diagnoses': data['analysis_summary']['most_common_diagnoses']
            }
        
        return context
    
    def validate_data(self, form_data):
        """Валідація даних для звіту по виплатах"""
        # Базова валідація
        is_valid, error = super().validate_data(form_data)
        if not is_valid:
            return is_valid, error
        
        # Додаткова валідація для звітів по виплатах
        target_units = form_data.get('target_units', '').strip()
        if not target_units:
            return False, "Необхідно вказати цільові підрозділи"
        
        return True, None
    
    def advanced_search_treatments_db(self, search_criteria):
        """Розширений пошук в БД"""
        try:
            results = self.db_manager.search_treatments(search_criteria)
            
            # Конвертуємо результати в потрібний формат
            patients = []
            for row in results:
                patients.append({
                    'patient_name': row['full_name'],
                    'data': {
                        'Підрозділ': row['unit'],
                        'Військове звання': row['rank'],
                        'Бойова/ небойова': row['combat_status'],
                        'Попередній діагноз': row['preliminary_diagnosis'],
                        'Заключний діагноз': row['final_diagnosis'],
                        'Дата первинної госпіталізації': row['treatment_start_date'],
                        'Місце госпіталізації': row['hospital_place'],
                        'Вид лікування': row['treatment_type']
                    }
                })
            
            result = {
                'total_found': len(patients),
                'patients': patients,
                'search_criteria': search_criteria
            }
            
            # Додаємо перевірку оплат якщо потрібно
            if search_criteria.get('check_payments', False):
                payment_check_results = self.check_payments_for_patients_db(patients)
                result['payment_verification'] = payment_check_results
            
            return result
            
        except Exception as e:
            logger.error(f"Помилка пошуку в БД: {e}")
            return {'error': str(e)}
    
    def check_payments_for_patients_db(self, patients_list):
        """Перевіряє оплати для списку пацієнтів через БД"""
        if not self.db_manager:
            return {}
        
        logger.info(f"Перевірка оплат для {len(patients_list)} пацієнтів через БД")
        
        results = {
            'total_patients': len(patients_list),
            'patients_with_payments': 0,
            'patients_without_payments': 0,
            'patient_details': []
        }
        
        for patient in patients_list:
            patient_name = patient.get('patient_name', '')
            patient_data = patient.get('data', {})
            
            # Перевіряємо оплати через БД
            payment_results = self.db_manager.search_payments(patient_name)
            
            has_payments = payment_results.get('total_records', 0) > 0
            
            if has_payments:
                results['patients_with_payments'] += 1
            else:
                results['patients_without_payments'] += 1
            
            # Додаємо детальну інформацію про пацієнта
            patient_detail = {
                'patient_name': patient_name,
                'unit': patient_data.get('Підрозділ', 'Невідомий'),
                'rank': patient_data.get('Військове звання', 'Невідомий'),
                'combat_status': patient_data.get('Бойова/ небойова', 'Невідомий'),
                'diagnosis': patient_data.get('Попередній діагноз', 'Невідомий'),
                'treatment_type': patient_data.get('Вид лікування', 'Невідомий'),
                'hospital_place': patient_data.get('Місце госпіталізації', 'Невідомий'),
                'has_payments': has_payments,
                'payment_summary': payment_results if has_payments else {}
            }
            
            results['patient_details'].append(patient_detail)
        
        logger.info(f"Перевірка завершена: {results['patients_with_payments']} з оплатами, {results['patients_without_payments']} без оплат")
        
        return results
