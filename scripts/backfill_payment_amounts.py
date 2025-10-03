#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sqlite3
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'database', 'medical_new.db')


def main():
    print('DB:', DB_PATH, 'exists=', os.path.exists(DB_PATH))
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Select payments needing backfill
    cur.execute(
        """
        SELECT id, payment_start_date, payment_end_date, treatment_days, amount_per_day, total_amount
        FROM payments
        WHERE treatment_days IS NULL OR treatment_days = 0 OR total_amount IS NULL OR amount_per_day IS NULL
        """
    )
    rows = cur.fetchall()
    updated = 0

    for pid, ps, pe, days, per_day, total in rows:
        try:
            s = pd.to_datetime(ps)
            e = pd.to_datetime(pe)
        except Exception:
            continue
        if s is None or pd.isna(s) or e is None or pd.isna(e):
            continue
        new_days = int((e - s).days) + 1
        new_per_day = per_day if per_day is not None else 3300.0
        new_total = total if total is not None else float(new_days) * float(new_per_day)
        # If we had values but zero, recompute
        if days in (None, 0):
            days = new_days
        if per_day is None:
            per_day = new_per_day
        if total is None:
            total = new_total
        cur.execute(
            "UPDATE payments SET treatment_days = ?, amount_per_day = ?, total_amount = ? WHERE id = ?",
            (days, per_day, total, pid),
        )
        updated += 1

    conn.commit()
    print('Updated payments:', updated)
    conn.close()

if __name__ == '__main__':
    main()
