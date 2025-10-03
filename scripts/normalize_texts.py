#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sqlite3
import unicodedata
import re

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'database', 'medical_new.db')

CONTROL_RE = re.compile(r'[\u0000-\u001F\u007F\u00A0]')  # include nbsp


def clean_text(s: str) -> str:
    if s is None:
        return None
    # normalize unicode and replace control/nbsp with space
    t = unicodedata.normalize('NFKC', str(s))
    t = CONTROL_RE.sub(' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def normalize_treatments(cur):
    cur.execute('SELECT id, hospital_place FROM treatments')
    rows = cur.fetchall()
    updates = 0
    for tid, place in rows:
        new_place = clean_text(place)
        if new_place != place:
            cur.execute('UPDATE treatments SET hospital_place = ? WHERE id = ?', (new_place, tid))
            updates += 1
    return updates


def normalize_diagnoses(cur):
    cur.execute('SELECT id, diagnosis_text FROM diagnoses')
    rows = cur.fetchall()
    updates = 0
    for did, dx in rows:
        new_dx = clean_text(dx)
        if new_dx != dx:
            cur.execute('UPDATE diagnoses SET diagnosis_text = ? WHERE id = ?', (new_dx, did))
            updates += 1
    return updates


def main():
    print('DB:', DB_PATH, 'exists=', os.path.exists(DB_PATH))
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    try:
        t_upd = normalize_treatments(cur)
    except sqlite3.OperationalError:
        t_upd = 0
    try:
        d_upd = normalize_diagnoses(cur)
    except sqlite3.OperationalError:
        d_upd = 0

    conn.commit()
    print('Normalized:', {'treatments.hospital_place': t_upd, 'diagnoses.diagnosis_text': d_upd})

    conn.close()

if __name__ == '__main__':
    main()

