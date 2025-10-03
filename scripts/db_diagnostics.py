#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quick diagnostics for database/medical_new.db
- Table counts
- Payments sample rows
- Duplicate patients by full_name
- Duplicate treatments by (patient_id, primary_hospitalization_date, hospital_place)
"""
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'database', 'medical_new.db')

def main():
    print('DB path:', DB_PATH)
    print('DB exists:', os.path.exists(DB_PATH))
    if not os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print('\nTable counts:')
    for t in ['patients','units','diagnoses','treatments','payments']:
        try:
            cur.execute(f'SELECT COUNT(*) FROM {t}')
            c = cur.fetchone()[0]
            print(f'  - {t}: {c}')
        except Exception as e:
            print(f'  - {t}: ERROR {e}')

    print('\nPayments sample (up to 10):')
    try:
        cur.execute('''
            SELECT payment_start_date, payment_end_date, treatment_days,
                   amount_per_day, total_amount, source_file
            FROM payments
            ORDER BY payment_end_date DESC NULLS LAST
            LIMIT 10
        ''')
        rows = cur.fetchall()
        for r in rows:
            print('  ', r)
        if not rows:
            print('  (no rows)')
    except Exception as e:
        print('  payments fetch error:', e)

    print('\nDuplicate patients (same full_name) top 20:')
    try:
        cur.execute('''
            SELECT full_name, COUNT(*) AS c
            FROM patients
            GROUP BY full_name
            HAVING c > 1
            ORDER BY c DESC, full_name ASC
            LIMIT 20
        ''')
        for r in cur.fetchall():
            print('  ', r)
    except Exception as e:
        print('  error:', e)

    print('\nDuplicate treatments by (patient_id, primary_hospitalization_date, hospital_place) top 20:')
    try:
        cur.execute('''
            SELECT patient_id, primary_hospitalization_date, hospital_place, COUNT(*) AS c
            FROM treatments
            GROUP BY patient_id, primary_hospitalization_date, hospital_place
            HAVING c > 1
            ORDER BY c DESC, patient_id ASC
            LIMIT 20
        ''')
        for r in cur.fetchall():
            print('  ', r)
    except Exception as e:
        print('  error:', e)

    print('\nPayments linked to nonexistent treatments (sanity check):')
    try:
        cur.execute('''
            SELECT COUNT(*)
            FROM payments p
            LEFT JOIN treatments t
              ON t.id = p.treatment_id
            WHERE p.treatment_id IS NOT NULL AND t.id IS NULL
        ''')
        print('  orphans:', cur.fetchone()[0])
    except Exception as e:
        print('  error:', e)

    conn.close()

if __name__ == '__main__':
    main()

