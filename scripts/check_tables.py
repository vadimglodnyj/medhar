#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Перевірка таблиць в базі даних
"""

import sqlite3

def check_tables():
    """Перевірка таблиць в базі даних"""
    conn = sqlite3.connect('medical.db')
    cursor = conn.execute('SELECT name FROM sqlite_master WHERE type="table"')
    tables = [row[0] for row in cursor.fetchall()]
    print("Таблиці в базі даних:", tables)
    conn.close()

if __name__ == "__main__":
    check_tables()

