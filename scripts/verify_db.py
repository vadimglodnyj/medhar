#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import sqlite3
import pandas as pd
from datetime import datetime


def main():
    db_path = os.environ.get('DB_PATH', 'medical_new.db')
    xlsx_path = sys.argv[1] if len(sys.argv) > 1 else None

    print(f"DB: {db_path} exists={os.path.exists(db_path)}")
    if xlsx_path:
        print(f"File: {xlsx_path} exists={os.path.exists(xlsx_path)}")
        if os.path.exists(xlsx_path):
            try:
                df = pd.read_excel(xlsx_path)
                df.columns = df.columns.astype(str).str.strip()
                print(f"Excel rows={len(df)} cols={len(df.columns)}")
                print("Columns:", list(df.columns))
                print("Sample (first 5 rows):")
                print(df.head(5).fillna('').to_string(index=False))
            except Exception as e:
                print("Excel read error:", e)

    if not os.path.exists(db_path):
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    def table_stats(tbl: str):
        today = 'n/a'
        try:
            cur.execute(f"SELECT COUNT(*) FROM {tbl} WHERE DATE(created_at)=DATE('now','localtime')")
            today = cur.fetchone()[0]
        except Exception:
            pass
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        total = cur.fetchone()[0]
        return today, total

    print("\nTable counts (today/total):")
    for t in ['patients','units','diagnoses','treatments','payments']:
        td, tot = table_stats(t)
        print(f"- {t}: today={td} total={tot}")

    print("\nRecent treatments today:")
    try:
        cur.execute(
            """
            SELECT t.id, p.full_name, t.treatment_type, t.hospital_place,
                   t.primary_hospitalization_date, t.discharge_date, t.created_at
            FROM treatments t
            LEFT JOIN patients p ON p.id=t.patient_id
            WHERE DATE(t.created_at)=DATE('now','localtime')
            ORDER BY t.created_at DESC
            LIMIT 10
            """
        )
        rows = cur.fetchall()
        for r in rows:
            print(dict(r))
    except Exception as e:
        print("Query error:", e)

    conn.close()


if __name__ == '__main__':
    main()


