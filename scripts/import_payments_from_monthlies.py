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

DATE_RE = re.compile(r"(\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{4}-\d{2}-\d{2})")


def parse_date(s: str):
    s = s.strip()
    for fmt in ('%d.%m.%Y', '%Y-%m-%d'):
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


def extract_dates(text):
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return []
    if isinstance(text, (pd.Timestamp, datetime)):
        return [text.to_pydatetime() if hasattr(text, 'to_pydatetime') else text]
    t = str(text)
    out = []
    for m in DATE_RE.findall(t.replace('\r', '\n')):
        dt = parse_date(m)
        if dt:
            out.append(dt)
    return out


def normalize_name(name):
    return re.sub(r"\s+", " ", str(name)).strip()


def load_patient_map(cur):
    cur.execute('SELECT id, full_name FROM patients')
    mp = {}
    for pid, fn in cur.fetchall():
        if fn:
            mp[normalize_name(fn).lower()] = pid
    return mp


def ensure_columns(cur):
    cur.execute('PRAGMA table_info(payments)')
    cols = {r[1] for r in cur.fetchall()}
    if 'patient_id' not in cols:
        cur.execute('ALTER TABLE payments ADD COLUMN patient_id INTEGER')


def import_file(conn, path):
    cur = conn.cursor()
    ensure_columns(cur)
    try:
        df = pd.read_excel(path)
    except Exception as e:
        print('ERR reading', path, e)
        return {'inserted': 0, 'skipped': 0}
    if df.empty:
        return {'inserted': 0, 'skipped': 0}

    cols = list(df.columns)
    # heuristics: name column has max words and Cyrillic
    def score_name_col(series: pd.Series):
        s = 0
        for v in series.head(100).astype(str):
            parts = v.strip().split()
            if 2 <= len(parts) <= 4:
                s += 1
        return s
    name_col = max(cols, key=lambda c: score_name_col(df[c]))

    # date cols: two columns with most extracted dates
    def volume(col):
        total = 0
        for v in df[col].head(200):
            total += len(extract_dates(v))
        return total
    scores = sorted([(volume(c), c) for c in cols], reverse=True)
    date_cols = [c for _, c in scores[:3]]
    if len(date_cols) < 2:
        return {'inserted': 0, 'skipped': 0}
    start_col, end_col = date_cols[0], date_cols[1]

    patient_map = load_patient_map(cur)
    inserted = 0
    skipped = 0

    for _, row in df.iterrows():
        name = normalize_name(row.get(name_col))
        if not name:
            continue
        pid = patient_map.get(name.lower())
        if not pid:
            skipped += 1
            continue
        starts = extract_dates(row.get(start_col))
        ends = extract_dates(row.get(end_col))
        for i, sdt in enumerate(starts):
            edt = ends[i] if i < len(ends) else sdt
            s_str = sdt.strftime('%Y-%m-%d %H:%M:%S')
            e_str = edt.strftime('%Y-%m-%d %H:%M:%S')
            # dedupe
            cur.execute('SELECT id FROM payments WHERE patient_id=? AND payment_start_date=? AND payment_end_date=?', (pid, s_str, e_str))
            if cur.fetchone():
                continue
            cur.execute('''
                INSERT INTO payments (
                    patient_id, payment_start_date, payment_end_date,
                    treatment_days, amount_per_day, total_amount,
                    payment_month, payment_year, source_file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                pid, s_str, e_str, None, 3300.0, None, sdt.month, sdt.year, path
            ))
            inserted += 1
    conn.commit()
    return {'inserted': inserted, 'skipped': skipped}


def main():
    print('DB:', DB_PATH, 'exists=', os.path.exists(DB_PATH))
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    total = {'inserted': 0, 'skipped': 0}
    for path in FILES:
        if os.path.exists(path):
            res = import_file(conn, path)
            print(os.path.abspath(path), res)
            total['inserted'] += res['inserted']
            total['skipped'] += res['skipped']
    print('TOTAL:', total)
    conn.close()

if __name__ == '__main__':
    main()
