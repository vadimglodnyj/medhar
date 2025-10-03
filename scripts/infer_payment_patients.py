#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sqlite3
import pandas as pd
from datetime import datetime, timedelta

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


def normalize_period(start_dt, end_dt, default_days_if_missing=30):
    if start_dt is None:
        return None, None
    if end_dt is None:
        end_dt = start_dt + timedelta(days=default_days_if_missing)
    return start_dt, end_dt


def overlap_days(a_start, a_end, b_start, b_end):
    if a_start is None or b_start is None:
        return 0
    a_end = a_end or a_start
    b_end = b_end or b_start
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    delta = (earliest_end - latest_start).days
    return max(0, delta + 1)


def ensure_columns(cur):
    cur.execute("PRAGMA table_info(payments)")
    cols = {r[1] for r in cur.fetchall()}
    if 'patient_id' not in cols:
        cur.execute("ALTER TABLE payments ADD COLUMN patient_id INTEGER")
    if 'treatment_id' not in cols:
        cur.execute("ALTER TABLE payments ADD COLUMN treatment_id INTEGER")


def fetch_payments(cur):
    cur.execute("SELECT id, payment_start_date, payment_end_date, patient_id FROM payments")
    return cur.fetchall()


def fetch_treatments_by_patient(cur):
    cur.execute("SELECT id, patient_id, primary_hospitalization_date, discharge_date FROM treatments")
    rows = cur.fetchall()
    by_patient = {}
    for tid, pid, ts, te in rows:
        by_patient.setdefault(pid, []).append((tid, ts, te))
    return by_patient


def infer_patient_for_payment(by_patient, p_start, p_end):
    best_pid = None
    best_days = 0
    second_best = 0
    for pid, treatments in by_patient.items():
        total = 0
        for tid, ts, te in treatments:
            ts_dt = to_dt(ts)
            te_dt = to_dt(te)
            ts_dt, te_dt = normalize_period(ts_dt, te_dt, default_days_if_missing=30)
            total += overlap_days(p_start, p_end, ts_dt, te_dt)
        if total > best_days:
            second_best = best_days
            best_days = total
            best_pid = pid
        elif total > second_best:
            second_best = total
    # Require clear signal: at least 3 days and strictly greater than runner-up by 2 days
    if best_days >= 3 and best_days >= second_best + 2:
        return best_pid, best_days
    return None, 0


def pick_treatment_for_payment(treatments, p_start, p_end):
    best_tid = None
    best = 0
    runner = 0
    for tid, ts, te in treatments:
        ts_dt = to_dt(ts)
        te_dt = to_dt(te)
        ts_dt, te_dt = normalize_period(ts_dt, te_dt, default_days_if_missing=30)
        d = overlap_days(p_start, p_end, ts_dt, te_dt)
        if d > best:
            runner = best
            best = d
            best_tid = tid
        elif d > runner:
            runner = d
    # Require at least 3 days and clear margin
    if best >= 3 and best >= runner + 2:
        return best_tid, best
    return None, 0


def main():
    print('DB:', DB_PATH, 'exists=', os.path.exists(DB_PATH))
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    ensure_columns(cur)

    payments = fetch_payments(cur)
    by_patient = fetch_treatments_by_patient(cur)

    set_pid = 0
    set_tid = 0

    for pay_id, ps, pe, existing_pid in payments:
        p_start = to_dt(ps)
        p_end = to_dt(pe)
        if p_start is None:
            continue
        pid = existing_pid
        if pid is None:
            inf_pid, score = infer_patient_for_payment(by_patient, p_start, p_end)
            if inf_pid is not None:
                cur.execute("UPDATE payments SET patient_id = ? WHERE id = ?", (inf_pid, pay_id))
                pid = inf_pid
                set_pid += 1
        if pid is not None and pid in by_patient:
            tid, d = pick_treatment_for_payment(by_patient[pid], p_start, p_end)
            if tid is not None:
                cur.execute("UPDATE payments SET treatment_id = ? WHERE id = ?", (tid, pay_id))
                set_tid += 1

    conn.commit()
    print({'patient_id_set': set_pid, 'treatment_id_set': set_tid, 'total_payments': len(payments)})
    conn.close()

if __name__ == '__main__':
    main()
