#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'database', 'medical_new.db')


def find_duplicates(cur):
    cur.execute('''
        SELECT patient_id, primary_hospitalization_date, hospital_place, GROUP_CONCAT(id), COUNT(*) c
        FROM treatments
        GROUP BY 1,2,3
        HAVING c > 1
    ''')
    return cur.fetchall()


def repoint_payments(cur, keep_id, to_delete_ids):
    if not to_delete_ids:
        return 0
    qmarks = ','.join(['?'] * len(to_delete_ids))
    cur.execute(f"UPDATE payments SET treatment_id = ? WHERE treatment_id IN ({qmarks})", (keep_id, *to_delete_ids))
    return cur.rowcount


def dedupe(cur):
    dups = find_duplicates(cur)
    removed = 0
    groups = 0
    repointed = 0
    for patient_id, date, place, id_list, c in dups:
        ids = [int(x) for x in str(id_list).split(',') if x]
        ids.sort()
        keep = ids[0]
        to_delete = ids[1:]
        if not to_delete:
            continue
        # repoint payments first
        repointed += repoint_payments(cur, keep, to_delete)
        # delete duplicates
        qmarks = ','.join(['?'] * len(to_delete))
        cur.execute(f'DELETE FROM treatments WHERE id IN ({qmarks})', to_delete)
        removed += len(to_delete)
        groups += 1
    return {'groups': groups, 'removed': removed, 'payments_repointed': repointed}


def create_unique_index(cur):
    try:
        cur.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_treatments_unique_triplet
            ON treatments (patient_id, primary_hospitalization_date, hospital_place)
        ''')
    except sqlite3.OperationalError as e:
        print('Index creation error:', e)


def main():
    print('DB:', DB_PATH, 'exists=', os.path.exists(DB_PATH))
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    stats = dedupe(cur)
    create_unique_index(cur)
    conn.commit()
    print('Deduplicated:', stats)

    conn.close()

if __name__ == '__main__':
    main()

