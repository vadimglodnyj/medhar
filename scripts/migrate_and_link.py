#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'database', 'medical_new.db')


def get_schema(cur):
    cur.execute('PRAGMA table_info(payments)')
    pay_cols = [r[1] for r in cur.fetchall()]
    cur.execute('PRAGMA table_info(treatments)')
    tr_cols = [r[1] for r in cur.fetchall()]
    return pay_cols, tr_cols


def ensure_treatment_id(cur):
    pay_cols, _ = get_schema(cur)
    if 'treatment_id' not in pay_cols:
        cur.execute('ALTER TABLE payments ADD COLUMN treatment_id INTEGER')
        return True
    return False


def fetch_payments(cur):
    cur.execute('''
        SELECT id, patient_id, payment_start_date, payment_end_date
        FROM payments
        ORDER BY payment_start_date
    ''')
    rows = cur.fetchall()
    res = []
    for pid, pat, s, e in rows:
        # Actually row order is (id, patient_id, ...)
        pass
    # Re-fetch with names to avoid mistakes
    cur.execute('''
        SELECT id, patient_id, payment_start_date, payment_end_date
        FROM payments
        ORDER BY payment_start_date
    ''')
    payments = []
    for r in cur.fetchall():
        payments.append({'id': r[0], 'patient_id': r[1], 'start': r[2], 'end': r[3]})
    return payments


def fetch_treatments_by_patient(cur, patient_id):
    cur.execute('''
        SELECT id, primary_hospitalization_date, discharge_date, hospital_place
        FROM treatments
        WHERE patient_id = ?
        ORDER BY primary_hospitalization_date
    ''', (patient_id,))
    ts = []
    for r in cur.fetchall():
        ts.append({'id': r[0], 'start': r[1], 'end': r[2], 'place': r[3]})
    return ts


def to_dt(v):
    if not v:
        return None
    try:
        # already ISO-like
        return datetime.fromisoformat(str(v).split('.')[0])
    except Exception:
        try:
            return datetime.strptime(str(v), '%Y-%m-%d %H:%M:%S')
        except Exception:
            try:
                return datetime.strptime(str(v), '%Y-%m-%d')
            except Exception:
                return None


def overlap(a_start, a_end, b_start, b_end):
    if not a_start or not b_start:
        return 0
    a_e = a_end or a_start
    b_e = b_end or b_start
    start = max(a_start, b_start)
    end = min(a_e, b_e)
    delta = (end - start).days
    return delta + 1 if delta >= 0 else 0


def link_payments(cur):
    payments = fetch_payments(cur)
    linked = 0
    ambiguous = 0
    no_match = 0
    for p in payments:
        pat = p['patient_id']
        ps = to_dt(p['start'])
        pe = to_dt(p['end'])
        if pat is None or ps is None:
            no_match += 1
            continue
        treatments = fetch_treatments_by_patient(cur, pat)
        # score by overlap days
        scored = []
        for t in treatments:
            ts = to_dt(t['start'])
            te = to_dt(t['end'])
            ov = overlap(ts, te, ps, pe)
            if ov > 0:
                scored.append((ov, t))
        if not scored:
            no_match += 1
            continue
        scored.sort(key=lambda x: (-x[0], x[1]['id']))
        best_ov = scored[0][0]
        best = [t for ov, t in scored if ov == best_ov]
        if len(best) > 1:
            # ambiguous, pick first deterministically but count
            ambiguous += 1
        sel = best[0]
        cur.execute('UPDATE payments SET treatment_id = ? WHERE id = ?', (sel['id'], p['id']))
        linked += 1
    return {'linked': linked, 'ambiguous': ambiguous, 'no_match': no_match, 'total': len(payments)}


def dedupe_candidates(cur, limit=50):
    cur.execute('''
        SELECT patient_id, primary_hospitalization_date, hospital_place, COUNT(*) c
        FROM treatments
        GROUP BY 1,2,3
        HAVING c > 1
        ORDER BY c DESC
        LIMIT ?
    ''', (limit,))
    return cur.fetchall()


def main():
    print('DB:', DB_PATH, 'exists=', os.path.exists(DB_PATH))
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    added = ensure_treatment_id(cur)
    if added:
        print('Added payments.treatment_id')
    conn.commit()

    stats = link_payments(cur)
    conn.commit()
    print('Linking payments -> treatments:', stats)

    dups = dedupe_candidates(cur)
    print('Duplicate treatment key groups (top):')
    for row in dups:
        print(' ', row)

    conn.close()

if __name__ == '__main__':
    main()

