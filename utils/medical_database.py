#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Медична база даних з нормалізованою структурою
"""

import sqlite3
import pandas as pd
import os
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
import re

logger = logging.getLogger(__name__)

class MedicalDatabase:
    """Медична база даних з нормалізованою структурою"""
    
    def __init__(self, db_path: str = "data/medical.db"):
        self.db_path = db_path
        self.ensure_db_directory()
        self.init_database()
    
    def ensure_db_directory(self):
        """Створює директорію для БД якщо не існує"""
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
    
    def init_database(self):
        """Ініціалізує структуру медичної бази даних"""
        with sqlite3.connect(self.db_path) as conn:
            # Таблиця підрозділів
            conn.execute('''
                CREATE TABLE IF NOT EXISTS units (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    parent_unit TEXT,
                    unit_type TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблиця пацієнтів (основна інформація)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS patients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    surname TEXT NOT NULL,
                    first_name TEXT NOT NULL,
                    patronymic TEXT,
                    full_name TEXT GENERATED ALWAYS AS (surname || ' ' || first_name || ' ' || COALESCE(patronymic, '')) STORED,
                    phone_number TEXT,
                    birth_date DATE,
                    unit_id INTEGER,
                    position TEXT,
                    rank TEXT,
                    category TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (unit_id) REFERENCES units (id),
                    UNIQUE(surname, first_name, patronymic, birth_date)
                )
            ''')
            
            # Таблиця діагнозів
            conn.execute('''
                CREATE TABLE IF NOT EXISTS diagnoses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    preliminary_diagnosis TEXT,
                    final_diagnosis TEXT,
                    injury_circumstances TEXT,
                    clarification TEXT,
                    is_combat_related BOOLEAN,
                    treatment_result TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблиця лікувань (епізоди лікування)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS treatments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id INTEGER NOT NULL,
                    diagnosis_id INTEGER,
                    episode_number INTEGER DEFAULT 1,
                    
                    -- Дати
                    injury_date DATE,
                    primary_hospitalization_date DATE,
                    current_hospital_admission_date DATE,
                    discharge_date DATE,
                    ambulatory_date DATE,
                    follow_up_date DATE,
                    
                    -- Місце лікування
                    hospital_place TEXT,
                    hospital_category TEXT,
                    hospital_location TEXT,
                    treatment_type TEXT,
                    
                    -- Діагностика
                    discharge_data TEXT,
                    
                    -- ВЛК (Військово-лікарська комісія)
                    vlk_start_date DATE,
                    vlk_registration_date TEXT,
                    vlk_received_date DATE,
                    vlk_conclusion_date TEXT,
                    vlk_issued_by TEXT,
                    vlk_conclusion TEXT,
                    vlk_decision_classification TEXT,
                    
                    -- МСЕК (Медико-соціальна експертна комісія)
                    msek_direction_date TEXT,
                    msek_decision TEXT,
                    
                    -- Фінанси
                    payment_period_from DATE,
                    payment_period_to DATE,
                    payment_note TEXT,
                    bed_days INTEGER,
                    
                    -- Статуси
                    is_combat BOOLEAN,
                    is_active BOOLEAN DEFAULT 1,
                    needs_prosthetics BOOLEAN DEFAULT 0,
                    has_exit_status BOOLEAN DEFAULT 0,
                    has_certificate_5 BOOLEAN DEFAULT 0,
                    
                    -- Додаткові дані
                    mkh_code TEXT,
                    patient_data TEXT,
                    follow_up_note TEXT,
                    detachment_circumstances TEXT,
                    affiliation TEXT,
                    
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    
                    FOREIGN KEY (patient_id) REFERENCES patients (id),
                    FOREIGN KEY (diagnosis_id) REFERENCES diagnoses (id)
                )
            ''')
            
            # Таблиця оплат (по місяцях)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id INTEGER NOT NULL,
                    treatment_id INTEGER,
                    payment_month TEXT NOT NULL,
                    payment_year INTEGER NOT NULL,
                    
                    injury_date DATE,
                    total_treatment_days REAL,
                    payment_date DATE,
                    diagnosis TEXT,
                    
                    -- Додаткові дані з файлів оплат
                    payment_dates TEXT,
                    raw_data TEXT,
                    
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    
                    FOREIGN KEY (patient_id) REFERENCES patients (id),
                    FOREIGN KEY (treatment_id) REFERENCES treatments (id),
                    UNIQUE(patient_id, payment_month, payment_year)
                )
            ''')
            
            # Індекси для швидкого пошуку
            conn.execute('CREATE INDEX IF NOT EXISTS idx_patients_full_name ON patients(full_name)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_patients_unit ON patients(unit_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_patients_phone ON patients(phone_number)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_treatments_patient ON treatments(patient_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_treatments_dates ON treatments(injury_date, primary_hospitalization_date)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_treatments_combat ON treatments(is_combat)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_payments_patient ON payments(patient_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_payments_month ON payments(payment_month, payment_year)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_units_name ON units(name)')
            
            conn.commit()
            logger.info("Структура медичної бази даних ініціалізована")
    
    def clean_name(self, name: str) -> str:
        """Очищає ім'я від зайвих символів"""
        if not name or pd.isna(name):
            return ""
        
        # Конвертуємо в рядок та очищаємо
        name_str = str(name).strip()
        
        # Видаляємо числа та зайві символи
        name_str = re.sub(r'^\d+$', '', name_str)  # Видаляємо рядки з одними числами
        name_str = re.sub(r'[^\w\s\-\.]', '', name_str)  # Залишаємо тільки літери, цифри, пробіли, дефіси та крапки
        
        return name_str.strip()
    
    def find_or_create_unit(self, unit_name: str) -> int:
        """Знаходить або створює підрозділ"""
        if not unit_name or pd.isna(unit_name):
            return None
        
        unit_name = str(unit_name).strip()
        if not unit_name:
            return None
        
        with sqlite3.connect(self.db_path) as conn:
            # Шукаємо існуючий підрозділ
            cursor = conn.execute('SELECT id FROM units WHERE name = ?', (unit_name,))
            result = cursor.fetchone()
            
            if result:
                return result[0]
            
            # Створюємо новий підрозділ
            cursor = conn.execute('''
                INSERT INTO units (name, unit_type, is_active)
                VALUES (?, 'unknown', 1)
            ''', (unit_name,))
            
            return cursor.lastrowid
    
    def find_or_create_patient(self, surname: str, first_name: str, patronymic: str = None, 
                              phone: str = None, birth_date: str = None, unit_name: str = None,
                              position: str = None, rank: str = None, category: str = None) -> int:
        """Знаходить або створює пацієнта"""
        
        # Очищаємо дані
        surname = self.clean_name(surname)
        first_name = self.clean_name(first_name)
        patronymic = self.clean_name(patronymic) if patronymic else None
        
        if not surname or not first_name:
            return None
        
        # Знаходимо або створюємо підрозділ
        unit_id = self.find_or_create_unit(unit_name) if unit_name else None
        
        with sqlite3.connect(self.db_path) as conn:
            # Шукаємо існуючого пацієнта
            query = '''
                SELECT id FROM patients 
                WHERE surname = ? AND first_name = ? 
                AND (patronymic = ? OR (patronymic IS NULL AND ? IS NULL))
            '''
            cursor = conn.execute(query, (surname, first_name, patronymic, patronymic))
            result = cursor.fetchone()
            
            if result:
                # Оновлюємо дані пацієнта якщо потрібно
                patient_id = result[0]
                conn.execute('''
                    UPDATE patients SET 
                        phone_number = COALESCE(?, phone_number),
                        unit_id = COALESCE(?, unit_id),
                        position = COALESCE(?, position),
                        rank = COALESCE(?, rank),
                        category = COALESCE(?, category),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (phone, unit_id, position, rank, category, patient_id))
                
                return patient_id
            
            # Створюємо нового пацієнта
            cursor = conn.execute('''
                INSERT INTO patients (
                    surname, first_name, patronymic, phone_number, birth_date,
                    unit_id, position, rank, category, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ''', (surname, first_name, patronymic, phone, birth_date, 
                  unit_id, position, rank, category))
            
            return cursor.lastrowid
    
    def create_diagnosis(self, preliminary: str = None, final: str = None, 
                        circumstances: str = None, clarification: str = None,
                        is_combat: bool = None, result: str = None) -> int:
        """Створює запис діагнозу"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                INSERT INTO diagnoses (
                    preliminary_diagnosis, final_diagnosis, injury_circumstances,
                    clarification, is_combat_related, treatment_result
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (preliminary, final, circumstances, clarification, is_combat, result))
            
            return cursor.lastrowid
    
    def create_treatment(self, patient_id: int, diagnosis_id: int = None, **treatment_data) -> int:
        """Створює запис лікування"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                INSERT INTO treatments (
                    patient_id, diagnosis_id, injury_date, primary_hospitalization_date,
                    current_hospital_admission_date, discharge_date, ambulatory_date,
                    follow_up_date, hospital_place, hospital_category, hospital_location,
                    treatment_type, discharge_data, vlk_start_date, vlk_registration_date,
                    vlk_received_date, vlk_conclusion_date, vlk_issued_by, vlk_conclusion,
                    vlk_decision_classification, msek_direction_date, msek_decision,
                    payment_period_from, payment_period_to, payment_note, bed_days,
                    is_combat, needs_prosthetics, has_exit_status, has_certificate_5,
                    mkh_code, patient_data, follow_up_note, detachment_circumstances,
                    affiliation, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ''', (
                patient_id, diagnosis_id,
                treatment_data.get('injury_date'),
                treatment_data.get('primary_hospitalization_date'),
                treatment_data.get('current_hospital_admission_date'),
                treatment_data.get('discharge_date'),
                treatment_data.get('ambulatory_date'),
                treatment_data.get('follow_up_date'),
                treatment_data.get('hospital_place'),
                treatment_data.get('hospital_category'),
                treatment_data.get('hospital_location'),
                treatment_data.get('treatment_type'),
                treatment_data.get('discharge_data'),
                treatment_data.get('vlk_start_date'),
                treatment_data.get('vlk_registration_date'),
                treatment_data.get('vlk_received_date'),
                treatment_data.get('vlk_conclusion_date'),
                treatment_data.get('vlk_issued_by'),
                treatment_data.get('vlk_conclusion'),
                treatment_data.get('vlk_decision_classification'),
                treatment_data.get('msek_direction_date'),
                treatment_data.get('msek_decision'),
                treatment_data.get('payment_period_from'),
                treatment_data.get('payment_period_to'),
                treatment_data.get('payment_note'),
                treatment_data.get('bed_days'),
                treatment_data.get('is_combat'),
                treatment_data.get('needs_prosthetics', False),
                treatment_data.get('has_exit_status', False),
                treatment_data.get('has_certificate_5', False),
                treatment_data.get('mkh_code'),
                treatment_data.get('patient_data'),
                treatment_data.get('follow_up_note'),
                treatment_data.get('detachment_circumstances'),
                treatment_data.get('affiliation')
            ))
            
            return cursor.lastrowid
    
    def create_payment(self, patient_id: int, treatment_id: int = None, 
                      month: str = None, year: int = None, **payment_data) -> int:
        """Створює запис оплати"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                INSERT OR REPLACE INTO payments (
                    patient_id, treatment_id, payment_month, payment_year,
                    injury_date, total_treatment_days, payment_date, diagnosis,
                    payment_dates, raw_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                patient_id, treatment_id, month, year,
                payment_data.get('injury_date'),
                payment_data.get('total_treatment_days'),
                payment_data.get('payment_date'),
                payment_data.get('diagnosis'),
                payment_data.get('payment_dates'),
                payment_data.get('raw_data')
            ))
            
            return cursor.lastrowid
    
    def search_patients(self, criteria: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Пошук пацієнтів за критеріями"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            query = '''
                SELECT p.*, u.name as unit_name
                FROM patients p
                LEFT JOIN units u ON p.unit_id = u.id
                WHERE 1=1
            '''
            params = []
            
            if criteria.get('name'):
                query += ' AND p.full_name LIKE ?'
                params.append(f"%{criteria['name']}%")
            
            if criteria.get('phone'):
                query += ' AND p.phone_number LIKE ?'
                params.append(f"%{criteria['phone']}%")
            
            if criteria.get('unit'):
                query += ' AND u.name LIKE ?'
                params.append(f"%{criteria['unit']}%")
            
            if criteria.get('rank'):
                query += ' AND p.rank = ?'
                params.append(criteria['rank'])
            
            query += ' ORDER BY p.full_name'
            
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
    
    def get_patient_treatments(self, patient_id: int) -> List[Dict[str, Any]]:
        """Отримує всі лікування пацієнта"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            cursor = conn.execute('''
                SELECT t.*, d.preliminary_diagnosis, d.final_diagnosis, t.is_combat
                FROM treatments t
                LEFT JOIN diagnoses d ON t.diagnosis_id = d.id
                WHERE t.patient_id = ? AND t.is_active = 1
                ORDER BY t.primary_hospitalization_date DESC
            ''', (patient_id,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_patient_payments(self, patient_id: int) -> List[Dict[str, Any]]:
        """Отримує всі оплати пацієнта"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            cursor = conn.execute('''
                SELECT * FROM payments
                WHERE patient_id = ?
                ORDER BY payment_year DESC, payment_month DESC
            ''', (patient_id,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_database_stats(self) -> Dict[str, Any]:
        """Отримує статистику бази даних"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            stats = {}
            
            # Кількість записів
            stats['patients_count'] = conn.execute('SELECT COUNT(*) as count FROM patients').fetchone()['count']
            stats['treatments_count'] = conn.execute('SELECT COUNT(*) as count FROM treatments WHERE is_active = 1').fetchone()['count']
            stats['payments_count'] = conn.execute('SELECT COUNT(*) as count FROM payments').fetchone()['count']
            stats['units_count'] = conn.execute('SELECT COUNT(*) as count FROM units').fetchone()['count']
            
            # Статистика по підрозділах
            cursor = conn.execute('''
                SELECT u.name, COUNT(p.id) as patient_count
                FROM units u
                LEFT JOIN patients p ON u.id = p.unit_id
                GROUP BY u.id, u.name
                ORDER BY patient_count DESC
                LIMIT 10
            ''')
            stats['top_units'] = [dict(row) for row in cursor.fetchall()]
            
            # Статистика по місяцях оплат
            cursor = conn.execute('''
                SELECT payment_month, payment_year, COUNT(*) as count
                FROM payments
                GROUP BY payment_month, payment_year
                ORDER BY payment_year DESC, payment_month DESC
            ''')
            stats['payments_by_month'] = [dict(row) for row in cursor.fetchall()]
            
            stats['database_path'] = self.db_path
            stats['database_size'] = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
            
            return stats
