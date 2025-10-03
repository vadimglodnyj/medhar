#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Менеджер бази даних для імпорту та роботи з даними лікування та оплат
"""

import sqlite3
import pandas as pd
import os
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
import json

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Менеджер для роботи з SQLite базою даних"""
    
    def __init__(self, db_path: str = "data/medchar.db"):
        self.db_path = db_path
        self.ensure_db_directory()
        self.init_database()
    
    def ensure_db_directory(self):
        """Створює директорію для БД якщо не існує"""
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
    
    def init_database(self):
        """Ініціалізує структуру бази даних"""
        with sqlite3.connect(self.db_path) as conn:
            # Таблиця для даних лікування
            conn.execute('''
                CREATE TABLE IF NOT EXISTS treatments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    surname TEXT,
                    name TEXT,
                    patronymic TEXT,
                    full_name TEXT,
                    unit TEXT,
                    rank TEXT,
                    combat_status TEXT,
                    preliminary_diagnosis TEXT,
                    final_diagnosis TEXT,
                    treatment_type TEXT,
                    hospital_place TEXT,
                    treatment_start_date TEXT,
                    treatment_end_date TEXT,
                    total_treatment_days REAL,
                    injury_date TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблиця для даних оплат
            conn.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    month TEXT,
                    full_name TEXT,
                    unit TEXT,
                    rank TEXT,
                    injury_date TEXT,
                    total_treatment_days REAL,
                    diagnosis TEXT,
                    payment_date TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Індекси для швидкого пошуку
            conn.execute('CREATE INDEX IF NOT EXISTS idx_treatments_full_name ON treatments(full_name)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_treatments_unit ON treatments(unit)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_treatments_combat_status ON treatments(combat_status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_payments_full_name ON payments(full_name)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_payments_month ON payments(month)')
            
            conn.commit()
    
    def import_treatments_data(self, excel_path: str) -> int:
        """Імпортує дані лікування з Excel файлу"""
        logger.info(f"Імпорт даних лікування з {excel_path}")
        
        try:
            df = pd.read_excel(excel_path)
            logger.info(f"Завантажено {len(df)} записів з treatments_2025")
            
            # Очищаємо дані
            df = self.clean_treatments_data(df)
            
            with sqlite3.connect(self.db_path) as conn:
                # Очищуємо попередні дані
                conn.execute('DELETE FROM treatments')
                
                # Імпортуємо нові дані
                for _, row in df.iterrows():
                    conn.execute('''
                        INSERT INTO treatments (
                            surname, name, patronymic, full_name, unit, rank,
                            combat_status, preliminary_diagnosis, final_diagnosis,
                            treatment_type, hospital_place, treatment_start_date,
                            treatment_end_date, total_treatment_days, injury_date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        self.safe_get(row, 'Прізвище'),
                        self.safe_get(row, 'Ім\'я'),
                        self.safe_get(row, 'По батькові'),
                        self.create_full_name(row),
                        self.safe_get(row, 'Підрозділ'),
                        self.safe_get(row, 'Військове звання'),
                        self.safe_get(row, 'Бойова/ небойова'),
                        self.safe_get(row, 'Попередній діагноз'),
                        self.safe_get(row, 'Заключний діагноз'),
                        self.safe_get(row, 'Вид лікування'),
                        self.safe_get(row, 'Місце госпіталізації'),
                        self.safe_get(row, 'Дата первинної госпіталізації', ''),
                        self.safe_get(row, 'Дата закінчення лікування', ''),
                        self.parse_treatment_days(self.safe_get(row, 'Сумарна кількість днів лікування')),
                        self.safe_get(row, 'Дата отримання поранення (контузії, травми, каліцтва)')
                    ))
                
                conn.commit()
                logger.info(f"Успішно імпортовано {len(df)} записів лікування")
                return len(df)
                
        except Exception as e:
            logger.error(f"Помилка імпорту даних лікування: {e}")
            return 0
    
    def import_payments_data(self, excel_paths: Dict[str, str]) -> int:
        """Імпортує дані оплат з Excel файлів по місяцях"""
        logger.info(f"Імпорт даних оплат з {len(excel_paths)} файлів")
        
        total_imported = 0
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Очищуємо попередні дані
                conn.execute('DELETE FROM payments')
                
                for month, excel_path in excel_paths.items():
                    if not os.path.exists(excel_path):
                        logger.warning(f"Файл не існує: {excel_path}")
                        continue
                    
                    try:
                        df = pd.read_excel(excel_path)
                        logger.info(f"Завантажено {len(df)} записів з {month}")
                        
                        # Очищаємо дані
                        df = self.clean_payments_data(df)
                        
                        for _, row in df.iterrows():
                            conn.execute('''
                                INSERT INTO payments (
                                    month, full_name, unit, rank, injury_date,
                                    total_treatment_days, diagnosis, payment_date
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                month,
                                self.get_patient_name_from_payments(row),
                                self.safe_get(row, 'Підрозділ'),
                                self.safe_get(row, 'Військове звання'),
                                self.safe_get(row, 'Дата отримання поранення (контузії, травми, каліцтва)'),
                                self.parse_treatment_days(self.safe_get(row, 'Сумарна кількість днів лікування')),
                                self.safe_get(row, 'Діагноз'),
                                self.safe_get(row, 'Дата отримання поранення (контузії, травми, каліцтва)')
                            ))
                        
                        total_imported += len(df)
                        logger.info(f"Імпортовано {len(df)} записів з {month}")
                        
                    except Exception as e:
                        logger.error(f"Помилка імпорту {month}: {e}")
                
                conn.commit()
                logger.info(f"Успішно імпортовано {total_imported} записів оплат")
                return total_imported
                
        except Exception as e:
            logger.error(f"Помилка імпорту даних оплат: {e}")
            return 0
    
    def clean_treatments_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Очищає дані лікування"""
        # Видаляємо рядки з порожніми іменами
        if 'Прізвище' in df.columns and 'Ім\'я' in df.columns:
            df = df.dropna(subset=['Прізвище', 'Ім\'я'])
            df = df[df['Прізвище'].astype(str).str.len() > 2]
            df = df[df['Ім\'я'].astype(str).str.len() > 2]
        
        return df
    
    def clean_payments_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Очищає дані оплат"""
        # Знаходимо стовпець з іменами
        name_columns = ['ПІБ', 'Прізвище, власне ім\'я, по батькові (за наявності)']
        name_column = None
        
        for col in name_columns:
            if col in df.columns:
                name_column = col
                break
        
        # Додаткова перевірка - шукаємо стовпці з "Прізвище"
        if not name_column:
            for col in df.columns:
                if 'Прізвище' in str(col):
                    name_column = col
                    break
        
        if name_column:
            # Видаляємо рядки де ім'я є NaN або число
            df = df.dropna(subset=[name_column])
            df = df[~df[name_column].astype(str).str.match(r'^\d+$')]
            df = df[df[name_column].astype(str).str.len() > 2]
        
        return df
    
    def create_full_name(self, row: pd.Series) -> str:
        """Створює повне ім'я з окремих стовпців"""
        surname = self.safe_get(row, 'Прізвище', '')
        name = self.safe_get(row, 'Ім\'я', '')
        patronymic = self.safe_get(row, 'По батькові', '')
        
        parts = [surname, name, patronymic]
        return ' '.join([part for part in parts if part and str(part).strip()])
    
    def get_patient_name_from_payments(self, row: pd.Series) -> str:
        """Отримує ім'я пацієнта з даних оплат"""
        name_columns = ['ПІБ', 'Прізвище, власне ім\'я, по батькові (за наявності)']
        
        for col in name_columns:
            if col in row.index and pd.notna(row[col]):
                return str(row[col]).strip()
        
        # Додаткова перевірка
        for col in row.index:
            if 'Прізвище' in str(col) and pd.notna(row[col]):
                return str(row[col]).strip()
        
        return ''
    
    def parse_treatment_days(self, value: Any) -> float:
        """Парсить кількість днів лікування"""
        if pd.isna(value) or value is None:
            return 0.0
        
        value_str = str(value)
        if not value_str or value_str.strip() == '':
            return 0.0
        
        # Знаходимо всі числа в рядку
        import re
        numbers = re.findall(r'\d+(?:\.\d+)?', value_str)
        
        if not numbers:
            return 0.0
        
        # Повертаємо найбільше число
        try:
            return max(float(num) for num in numbers)
        except (ValueError, TypeError):
            return 0.0
    
    def safe_get(self, row: pd.Series, column: str, default: Any = None) -> Any:
        """Безпечно отримує значення з рядка"""
        if column in row.index and pd.notna(row[column]):
            value = row[column]
            # Конвертуємо pandas Timestamp в рядок
            if hasattr(value, 'strftime'):
                return value.strftime('%Y-%m-%d')
            return str(value)
        return default
    
    def search_treatments(self, criteria: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Пошук в даних лікування"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            query = "SELECT * FROM treatments WHERE 1=1"
            params = []
            
            if criteria.get('unit_filter'):
                # Підрозділи 2 БОП (без неоплачуваних типів лікування)
                unit_2bop = [
                    '1 РОП 2 БОП', '2 РОП 2 БОП', '3 РОП 2 БОП',
                    'ВІ 2 БОП', 'ВЗ 2 БОП', 'ВМТЗ 2 БОП', 'ВТО 2 БОП',
                    'ВРСП 2 БОП', 'МБ(120 мм) 2 БОП',
                    'МБ(60 мм(82 мм)) 2 БОП', 'ІСВ 2 БОП', 'РВП 2 БОП',
                    'Штаб 2 БОП'
                ]
                placeholders = ','.join(['?' for _ in unit_2bop])
                query += f" AND unit IN ({placeholders})"
                params.extend(unit_2bop)
            
            if criteria.get('combat_status'):
                query += " AND combat_status = ?"
                params.append(criteria['combat_status'])
            
            if criteria.get('patient_name'):
                query += " AND full_name LIKE ?"
                params.append(f"%{criteria['patient_name']}%")
            
            if criteria.get('diagnosis_keywords'):
                injury_keywords = [
                    'ВОСП', 'ВТ', 'МВТ', 'наслідки МВТ', 'наслідки ВОСП',
                    'огнепальне поранення', 'осколкове поранення', 'контузія',
                    'травма', 'поранення', 'каліцтво', 'опік', 'обмороження',
                    'комбіноване поранення', 'множинне поранення', 'пневмоторакс',
                    'гемоторакс', 'черепно-мозкова травма', 'ЧМТ',
                    'спинальна травма', 'перелом', 'вивих', 'розтягнення',
                    'розрив', 'ампутація', 'втрата кінцівки', 'порушення слуху',
                    'порушення зору', 'посттравматичний стресовий розлад', 'ПТСР'
                ]
                
                diagnosis_conditions = []
                for keyword in injury_keywords:
                    diagnosis_conditions.append("(preliminary_diagnosis LIKE ? OR final_diagnosis LIKE ?)")
                    params.extend([f"%{keyword}%", f"%{keyword}%"])
                
                query += f" AND ({' OR '.join(diagnosis_conditions)})"
            
            # Виключаємо неоплачувані типи лікування
            excluded_treatment_types = ['Стабілізаційний пункт', 'Амбулаторно', 'Лазарет', 'ВЛК']
            treatment_placeholders = ','.join(['?' for _ in excluded_treatment_types])
            query += f" AND treatment_type NOT IN ({treatment_placeholders})"
            params.extend(excluded_treatment_types)
            
            # Виключаємо неоплачувані місця госпіталізації
            excluded_hospital_places = [
                'медична рота 3029', 'Медична рота 3029',
                'Медична рота 3029 (ОТУ Харків)', 'Медична рота 3029 (ОТУ Донецьк)',
                'Медичний пункт бригади в/ч 3029'
            ]
            hospital_placeholders = ','.join(['?' for _ in excluded_hospital_places])
            query += f" AND hospital_place NOT IN ({hospital_placeholders})"
            params.extend(excluded_hospital_places)
            
            cursor = conn.execute(query, params)
            results = [dict(row) for row in cursor.fetchall()]
            
            logger.info(f"Знайдено {len(results)} записів лікування")
            return results
    
    def search_payments(self, patient_name: str) -> Dict[str, Any]:
        """Пошук оплат для пацієнта"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            query = "SELECT * FROM payments WHERE full_name LIKE ?"
            cursor = conn.execute(query, (f"%{patient_name}%",))
            results = [dict(row) for row in cursor.fetchall()]
            
            # Підраховуємо статистику
            total_paid_days = sum(row['total_treatment_days'] or 0 for row in results)
            payment_count = len(results)
            last_payment_date = None
            
            if results:
                dates = [row['payment_date'] for row in results if row['payment_date']]
                if dates:
                    try:
                        last_payment_date = max(pd.to_datetime(date) for date in dates if date)
                    except:
                        pass
            
            return {
                'total_paid_days': total_paid_days,
                'payment_count': payment_count,
                'last_payment_date': last_payment_date,
                'total_records': len(results),
                'payments': results
            }
    
    def get_database_stats(self) -> Dict[str, Any]:
        """Отримує статистику бази даних"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            treatments_count = conn.execute("SELECT COUNT(*) as count FROM treatments").fetchone()['count']
            payments_count = conn.execute("SELECT COUNT(*) as count FROM payments").fetchone()['count']
            
            months = conn.execute("SELECT DISTINCT month FROM payments ORDER BY month").fetchall()
            months_list = [row['month'] for row in months]
            
            return {
                'treatments_count': treatments_count,
                'payments_count': payments_count,
                'available_months': months_list,
                'database_path': self.db_path,
                'database_size': os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
            }
