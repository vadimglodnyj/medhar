#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sqlite3
import pandas as pd
import re
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'database', 'medical_new.db')
PAYMENT_FILES = [
    os.path.join('data', 'may_2025.xlsx'),
    os.path.join('data', 'june_2025.xlsx'),
    os.path.join('data', 'july_2025.xlsx'),
    os.path.join('data', 'august_2025.xlsx'),
    os.path.join('data', 'payment.xlsx'),
]

DATE_RE = re.compile(r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?)")
CYR_CHARS = set("АБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯабвгґдеєжзиіїйклмнопрстуфхцчшщьюя'’ -")


def parse_single_date(text):
    if not text:
        return None
    text = text.strip()
    for fmt in ('%d.%m.%Y', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    try:
        v = pd.to_datetime(text, dayfirst=True, errors='coerce')
        if pd.isna(v):
            return None
        return v.to_pydatetime()
    except Exception:
        return None


def extract_dates_from_cell(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return []
    # Already a datetime-like
    if isinstance(val, (pd.Timestamp, datetime)):
        return [val.to_pydatetime() if hasattr(val, 'to_pydatetime') else val]
    text = str(val)
    parts = DATE_RE.findall(text)
    dates = []
    for p in parts:
        dt = parse_single_date(p)
        if dt:
            dates.append(dt)
    return dates


def column_date_volume(series: pd.Series) -> int:
    total = 0
    for v in series:
        total += len(extract_dates_from_cell(v))
    return total


def is_likely_name(value: str) -> bool:
    if not value:
        return False
    text = str(value)
    # filter out if mostly non-cyrillic
    cyr_ratio = sum(1 for ch in text if ch in CYR_CHARS) / max(1, len(text))
    if cyr_ratio < 0.5:
        return False
    parts = [p for p in re.split(r"\s+", text.strip()) if p]
    return 2 <= len(parts) <= 4


def ensure_patient_id_column(cur):
    cur.execute("PRAGMA table_info(payments)")
    cols = [r[1] for r in cur.fetchall()]
    if 'patient_id' not in cols:
        cur.execute("ALTER TABLE payments ADD COLUMN patient_id INTEGER")
    if 'treatment_id' not in cols:
        cur.execute("ALTER TABLE payments ADD COLUMN treatment_id INTEGER")


def load_patients(cur):
    cur.execute("SELECT id, full_name FROM patients")
    rows = cur.fetchall()
    mapping = {}
    for pid, name in rows:
        if not name:
            continue
        key = re.sub(r"\s+", " ", name).strip().lower()
        mapping[key] = pid
    return mapping


def backfill_from_file(conn, path, patient_map):
    print('Processing file:', os.path.abspath(path))
    try:
        df = pd.read_excel(path)
    except Exception as e:
        print('  read error:', e)
        return 0
    if df.empty:
        return 0

    # Pick two date-rich columns
    volumes = []
    for col in df.columns:
        try:
            vol = column_date_volume(df[col])
        except Exception:
            vol = 0
        volumes.append((vol, col))
    volumes.sort(reverse=True)
    if not volumes or volumes[0][0] == 0:
        print('  no date-like columns found')
        return 0
    date_cols = [c for _, c in volumes[:3]]
    if len(date_cols) < 2:
        print('  insufficient date columns')
        return 0
    start_col, end_col = date_cols[0], date_cols[1]

    # Pick name column by name-likeness score
    best_name_col = None
    best_score = -1
    for col in df.columns:
        series = df[col].astype(str).fillna('')
        score = sum(1 for v in series[:500] if is_likely_name(v))
        if score > best_score:
            best_score = score
            best_name_col = col
    if best_name_col is None:
        print('  no likely name column found, using positional fallback index 3')
        try:
            best_name_col = df.columns[3]
        except Exception:
            return 0

    cur = conn.cursor()
    updated = 0

    # Pick start/end columns; if detection yielded same or invalid, use positional fallbacks
    if not start_col or not end_col or start_col == end_col:
        try:
            start_col = df.columns[6]
            end_col = df.columns[7]
            print(f'  using positional date columns: start={start_col}, end={end_col}')
        except Exception:
            print('  cannot use positional date columns')
            return 0

    # Preload payments for this source file
    cur.execute("SELECT id, payment_start_date, payment_end_date FROM payments WHERE source_file = ? AND (patient_id IS NULL OR patient_id = '')", (path.replace('\\', '/'),))
    payments = cur.fetchall()
    # Build index by (start,end) strings for faster lookup
    pay_index = {}
    for pid, ps, pe in payments:
        try:
            s = pd.to_datetime(ps)
            e = pd.to_datetime(pe)
            key = (s.strftime('%Y-%m-%d %H:%M:%S'), e.strftime('%Y-%m-%d %H:%M:%S'))
            pay_index.setdefault(key, []).append(pid)
        except Exception:
            continue

    if not pay_index:
        # fallback: do not filter by source_file
        cur.execute("SELECT id, payment_start_date, payment_end_date FROM payments WHERE (patient_id IS NULL OR patient_id = '')")
        payments = cur.fetchall()
        pay_index = {}
        for pid, ps, pe in payments:
            try:
                s = pd.to_datetime(ps)
                e = pd.to_datetime(pe)
                key = (s.strftime('%Y-%m-%d %H:%M:%S'), e.strftime('%Y-%m-%d %H:%M:%S'))
                pay_index.setdefault(key, []).append(pid)
            except Exception:
                continue

    for _, row in df.iterrows():
        name_raw = row.get(best_name_col)
        full_name = re.sub(r"\s+", " ", str(name_raw)).strip().lower() if pd.notna(name_raw) else ''
        if not full_name:
            continue
        patient_id = patient_map.get(full_name)
        if not patient_id:
            continue
        starts = extract_dates_from_cell(row.get(start_col))
        ends = extract_dates_from_cell(row.get(end_col))
        # Pair by position; if ends shorter, reuse last
        for i, sdt in enumerate(starts):
            edt = ends[i] if i < len(ends) else sdt
            s_key = sdt.strftime('%Y-%m-%d %H:%M:%S')
            e_key = edt.strftime('%Y-%m-%d %H:%M:%S')
            ids = pay_index.get((s_key, e_key))
            if not ids:
                continue
            for pay_id in ids:
                cur.execute("UPDATE payments SET patient_id = ? WHERE id = ? AND (patient_id IS NULL OR patient_id = '')", (patient_id, pay_id))
                if cur.rowcount:
                    updated += 1
    conn.commit()
    print('  updated from file:', updated)
    return updated


def backfill_patient_id(conn):
    cur = conn.cursor()
    ensure_patient_id_column(cur)
    patient_map = load_patients(cur)

    total_updated = 0
    for path in PAYMENT_FILES:
        if os.path.exists(path):
            total_updated += backfill_from_file(conn, path, patient_map)
    return total_updated


def link_payments_to_treatments(conn):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(payments)")
    cols = [r[1] for r in cur.fetchall()]
    if 'treatment_id' not in cols:
        cur.execute("ALTER TABLE payments ADD COLUMN treatment_id INTEGER")
        conn.commit()

    cur.execute(
        """
        SELECT id, patient_id, payment_start_date, payment_end_date
        FROM payments
        WHERE patient_id IS NOT NULL
        """
    )
    payments = cur.fetchall()

    def to_dt(s):
        if s is None:
            return None
        return pd.to_datetime(s)

    linked = 0
    ambiguous = 0
    no_match = 0

    for pay_id, patient_id, ps, pe in payments:
        p_start = to_dt(ps)
        p_end = to_dt(pe)
        if p_start is None or p_end is None:
            no_match += 1
            continue
        cur.execute(
            """
            SELECT id, primary_hospitalization_date, discharge_date
            FROM treatments
            WHERE patient_id = ?
            """,
            (patient_id,)
        )
        cands = []
        for tid, ts, te in cur.fetchall():
            ts_dt = to_dt(ts)
            te_dt = to_dt(te)
            if ts_dt is None or te_dt is None:
                continue
            if max(ts_dt, p_start) <= min(te_dt, p_end):
                cands.append((tid, min(te_dt, p_end) - max(ts_dt, p_start)))
        if len(cands) == 1:
            cur.execute("UPDATE payments SET treatment_id = ? WHERE id = ?", (cands[0][0], pay_id))
            linked += 1
        elif len(cands) == 0:
            no_match += 1
        else:
            # pick max overlap
            cands.sort(key=lambda x: x[1], reverse=True)
            best_tid = cands[0][0]
            # if tie on overlap, mark ambiguous
            if len(cands) > 1 and cands[0][1] == cands[1][1]:
                ambiguous += 1
            else:
                cur.execute("UPDATE payments SET treatment_id = ? WHERE id = ?", (best_tid, pay_id))
                linked += 1
    conn.commit()
    return {'linked': linked, 'ambiguous': ambiguous, 'no_match': no_match, 'total': len(payments)}


def main():
    print('DB:', DB_PATH, 'exists=', os.path.exists(DB_PATH))
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)

    updated = backfill_patient_id(conn)
    print('payments.patient_id updated:', updated)

    res = link_payments_to_treatments(conn)
    print('linking result:', res)

    conn.close()

if __name__ == '__main__':
    main()
