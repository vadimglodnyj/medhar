#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Виправлення оплат для БАУТІН
"""

import sys
import os
import sqlite3
import pandas as pd
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.medical_database import MedicalDatabase

def fix_bautin_payments():
    """Виправлення оплат для БАУТІН"""
    print("🔧 Виправлення оплат для БАУТІН")
    print("=" * 80)
    
    db = MedicalDatabase()
    
    # Знаходимо пацієнта БАУТІН
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        cursor = conn.execute('''
            SELECT id, full_name FROM patients 
            WHERE full_name LIKE '%БАУТІН%'
        ''')
        patient = cursor.fetchone()
        
        if not patient:
            print("❌ Пацієнт БАУТІН не знайдений")
            return
        
        patient_id = patient['id']
        print(f"✅ Знайдено пацієнта: {patient['full_name']} (ID: {patient_id})")
        
        # Видаляємо існуючі оплати для БАУТІН
        print(f"\n🗑️ Видаляємо існуючі оплати...")
        cursor = conn.execute('DELETE FROM payments WHERE patient_id = ?', (patient_id,))
        print(f"   Видалено {cursor.rowcount} записів")
        
        # Імпортуємо оплати з Excel файлів
        payment_files = {
            "may": "data/may_2025.xlsx",
            "june": "data/june_2025.xlsx", 
            "july": "data/july_2025.xlsx",
            "august": "data/august_2025.xlsx"
        }
        
        imported_payments = 0
        
        for month, file_path in payment_files.items():
            if not os.path.exists(file_path):
                print(f"   ⚠️ Файл не знайдено: {file_path}")
                continue
            
            print(f"\n📋 Імпорт оплат з {month}:")
            
            try:
                df = pd.read_excel(file_path)
                
                # Знаходимо записи БАУТІН
                name_columns = ['ПІБ', 'Прізвище, власне ім\'я, по батькові (за наявності)']
                bautin_records = None
                
                for col in name_columns:
                    if col in df.columns:
                        bautin_records = df[df[col].str.contains('БАУТІН', case=False, na=False)]
                        if not bautin_records.empty:
                            break
                
                if bautin_records is None or bautin_records.empty:
                    print(f"   ❌ БАУТІН не знайдений в {month}")
                    continue
                
                print(f"   ✅ Знайдено {len(bautin_records)} записів для БАУТІН")
                
                for i, (_, row) in enumerate(bautin_records.iterrows(), 1):
                    print(f"\n   📄 Запис {i}:")
                    
                    # Парсимо дані
                    def safe_get(row, column, default=None):
                        if column in row.index and pd.notna(row[column]):
                            return row[column]
                        return default
                    
                    def parse_treatment_days(days_value):
                        if pd.isna(days_value) or days_value is None:
                            return 0.0
                        
                        try:
                            days_str = str(days_value)
                            if not days_str or days_str.strip() == '':
                                return 0.0
                            
                            # Знаходимо всі числа в рядку
                            import re
                            numbers = re.findall(r'\d+(?:\.\d+)?', days_str)
                            
                            if not numbers:
                                return 0.0
                            
                            # Повертаємо найбільше число
                            return max(float(num) for num in numbers)
                        except (ValueError, TypeError):
                            return 0.0
                    
                    injury_date = safe_get(row, 'Дата отримання поранення (контузії, травми, каліцтва)')
                    if injury_date and hasattr(injury_date, 'strftime'):
                        injury_date = injury_date.strftime('%Y-%m-%d')
                    
                    total_days = parse_treatment_days(safe_get(row, 'Сумарна кількість днів лікування'))
                    diagnosis = safe_get(row, 'Діагноз')
                    payment_dates = safe_get(row, 'Дати загал')
                    
                    print(f"      Дата поранення: {injury_date}")
                    print(f"      Дні лікування: {total_days}")
                    print(f"      Діагноз: {str(diagnosis)[:100]}...")
                    print(f"      Дати оплати: {payment_dates}")
                    
                    # Створюємо оплату
                    payment_id = db.create_payment(
                        patient_id=patient_id,
                        month=month,
                        year=2025,
                        injury_date=injury_date,
                        total_treatment_days=total_days,
                        payment_date=injury_date,
                        diagnosis=diagnosis,
                        payment_dates=str(payment_dates),
                        raw_data=str(row.to_dict())
                    )
                    
                    print(f"      ✅ Створено оплату ID: {payment_id}")
                    imported_payments += 1
                
            except Exception as e:
                print(f"   ❌ Помилка імпорту {month}: {e}")
        
        conn.commit()
        
        # Перевіряємо результат
        print(f"\n📊 РЕЗУЛЬТАТ:")
        cursor = conn.execute('''
            SELECT id, payment_month, payment_year, total_treatment_days, 
                   payment_date, diagnosis, payment_dates
            FROM payments
            WHERE patient_id = ?
            ORDER BY payment_year, payment_month
        ''', (patient_id,))
        
        payments = cursor.fetchall()
        print(f"   Імпортовано {len(payments)} оплат:")
        
        for payment in payments:
            print(f"\n   💰 Оплата ID {payment['id']}:")
            print(f"      Місяць: {payment['payment_month']} {payment['payment_year']}")
            print(f"      Дні лікування: {payment['total_treatment_days']}")
            print(f"      Дата оплати: {payment['payment_date']}")
            print(f"      Дати загал: {payment['payment_dates']}")

if __name__ == "__main__":
    fix_bautin_payments()
