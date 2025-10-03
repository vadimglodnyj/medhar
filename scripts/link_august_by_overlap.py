#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sqlite3
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'database', 'medical_new.db')


def to_dt(val):
    if val is None:
        return None
    try:
        return pd.to_datetime(val)
    except Exception:
        try:
            return pd.to_datetime(str(val), dayfirst=True, errors='coerce')
        except Exception:
            return None


def main():
    print('DB:', DB_PATH, 'exists=', os.path.exists(DB_PATH))
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Ensure needed columns
    cur.execute('PRAGMA table_info(payments)')
    cols = {r[1] for r in cur.fetchall()}
    if 'patient_id' not in cols:
        cur.execute('ALTER TABLE payments ADD COLUMN patient_id INTEGER')
    if 'treatment_id' not in cols:
        cur.execute('ALTER TABLE payments ADD COLUMN treatment_id INTEGER')

    # Load August payments without patient_id
    cur.execute("""
        SELECT id, payment_start_date, payment_end_date
        FROM payments
        WHERE (patient_id IS NULL OR patient_id = '') AND source_file LIKE '%august_2025.xlsx%'
    """)
    august_payments = cur.fetchall()

    # Load treatments grouped by patient
    cur.execute("SELECT id, patient_id, primary_hospitalization_date, discharge_date, treatment_type FROM treatments")
    treatments = cur.fetchall()
    by_patient = {}
    for tid, pid, ts, te, tt in treatments:
        by_patient.setdefault(pid, []).append((tid, ts, te, tt))

    set_pid = 0
    set_tid = 0

    for pay_id, ps, pe in august_payments:
        p_start = to_dt(ps)
        p_end = to_dt(pe)
        if p_start is None or p_end is None:
            continue
        candidates = []
        for pid, tre_list in by_patient.items():
            if pid is None:
                continue
            # any overlap counts as candidate
            has = False
            best_overlap = 0
            best_tid = None
            for tid, ts, te, tt in tre_list:
                ts_dt = to_dt(ts)
                te_dt = to_dt(te)
                if ts_dt is None:
                    continue
                if te_dt is None:
                    te_dt = ts_dt  # conservative
                latest_start = max(p_start, ts_dt)
                earliest_end = min(p_end, te_dt)
                overlap_days = (earliest_end - latest_start).days + 1
                if overlap_days >= 1:
                    has = True
                    if overlap_days > best_overlap:
                        best_overlap = overlap_days
                        best_tid = tid
            if has:
                candidates.append((pid, best_overlap, best_tid))
        # Keep only clear unique best (strictly greater than next)
        if not candidates:
            continue
        candidates.sort(key=lambda x: x[1], reverse=True)
        if len(candidates) == 1 or (len(candidates) > 1 and candidates[0][1] > candidates[1][1]):
            best_pid, _, best_tid = candidates[0]
            cur.execute("UPDATE payments SET patient_id = ? WHERE id = ?", (best_pid, pay_id))
            set_pid += 1
            if best_tid is not None:
                cur.execute("UPDATE payments SET treatment_id = ? WHERE id = ?", (best_tid, pay_id))
                set_tid += 1

    conn.commit()
    print({'august_payments': len(august_payments), 'patient_id_set': set_pid, 'treatment_id_set': set_tid})
    conn.close()

if __name__ == '__main__':
    main()
