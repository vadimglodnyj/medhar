#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Аналізатор виплат на основі медичної бази даних
"""

import os
import sys
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
import sqlite3

# Додаємо батьківську папку до шляху для імпорту
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.medical_database import MedicalDatabase
from config import *

logger = logging.getLogger(__name__)

class MedicalPaymentAnalyzer:
    """Аналізатор виплат на основі медичної бази даних"""
    
    def __init__(self):
        self.db = MedicalDatabase()
        
        # Підрозділи 2 БОП (без неоплачуваних типів лікування)
        self.unit_2bop = [
            '1 РОП 2 БОП', '2 РОП 2 БОП', '3 РОП 2 БОП',
            'ВІ 2 БОП', 'ВЗ 2 БОП', 'ВМТЗ 2 БОП', 'ВТО 2 БОП',
            'ВРСП 2 БОП', 'МБ(120 мм) 2 БОП',
            'МБ(60 мм(82 мм)) 2 БОП', 'ІСВ 2 БОП', 'РВП 2 БОП',
            'Штаб 2 БОП'
        ]
        
        # Ключові слова для діагнозів поранень
        self.injury_keywords = [
            'ВОСП', 'ВТ', 'МВТ', 'наслідки МВТ', 'наслідки ВОСП',
            'огнепальне поранення', 'осколкове поранення', 'контузія',
            'травма', 'поранення', 'каліцтво', 'опік', 'обмороження',
            'комбіноване поранення', 'множинне поранення', 'пневмоторакс',
            'гемоторакс', 'черепно-мозкова травма', 'ЧМТ',
            'спинальна травма', 'перелом', 'вивих', 'розтягнення',
            'розрив', 'ампутація', 'втрата кінцівки', 'порушення слуху',
            'порушення зору', 'посттравматичний стресовий розлад', 'ПТСР'
        ]
        
        # Виключаємо типи лікування, які не оплачуються
        self.excluded_treatment_types = [
            'Стабілізаційний пункт',
            'Амбулаторно', 
            'Лазарет',
            'ВЛК'
        ]
        
        # Виключаємо місця госпіталізації, які не оплачуються
        self.excluded_hospital_places = [
            'медична рота 3029',
            'Медична рота 3029',
            'Медична рота 3029 (ОТУ Харків)',
            'Медична рота 3029 (ОТУ Донецьк)',
            'Медичний пункт бригади в/ч 3029'
        ]
    
    def search_patients(self, criteria: Dict[str, Any]) -> Dict[str, Any]:
        """Пошук пацієнтів за критеріями"""
        try:
            with sqlite3.connect(self.db.db_path) as conn:
                conn.row_factory = sqlite3.Row
                
                # Базовий запит
                query = '''
                    SELECT DISTINCT p.*, u.name as unit_name
                    FROM patients p
                    LEFT JOIN units u ON p.unit_id = u.id
                    LEFT JOIN treatments t ON p.id = t.patient_id
                    LEFT JOIN diagnoses d ON t.diagnosis_id = d.id
                    WHERE p.is_active = 1 AND t.is_active = 1
                '''
                params = []
                
                # Фільтр по підрозділах 2 БОП
                if criteria.get('unit_filter', False):
                    placeholders = ','.join(['?' for _ in self.unit_2bop])
                    query += f' AND u.name IN ({placeholders})'
                    params.extend(self.unit_2bop)
                
                # Фільтр по бойовому статусу
                if criteria.get('combat_status'):
                    query += ' AND t.is_combat = ?'
                    params.append(criteria['combat_status'] == 'бойова')
                
                # Фільтр по імені пацієнта
                if criteria.get('patient_name'):
                    query += ' AND p.full_name LIKE ?'
                    params.append(f"%{criteria['patient_name']}%")
                
                # Фільтр по діагнозах поранень
                if criteria.get('diagnosis_keywords', False):
                    diagnosis_conditions = []
                    for keyword in self.injury_keywords:
                        diagnosis_conditions.append("(d.preliminary_diagnosis LIKE ? OR d.final_diagnosis LIKE ?)")
                        params.extend([f"%{keyword}%", f"%{keyword}%"])
                    
                    query += f' AND ({" OR ".join(diagnosis_conditions)})'
                
                # Виключаємо неоплачувані типи лікування
                treatment_placeholders = ','.join(['?' for _ in self.excluded_treatment_types])
                query += f' AND t.treatment_type NOT IN ({treatment_placeholders})'
                params.extend(self.excluded_treatment_types)
                
                # Виключаємо неоплачувані місця госпіталізації
                hospital_placeholders = ','.join(['?' for _ in self.excluded_hospital_places])
                query += f' AND t.hospital_place NOT IN ({hospital_placeholders})'
                params.extend(self.excluded_hospital_places)
                
                query += ' ORDER BY p.full_name'
                
                cursor = conn.execute(query, params)
                results = [dict(row) for row in cursor.fetchall()]
                
                # Перетворюємо в потрібний формат
                patients = []
                for patient in results:
                    # Отримуємо лікування для пацієнта
                    treatments = self.get_patient_treatments(patient['id'])
                    
                    if treatments:
                        # Беремо найактуальніше лікування (останнє по даті виписки)
                        treatment = max(treatments, key=lambda t: t['discharge_date'] or t['primary_hospitalization_date'] or '')
                        
                        patients.append({
                            'patient_name': patient['full_name'],
                            'data': {
                                'Підрозділ': patient['unit_name'],
                                'Військове звання': patient['rank'],
                                'Бойова/ небойова': 'бойова' if treatment['is_combat'] else 'небойова',
                                'Попередній діагноз': treatment['preliminary_diagnosis'],
                                'Заключний діагноз': treatment['final_diagnosis'],
                                'Дата первинної госпіталізації': treatment['primary_hospitalization_date'],
                                'Дата закінчення лікування': treatment['discharge_date'],
                                'Місце госпіталізації': treatment['hospital_place'],
                                'Вид лікування': treatment['treatment_type'],
                                'Номер телефону': patient['phone_number'],
                                'Кількість лікувань': len(treatments),
                                'Всі лікування': treatments  # Додаємо всі записи лікування
                            }
                        })
                
                result = {
                    'total_found': len(patients),
                    'patients': patients,
                    'search_criteria': criteria
                }
                
                # Додаємо перевірку оплат якщо потрібно
                if criteria.get('check_payments', False):
                    payment_check_results = self.check_payments_for_patients(patients)
                    result['payment_verification'] = payment_check_results
                
                logger.info(f"Знайдено {len(patients)} пацієнтів")
                return result
                
        except Exception as e:
            logger.error(f"Помилка пошуку пацієнтів: {e}")
            return {'error': str(e)}
    
    def get_patient_treatments(self, patient_id: int) -> List[Dict[str, Any]]:
        """Отримує лікування пацієнта"""
        return self.db.get_patient_treatments(patient_id)
    
    def get_patient_payments(self, patient_id: int) -> List[Dict[str, Any]]:
        """Отримує оплати пацієнта"""
        return self.db.get_patient_payments(patient_id)
    
    def search_patient_payment_history(self, patient_name: str) -> Dict[str, Any]:
        """Пошук історії оплат пацієнта"""
        try:
            # Знаходимо пацієнта
            patients = self.db.search_patients({'name': patient_name})
            
            if not patients:
                return {'error': 'Пацієнт не знайдений'}
            
            patient = patients[0]
            patient_id = patient['id']
            
            # Отримуємо лікування
            treatments = self.get_patient_treatments(patient_id)
            
            # Отримуємо оплати
            payments = self.get_patient_payments(patient_id)
            
            # Підраховуємо статистику
            total_paid_days = sum(payment.get('total_treatment_days', 0) or 0 for payment in payments)
            payment_count = len(payments)
            last_payment_date = None
            
            if payments:
                dates = [payment['payment_date'] for payment in payments if payment['payment_date']]
                if dates:
                    last_payment_date = max(dates)
            
            # Знаходимо дати лікування
            treatment_start_date = None
            treatment_end_date = None
            treatment_location = None
            
            if treatments:
                treatment = treatments[0]
                treatment_start_date = treatment.get('primary_hospitalization_date')
                treatment_end_date = treatment.get('discharge_date')
                treatment_location = treatment.get('hospital_place')
            
            return {
                'patient_info': {
                    'id': patient_id,
                    'full_name': patient['full_name'],
                    'phone': patient['phone_number'],
                    'unit': patient['unit_name'],
                    'rank': patient['rank']
                },
                'payment_summary': {
                    'total_paid_days': round(total_paid_days, 1),
                    'payment_count': payment_count,
                    'last_payment_date': last_payment_date,
                    'total_records': len(payments),
                    'treatment_start_date': treatment_start_date,
                    'treatment_end_date': treatment_end_date,
                    'treatment_location': treatment_location,
                    'has_treatment_data': len(treatments) > 0
                },
                'treatments': treatments,
                'payments': payments
            }
            
        except Exception as e:
            logger.error(f"Помилка пошуку оплат для {patient_name}: {e}")
            return {'error': str(e)}
    
    def check_payments_for_patients(self, patients_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Перевіряє оплати для списку пацієнтів"""
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
            
            # Перевіряємо оплати
            payment_results = self.search_patient_payment_history(patient_name)
            
            has_payments = False
            payment_summary = {}
            
            if payment_results and not payment_results.get('error'):
                payment_summary = payment_results.get('payment_summary', {})
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
                'phone': patient_data.get('Номер телефону', 'Невідомий'),
                'has_payments': has_payments,
                'payment_summary': payment_summary
            }
            
            results['patient_details'].append(patient_detail)
        
        logger.info(f"Перевірка завершена: {results['patients_with_payments']} з оплатами, {results['patients_without_payments']} без оплат")
        
        return results
    
    def compare_treatments_with_payments(self) -> Dict[str, Any]:
        """Порівнює лікування з оплатами"""
        try:
            with sqlite3.connect(self.db.db_path) as conn:
                conn.row_factory = sqlite3.Row
                
                # Знаходимо всіх пацієнтів з лікуваннями в 2 БОП
                query = '''
                    SELECT DISTINCT p.id, p.full_name, u.name as unit_name
                    FROM patients p
                    LEFT JOIN units u ON p.unit_id = u.id
                    LEFT JOIN treatments t ON p.id = t.patient_id
                    WHERE u.name IN ({})
                    AND t.is_active = 1
                    AND t.treatment_type NOT IN ({})
                    AND t.hospital_place NOT IN ({})
                '''.format(
                    ','.join(['?' for _ in self.unit_2bop]),
                    ','.join(['?' for _ in self.excluded_treatment_types]),
                    ','.join(['?' for _ in self.excluded_hospital_places])
                )
                
                params = self.unit_2bop + self.excluded_treatment_types + self.excluded_hospital_places
                
                cursor = conn.execute(query, params)
                all_patients = [dict(row) for row in cursor.fetchall()]
                
                # Перевіряємо оплати для кожного пацієнта
                paid_treatments = 0
                unpaid_treatments = 0
                details = []
                
                for patient in all_patients:
                    payments = self.get_patient_payments(patient['id'])
                    has_payments = len(payments) > 0
                    
                    if has_payments:
                        paid_treatments += 1
                    else:
                        unpaid_treatments += 1
                    
                    details.append({
                        'patient_name': patient['full_name'],
                        'unit': patient['unit_name'],
                        'has_payments': has_payments,
                        'payment_count': len(payments)
                    })
                
                return {
                    'total_treatments': len(all_patients),
                    'paid_treatments': paid_treatments,
                    'unpaid_treatments': unpaid_treatments,
                    'payment_percentage': round((paid_treatments / len(all_patients)) * 100, 2) if all_patients else 0,
                    'details': details
                }
                
        except Exception as e:
            logger.error(f"Помилка порівняння лікування з оплатами: {e}")
            return {'error': str(e)}
    
    def get_database_stats(self) -> Dict[str, Any]:
        """Отримує статистику бази даних"""
        return self.db.get_database_stats()
    
    def get_monthly_payment_stats(self, month: str) -> Dict[str, Any]:
        """Отримує статистику оплат по місяцях"""
        try:
            with sqlite3.connect(self.db.db_path) as conn:
                conn.row_factory = sqlite3.Row
                
                # Статистика по місяцю
                cursor = conn.execute('''
                    SELECT 
                        COUNT(*) as total_payments,
                        SUM(total_treatment_days) as total_days,
                        AVG(total_treatment_days) as avg_days,
                        COUNT(DISTINCT patient_id) as unique_patients
                    FROM payments 
                    WHERE payment_month = ?
                ''', (month,))
                
                stats = dict(cursor.fetchone())
                
                # Топ підрозділи
                cursor = conn.execute('''
                    SELECT u.name, COUNT(p.id) as payment_count
                    FROM payments pay
                    JOIN patients p ON pay.patient_id = p.id
                    LEFT JOIN units u ON p.unit_id = u.id
                    WHERE pay.payment_month = ?
                    GROUP BY u.id, u.name
                    ORDER BY payment_count DESC
                    LIMIT 10
                ''', (month,))
                
                top_units = [dict(row) for row in cursor.fetchall()]
                
                return {
                    'month': month,
                    'total_payments': stats['total_payments'] or 0,
                    'total_days': stats['total_days'] or 0,
                    'avg_days': round(stats['avg_days'] or 0, 1),
                    'unique_patients': stats['unique_patients'] or 0,
                    'top_units': top_units
                }
                
        except Exception as e:
            logger.error(f"Помилка отримання статистики за {month}: {e}")
            return {'error': str(e)}
