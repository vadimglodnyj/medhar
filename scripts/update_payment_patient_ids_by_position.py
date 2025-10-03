#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sqlite3
import pandas as pd
import re
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'database', 'medical_new.db')
FILES = [
    os.path.join('data', 'may_2025.xlsx'),
    os.path.join('data', 'june_2025.xlsx'),
    os.path.join('data', 'july_2025.xlsx'),
    os.path.join('data', 'august_2025.xlsx'),
    os.path.join('data', 'payment.xlsx'),
]
NAME_COL_IDX = 3
START_COL_IDX = 6
END_COL_IDX = 7
DATE_RE = re.compile(r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?)")


def parse_date(s: str):
    s = s.strip()
    for fmt in ('%d.%m.%Y', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    try:
        v = pd.to_datetime(s, dayfirst=True, errors='coerce')
        if pd.isna(v):
            return None
        return v.to_pydatetime()
    except Exception:
        return None


def extract_dates(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return []
    if isinstance(val, (pd.Timestamp, datetime)):
        return [val.to_pydatetime() if hasattr(val, 'to_pydatetime') else val]
    text = str(val)
    parts = DATE_RE.findall(text)
    out = []
    for p in parts:
        dt = parse_date(p)
        if dt:
            out.append(dt)
    return out


def load_patient_map(cur):
    cur.execute('SELECT id, full_name FROM patients')
    mp = {}
    for pid, name in cur.fetchall():
        if not name:
            continue
        key = re.sub(r'\s+', ' ', str(name)).strip().lower()
        mp[key] = pid
    return mp


def ensure_columns(cur):
    cur.execute("PRAGMA table_info(payments)")
    cols = {r[1] for r in cur.fetchall()}
    if 'patient_id' not in cols:
        cur.execute('ALTER TABLE payments ADD COLUMN patient_id INTEGER')


def update_from_file(conn, path, patient_map):
    if not os.path.exists(path):
        print('MISS', path)
        return 0
    print('FILE:', os.path.abspath(path))
    try:
        df = pd.read_excel(path)
    except Exception as e:
        print('  read error:', e)
        return 0
    if df.empty:
        return 0
    cols = list(df.columns)
    if len(cols) <= max(NAME_COL_IDX, START_COL_IDX, END_COL_IDX):
        print('  not enough columns')
        return 0

    name_col = cols[NAME_COL_IDX]
    start_col = cols[START_COL_IDX]
    end_col = cols[END_COL_IDX]

    cur = conn.cursor()
    ensure_columns(cur)

    # Build payment index by (start,end) for this file
    cur.execute('SELECT id, payment_start_date, payment_end_date FROM payments WHERE source_file = ? AND (patient_id IS NULL OR patient_id = "")', (path.replace('\\', '/'),))
    pay_index = {}
    for pid, ps, pe in cur.fetchall():
        try:
            s = pd.to_datetime(ps)
            e = pd.to_datetime(pe)
            key = (s.strftime('%Y-%m-%d %H:%M:%S'), e.strftime('%Y-%m-%d %H:%M:%S'))
            pay_index.setdefault(key, []).append(pid)
        except Exception:
            continue

    updated = 0
    for _, row in df.iterrows():
        name_raw = row.get(name_col)
        full_name = re.sub(r'\s+', ' ', str(name_raw)).strip().lower() if pd.notna(name_raw) else ''
        if not full_name:
            continue
        patient_id = patient_map.get(full_name)
        if not patient_id:
            continue
        starts = extract_dates(row.get(start_col))
        ends = extract_dates(row.get(end_col))
        for i, sdt in enumerate(starts):
            edt = ends[i] if i < len(ends) else sdt
            s_key = sdt.strftime('%Y-%m-%d %H:%M:%S')
            e_key = edt.strftime('%Y-%m-%d %H:%M:%S')
            ids = pay_index.get((s_key, e_key))
            if not ids:
                continue
            for pay_id in ids:
                cur.execute('UPDATE payments SET patient_id = ? WHERE id = ? AND (patient_id IS NULL OR patient_id = "")', (patient_id, pay_id))
                if cur.rowcount:
                    updated += 1
    conn.commit()
    print('  updated:', updated)
    return updated


def main():
    print('DB:', DB_PATH, 'exists=', os.path.exists(DB_PATH))
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    ensure_columns(cur)
    patient_map = load_patient_map(cur)

    total = 0
    for path in FILES:
        total += update_from_file(conn, path, patient_map)

    print('TOTAL updated:', total)

if __name__ == '__main__':
    main()
