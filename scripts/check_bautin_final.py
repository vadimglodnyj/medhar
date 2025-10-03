#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Фінальна перевірка БАУТІН
"""

import sys
import os
import sqlite3
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.medical_database import MedicalDatabase

def check_bautin_final():
    """Фінальна перевірка БАУТІН"""
    print("🔍 Фінальна перевірка БАУТІН")
    print("=" * 80)
    
    db = MedicalDatabase()
    
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        # 1. Знаходимо пацієнта
        print("1️⃣ ПОШУК ПАЦІЄНТА:")
        cursor = conn.execute('''
            SELECT id, full_name, rank, phone_number
            FROM patients 
            WHERE full_name LIKE '%БАУТІН%'
        ''')
        patient = cursor.fetchone()
        
        if not patient:
            print("❌ Пацієнт БАУТІН не знайдений в базі даних")
            return
        
        print(f"✅ Знайдено пацієнта:")
        print(f"   ID: {patient['id']}")
        print(f"   ПІБ: {patient['full_name']}")
        print(f"   Звання: {patient['rank']}")
        print(f"   Телефон: {patient['phone_number']}")
        
        # 2. Перевіряємо лікування
        print(f"\n2️⃣ ЛІКУВАННЯ:")
        cursor = conn.execute('''
            SELECT t.id, t.episode_number, t.primary_hospitalization_date, 
                   t.discharge_date, t.hospital_place, t.treatment_type,
                   t.injury_date, t.is_combat, t.is_active,
                   d.preliminary_diagnosis, d.final_diagnosis
            FROM treatments t
            LEFT JOIN diagnoses d ON t.diagnosis_id = d.id
            WHERE t.patient_id = ?
            ORDER BY t.primary_hospitalization_date
        ''', (patient['id'],))
        
        treatments = cursor.fetchall()
        print(f"   Знайдено {len(treatments)} записів лікування:")
        
        for i, treatment in enumerate(treatments, 1):
            print(f"\n   📋 Лікування {i} (ID: {treatment['id']}):")
            print(f"      Епізод: {treatment['episode_number']}")
            print(f"      Дата початку: {treatment['primary_hospitalization_date']}")
            print(f"      Дата закінчення: {treatment['discharge_date']}")
            print(f"      Місце лікування: {treatment['hospital_place']}")
            print(f"      Тип лікування: {treatment['treatment_type']}")
            print(f"      Дата поранення: {treatment['injury_date']}")
            print(f"      Бойовий статус: {treatment['is_combat']} ({'бойова' if treatment['is_combat'] else 'небойова'})")
            print(f"      Активний: {treatment['is_active']}")
        
        # 3. Перевіряємо оплати
        print(f"\n3️⃣ ОПЛАТИ:")
        cursor = conn.execute('''
            SELECT id, payment_month, payment_year, total_treatment_days,
                   payment_date, diagnosis, payment_dates, raw_data
            FROM payments
            WHERE patient_id = ?
            ORDER BY payment_year, payment_month
        ''', (patient['id'],))
        
        payments = cursor.fetchall()
        print(f"   Знайдено {len(payments)} записів оплат:")
        
        if payments:
            for i, payment in enumerate(payments, 1):
                print(f"\n   💰 Оплата {i} (ID: {payment['id']}):")
                print(f"      Місяць: {payment['payment_month']} {payment['payment_year']}")
                print(f"      Дні лікування: {payment['total_treatment_days']}")
                print(f"      Дата оплати: {payment['payment_date']}")
                print(f"      Дати загал: {payment['payment_dates']}")
                print(f"      Діагноз: {str(payment['diagnosis'])[:100]}...")
        else:
            print("   ❌ Оплати не знайдені")
        
        # 4. Статистика по базі
        print(f"\n4️⃣ СТАТИСТИКА БАЗИ ДАНИХ:")
        stats = db.get_database_stats()
        print(f"   Пацієнтів: {stats['patients_count']}")
        print(f"   Лікувань: {stats['treatments_count']}")
        print(f"   Оплат: {stats['payments_count']}")
        print(f"   Підрозділів: {stats['units_count']}")

if __name__ == "__main__":
    check_bautin_final()

