# config.py
"""
Конфігурація проекту
"""

import os
import re

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Шляхи до папок
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if load_dotenv:
    load_dotenv(os.path.join(BASE_DIR, ".env"))

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
VLK_FABULA_TEMPLATE = os.path.join(TEMPLATES_DIR, 'vlk_fabula.docx')
OUTPATIENT_JOURNAL_TEMPLATE = os.path.join(TEMPLATES_DIR, 'record_keeping_for_outpatients.docx')
OUTPATIENT_JOURNAL_DB = os.path.join(DATA_DIR, 'outpatient_journal.db')
# legacy (міграція, якщо файл ще є)
OUTPATIENT_JOURNAL_FILE = os.path.join(DATA_DIR, 'outpatient_journal.xlsx')
TREATMENT_MEDIA_DIR = os.path.join(DATA_DIR, 'treatment_media')
TREATMENT_MEDIA_MAX_BYTES = 10 * 1024 * 1024  # 10 МБ
REMINDER_WITHIN_DAYS = 3

# Оплати / відомість неоплачених стаціонарів
PAYMENTS_DIR = os.path.join(DATA_DIR, 'payments')
PAYMENTS_TEMPLATE = os.path.join(TEMPLATES_DIR, 'payments_template.docx')
PAYMENTS_HISTORY_START = '2026-05-01'  # даних оплат раніше немає
COMBAT_DIAGNOSIS_PATTERNS_FILE = os.path.join(DATA_DIR, 'combat_diagnosis_patterns.json')
# У відомості лише підрозділи 2 БОП (напр. «1 РОП 2 БОП», «РВП 2 БОП»)
PAYMENTS_UNIT_FILTER = '2 БОП'
# Якщо в діагнозі немає бойового патерну, але у Excel «Бойова/ небойова» = Бойова (як у ручній відомості)
PAYMENTS_COMBAT_COLUMN_FALLBACK = True
# Червневий «хвіст» у липневій відомості: неоплачене, що закінчилось не раніше цього числа попереднього місяця
PAYMENTS_PREV_MONTH_CARRY_DAY = 18
PAYMENTS_FILE_RE = re.compile(
    r'^(january|february|march|april|may|june|july|august|september|october|november|december)_(\d{4})\.xlsx$',
    re.IGNORECASE,
)

# Тимчасова пауза перед віддачею DOCX (секунди). 0 = вимкнено.
LOADER_PREVIEW_DELAY_SEC = 0

# Налаштування Flask
SECRET_KEY = 'your-secret-key-here'
DEBUG = True

# Gemini (ключ лише з .env, не комітити)
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = (os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash").strip()

# Dropbox API path (НЕ Windows-шлях):
# App folder (Програми/patientss) → DROPBOX_ROOT_FOLDER порожній → /patients/{ПІБ}/...
# Full Dropbox → DROPBOX_ROOT_FOLDER=м-с → /м-с/patients/{ПІБ}/...
# Токен sl.u.… короткоживучий (~4 год). Для постійної роботи задайте
# DROPBOX_APP_KEY + DROPBOX_APP_SECRET + DROPBOX_REFRESH_TOKEN.
DROPBOX_ACCESS_TOKEN = (os.environ.get("DROPBOX_ACCESS_TOKEN") or "").strip()
DROPBOX_APP_KEY = (os.environ.get("DROPBOX_APP_KEY") or "").strip()
DROPBOX_APP_SECRET = (os.environ.get("DROPBOX_APP_SECRET") or "").strip()
DROPBOX_REFRESH_TOKEN = (os.environ.get("DROPBOX_REFRESH_TOKEN") or "").strip()


def _normalize_dropbox_root(raw: str) -> str:
    """
    Нормалізує корінь Dropbox API.
    Порожнє значення = корінь App folder (D:\\Dropbox\\Програми\\<app>\\).
    """
    text = (raw or "").strip().replace("\\", "/")
    if not text:
        return ""
    # прибрати диск Windows і префікс Dropbox / Програми
    text = re.sub(r"^[A-Za-z]:/", "", text)
    text = re.sub(r"^/?Dropbox/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^/?Програми/[^/]+/?", "", text, flags=re.IGNORECASE)
    text = text.strip("/")
    # якщо вказали .../patients — корінь без patients (patients додає код сам)
    if text.casefold().endswith("/patients"):
        text = text[: -len("/patients")].rstrip("/")
    elif text.casefold() == "patients":
        text = ""
    # App folder name у .env не потрібен — API вже обмежений коренем додатку
    if text.casefold() in ("patientss", "programs"):
        text = ""
    return text


# None / відсутній ключ і порожній рядок → App folder root (Програми/patientss)
_raw_dropbox_root = os.environ.get("DROPBOX_ROOT_FOLDER")
DROPBOX_ROOT_FOLDER = _normalize_dropbox_root(
    "" if _raw_dropbox_root is None else _raw_dropbox_root
)


