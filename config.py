# config.py
"""
Конфігурація проекту
"""

import os
import re

# Шляхи до папок
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
TEMP_DIR = os.path.join(BASE_DIR, 'temp')

# Excel лікувань: у data/ шукаються файли treatments_YYYY.xlsx (будь-який рік), порядок злиття — за роком зростання;
# при дублікатах пріоритет у більшого року. Опційний архів treatments_final.xlsx додається першим.
TREATMENTS_YEAR_FILE_RE = re.compile(r'^treatments_(\d{4})\.xlsx$', re.IGNORECASE)
TREATMENTS_FINAL_FILE = os.path.join(DATA_DIR, 'treatments_final.xlsx')
TREATMENTS_UPLOAD_MAX_BYTES = 80 * 1024 * 1024  # 80 МБ
BASE_UPLOAD_MAX_BYTES = 80 * 1024 * 1024  # 80 МБ

# Шаблони документів
MEDICAL_CHARACTERISTIC_TEMPLATE = os.path.join(TEMPLATES_DIR, 'medical_characteristic_template.docx')
SERVICE_CHARACTERISTIC_TEMPLATE = os.path.join(TEMPLATES_DIR, 'service_characteristic.docx')
VLK_REPORT_TEMPLATE = os.path.join(TEMPLATES_DIR, 'vlk_report.docx')
VACATION_REPORT_TEMPLATE = os.path.join(TEMPLATES_DIR, 'vacantion_report.docx')

# Налаштування Flask
SECRET_KEY = 'your-secret-key-here'
DEBUG = True


