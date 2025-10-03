#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import pandas as pd

FILES = [
    os.path.join('data', 'may_2025.xlsx'),
    os.path.join('data', 'june_2025.xlsx'),
    os.path.join('data', 'july_2025.xlsx'),
    os.path.join('data', 'august_2025.xlsx'),
    os.path.join('data', 'payment.xlsx'),
]

def main():
    for path in FILES:
        print('\nFILE:', os.path.abspath(path))
        if not os.path.exists(path):
            print('  MISSING')
            continue
        try:
            df = pd.read_excel(path)
        except Exception as e:
            print('  ERROR reading:', e)
            continue
        print('  COLUMNS:', list(df.columns))
        with pd.option_context('display.max_columns', None, 'display.width', 200):
            print(df.head(5))

if __name__ == '__main__':
    main()
