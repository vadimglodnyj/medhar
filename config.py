# config.py
"""
Конфігурація проекту
"""

import os

# Шляхи до папок
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
TEMP_DIR = os.path.join(BASE_DIR, 'temp')

# Налаштування бази даних
DATABASE_FILE = os.path.join(DATA_DIR, 'database.xlsx')
TREATMENTS_2024_FILE = os.path.join(DATA_DIR, 'treatments_2024.xlsx')
TREATMENTS_2025_FILE = os.path.join(DATA_DIR, 'treatments_2025.xlsx')
TREATMENTS_ADAPTED_FILE = os.path.join(DATA_DIR, 'treatments_adapted.xlsx')
TREATMENTS_CLEANED_FILE = os.path.join(DATA_DIR, 'treatments_cleaned.xlsx')
TREATMENTS_FINAL_FILE = os.path.join(DATA_DIR, 'treatments_final.xlsx')

# Шаблони документів
MEDICAL_CHARACTERISTIC_TEMPLATE = os.path.join(TEMPLATES_DIR, 'medical_characteristic_template.docx')
SERVICE_CHARACTERISTIC_TEMPLATE = os.path.join(TEMPLATES_DIR, 'service_characteristic_template.docx')
VLK_REPORT_TEMPLATE = os.path.join(TEMPLATES_DIR, 'vlk_report_template.docx')

# Налаштування Flask
SECRET_KEY = 'your-secret-key-here'
DEBUG = True
