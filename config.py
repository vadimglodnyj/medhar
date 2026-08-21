# config.py
"""
Конфігурація проекту
"""

import os
import re
import sys

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from utils.team_vault import (
    read_session,
    resolve_excel_dir,
    session_path,
    team_env_path,
    vault_exists,
    write_session,
)


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _desktop_mode() -> bool:
    if _is_frozen():
        return True
    flag = (os.environ.get("MEDHAR_DESKTOP") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _resource_root() -> str:
    """Read-only: templates/static/seed у бандлі або корені репозиторію."""
    if _is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _user_data_root() -> str:
    """Записуваний корінь (AppData\\Medhar у desktop, інакше корінь проєкту)."""
    if _desktop_mode():
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(local, "Medhar")
    return _resource_root()


RESOURCE_ROOT = _resource_root()
USER_DATA_ROOT = _user_data_root()

# Версія desktop/Install.exe (синхронізується з desktop/installer.iss у build.ps1)
APP_VERSION = "1.2.22"


def _load_env_files() -> None:
    """Завантажує .env користувача, потім team.env якщо обрано режим команди."""
    if not load_dotenv:
        return
    env_user = os.path.join(USER_DATA_ROOT, ".env")
    env_res = os.path.join(RESOURCE_ROOT, ".env")
    # Базовий .env (AppData у desktop, інакше корінь репо / бандла)
    if os.path.isfile(env_user):
        load_dotenv(env_user, override=True)
    if os.path.isfile(env_res):
        # У desktop репозиторний .env підхоплюємо лише якщо AppData ще порожній
        # від секретів — зручно для .\desktop\dev.ps1 на машині розробника.
        if not _desktop_mode() or not os.path.isfile(env_user):
            load_dotenv(env_res, override=True)
        elif not (os.environ.get("TURSO_AUTH_TOKEN") or os.environ.get("GEMINI_API_KEY")):
            load_dotenv(env_res, override=False)

    mode = read_session(session_path(USER_DATA_ROOT))
    team_env = team_env_path(USER_DATA_ROOT)
    if mode == "team" and os.path.isfile(team_env):
        load_dotenv(team_env, override=True)
        os.environ["MEDHAR_MODE"] = "team"
    elif mode == "local":
        os.environ["MEDHAR_MODE"] = "local"


_load_env_files()

# Маніфест оновлення: за замовчуванням Dropbox /releases/update_manifest.json
# (режим команди). Можна перевизначити MEDHAR_UPDATE_URL=https://… або dropbox:/path
APP_UPDATE_MANIFEST_URL = (
    os.environ.get("MEDHAR_UPDATE_URL") or "dropbox:/releases/update_manifest.json"
).strip()


def _compute_excel_data_dir() -> str:
    raw = (
        os.environ.get("MEDHAR_EXCEL_DIR")
        or os.environ.get("MEDHAR_DATA_DIR")
        or ""
    ).strip().strip('"')
    if raw:
        raw = os.path.expanduser(os.path.expandvars(raw))
        if not os.path.isabs(raw):
            raw = os.path.join(USER_DATA_ROOT, raw)
        return os.path.normpath(raw)

    sub = (os.environ.get("MEDHAR_EXCEL_SUBPATH") or "").strip()
    if sub:
        resolved = resolve_excel_dir(sub)
        if resolved:
            return resolved

    return os.path.join(USER_DATA_ROOT, "data")


def _normalize_dropbox_root(raw: str) -> str:
    """
    Нормалізує корінь Dropbox API.
    Порожнє значення = корінь App folder (D:\\Dropbox\\Програми\\<app>\\).
    """
    text = (raw or "").strip().replace("\\", "/")
    if not text:
        return ""
    text = re.sub(r"^[A-Za-z]:/", "", text)
    text = re.sub(r"^/?Dropbox/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^/?Програми/[^/]+/?", "", text, flags=re.IGNORECASE)
    text = text.strip("/")
    if text.casefold().endswith("/patients"):
        text = text[: -len("/patients")].rstrip("/")
    elif text.casefold() == "patients":
        text = ""
    if text.casefold() in ("patientss", "programs"):
        text = ""
    return text


def refresh_runtime_config() -> None:
    """Перечитує env-залежні поля після входу в команду / виходу в локальний режим."""
    global DATA_DIR, EXCEL_DATA_DIR, TEMP_DIR
    global MEDHAR_MODE, TURSO_DATABASE_URL, TURSO_AUTH_TOKEN, USE_TURSO, USE_TURSO_SYNC
    global TREATMENTS_FINAL_FILE, OUTPATIENT_JOURNAL_DB, OUTPATIENT_JOURNAL_FILE
    global TREATMENT_MEDIA_DIR, PAYMENTS_DIR, COMBAT_DIAGNOSIS_PATTERNS_FILE
    global GEMINI_API_KEY, GEMINI_MODEL
    global WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID
    global WHATSAPP_RECIPIENT, WHATSAPP_API_VERSION
    global DROPBOX_ACCESS_TOKEN, DROPBOX_APP_KEY, DROPBOX_APP_SECRET
    global DROPBOX_REFRESH_TOKEN, DROPBOX_ROOT_FOLDER
    global SESSION_MODE, NEEDS_TEAM_UNLOCK, HAS_TEAM_VAULT

    DATA_DIR = os.path.join(USER_DATA_ROOT, "data")
    EXCEL_DATA_DIR = _compute_excel_data_dir()
    TEMP_DIR = os.path.join(USER_DATA_ROOT, "temp")

    MEDHAR_MODE = (os.environ.get("MEDHAR_MODE") or "local").strip().lower()
    TURSO_DATABASE_URL = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
    TURSO_AUTH_TOKEN = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
    if MEDHAR_MODE == "local":
        TURSO_DATABASE_URL = ""
        TURSO_AUTH_TOKEN = ""
    if bool(TURSO_DATABASE_URL) != bool(TURSO_AUTH_TOKEN):
        raise RuntimeError(
            "Для Turso потрібно задати разом TURSO_DATABASE_URL і TURSO_AUTH_TOKEN"
        )
    USE_TURSO = bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)
    USE_TURSO_SYNC = USE_TURSO and MEDHAR_MODE == "team"

    TREATMENTS_FINAL_FILE = os.path.join(EXCEL_DATA_DIR, "treatments_final.xlsx")
    OUTPATIENT_JOURNAL_DB = os.path.join(DATA_DIR, "outpatient_journal.db")
    OUTPATIENT_JOURNAL_FILE = os.path.join(DATA_DIR, "outpatient_journal.xlsx")
    TREATMENT_MEDIA_DIR = os.path.join(DATA_DIR, "treatment_media")
    PAYMENTS_DIR = os.path.join(DATA_DIR, "payments")
    COMBAT_DIAGNOSIS_PATTERNS_FILE = os.path.join(
        DATA_DIR, "combat_diagnosis_patterns.json"
    )

    GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
    GEMINI_MODEL = (os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash").strip()
    WHATSAPP_ACCESS_TOKEN = (os.environ.get("WHATSAPP_ACCESS_TOKEN") or "").strip()
    WHATSAPP_PHONE_NUMBER_ID = (os.environ.get("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    WHATSAPP_RECIPIENT = (os.environ.get("WHATSAPP_RECIPIENT") or "").strip()
    WHATSAPP_API_VERSION = (os.environ.get("WHATSAPP_API_VERSION") or "v20.0").strip()
    if MEDHAR_MODE == "local":
        # Локальний режим — без спільних хмарних ключів з пакету команди.
        # Власний GEMINI у базовому .env (розробка) лишаємо, якщо MEDHAR_DESKTOP вимкнено.
        if _desktop_mode():
            GEMINI_API_KEY = ""
            WHATSAPP_ACCESS_TOKEN = ""
            WHATSAPP_PHONE_NUMBER_ID = ""
            WHATSAPP_RECIPIENT = ""
            DROPBOX_ACCESS_TOKEN = ""
            DROPBOX_APP_KEY = ""
            DROPBOX_APP_SECRET = ""
            DROPBOX_REFRESH_TOKEN = ""
        else:
            DROPBOX_ACCESS_TOKEN = (os.environ.get("DROPBOX_ACCESS_TOKEN") or "").strip()
            DROPBOX_APP_KEY = (os.environ.get("DROPBOX_APP_KEY") or "").strip()
            DROPBOX_APP_SECRET = (os.environ.get("DROPBOX_APP_SECRET") or "").strip()
            DROPBOX_REFRESH_TOKEN = (os.environ.get("DROPBOX_REFRESH_TOKEN") or "").strip()
    else:
        DROPBOX_ACCESS_TOKEN = (os.environ.get("DROPBOX_ACCESS_TOKEN") or "").strip()
        DROPBOX_APP_KEY = (os.environ.get("DROPBOX_APP_KEY") or "").strip()
        DROPBOX_APP_SECRET = (os.environ.get("DROPBOX_APP_SECRET") or "").strip()
        DROPBOX_REFRESH_TOKEN = (os.environ.get("DROPBOX_REFRESH_TOKEN") or "").strip()

    _raw_dropbox_root = os.environ.get("DROPBOX_ROOT_FOLDER")
    DROPBOX_ROOT_FOLDER = _normalize_dropbox_root(
        "" if _raw_dropbox_root is None else _raw_dropbox_root
    )

    SESSION_MODE = read_session(session_path(USER_DATA_ROOT))
    HAS_TEAM_VAULT = vault_exists(RESOURCE_ROOT)
    NEEDS_TEAM_UNLOCK = (
        SESSION_MODE is None and _desktop_mode() and HAS_TEAM_VAULT
    )


# Шляхи. Excel можна винести у синхронізовану папку Dropbox, решта
# персональних даних (SQLite, медіа, JSON) лишається локальною.
BASE_DIR = RESOURCE_ROOT  # сумісність: шаблони/static поруч із бандлом
DATA_DIR = os.path.join(USER_DATA_ROOT, "data")
EXCEL_DATA_DIR = _compute_excel_data_dir()
TEMPLATES_DIR = os.path.join(RESOURCE_ROOT, "templates")
STATIC_DIR = os.path.join(RESOURCE_ROOT, "static")
TEMP_DIR = os.path.join(USER_DATA_ROOT, "temp")

# Спільний журнал Turso вмикається тільки коли задані обидва секрети.
MEDHAR_MODE = (os.environ.get("MEDHAR_MODE") or "local").strip().lower()
TURSO_DATABASE_URL = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
TURSO_AUTH_TOKEN = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
SESSION_MODE = read_session(session_path(USER_DATA_ROOT))
HAS_TEAM_VAULT = vault_exists(RESOURCE_ROOT)
NEEDS_TEAM_UNLOCK = False

if SESSION_MODE is None and _desktop_mode() and HAS_TEAM_VAULT:
    # Уже заповнений AppData .env (старі встановлення) — вважаємо режимом команди.
    if TURSO_DATABASE_URL and TURSO_AUTH_TOKEN:
        write_session(session_path(USER_DATA_ROOT), "team")
        SESSION_MODE = "team"
        MEDHAR_MODE = "team"
        os.environ["MEDHAR_MODE"] = "team"
    else:
        NEEDS_TEAM_UNLOCK = True
        TURSO_DATABASE_URL = ""
        TURSO_AUTH_TOKEN = ""
        MEDHAR_MODE = "local"
elif SESSION_MODE == "local":
    TURSO_DATABASE_URL = ""
    TURSO_AUTH_TOKEN = ""
    MEDHAR_MODE = "local"
elif SESSION_MODE == "team":
    MEDHAR_MODE = "team"

if bool(TURSO_DATABASE_URL) != bool(TURSO_AUTH_TOKEN):
    raise RuntimeError(
        "Для Turso потрібно задати разом TURSO_DATABASE_URL і TURSO_AUTH_TOKEN"
    )
USE_TURSO = bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)
USE_TURSO_SYNC = USE_TURSO and MEDHAR_MODE == "team"

# Excel лікувань: у EXCEL_DATA_DIR шукаються treatments_YYYY.xlsx (будь-який рік), порядок злиття — за роком зростання;
# при дублікатах пріоритет у більшого року. Опційний архів treatments_final.xlsx додається першим.
TREATMENTS_YEAR_FILE_RE = re.compile(r'^treatments_(\d{4})\.xlsx$', re.IGNORECASE)
TREATMENTS_FINAL_FILE = os.path.join(EXCEL_DATA_DIR, 'treatments_final.xlsx')
TREATMENTS_UPLOAD_MAX_BYTES = 80 * 1024 * 1024  # 80 МБ
BASE_UPLOAD_MAX_BYTES = 80 * 1024 * 1024  # 80 МБ

# Шаблони документів.
# DOCX можна перевизначити без перевстановлення: покладіть файл із такою ж
# назвою в %LOCALAPPDATA%\Medhar\templates — він матиме пріоритет над бандлом.
USER_TEMPLATES_DIR = os.path.join(USER_DATA_ROOT, "templates")


def _doc_template(filename: str) -> str:
    override = os.path.join(USER_TEMPLATES_DIR, filename)
    if os.path.isfile(override):
        return override
    return os.path.join(TEMPLATES_DIR, filename)


MEDICAL_CHARACTERISTIC_TEMPLATE = _doc_template('medical_characteristic_template.docx')
SERVICE_CHARACTERISTIC_TEMPLATE = _doc_template('service_characteristic.docx')
VLK_REPORT_TEMPLATE = _doc_template('vlk_report.docx')
VACATION_REPORT_TEMPLATE = _doc_template('vacantion_report.docx')
VLK_FABULA_TEMPLATE = _doc_template('vlk_fabula.docx')
OUTPATIENT_JOURNAL_TEMPLATE = _doc_template('record_keeping_for_outpatients.docx')
OUTPATIENT_JOURNAL_DB = os.path.join(DATA_DIR, 'outpatient_journal.db')
# legacy (міграція, якщо файл ще є)
OUTPATIENT_JOURNAL_FILE = os.path.join(DATA_DIR, 'outpatient_journal.xlsx')
TREATMENT_MEDIA_DIR = os.path.join(DATA_DIR, 'treatment_media')
TREATMENT_MEDIA_MAX_BYTES = 10 * 1024 * 1024  # 10 МБ
REMINDER_WITHIN_DAYS = 3

# Оплати / відомість неоплачених стаціонарів
PAYMENTS_DIR = os.path.join(DATA_DIR, 'payments')
PAYMENTS_TEMPLATE = _doc_template('payments_template.docx')
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
# У desktop/frozen завжди без debug
DEBUG = False if _desktop_mode() else True

# Gemini (ключ лише з .env / team vault, не комітити)
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = (os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash").strip()
if MEDHAR_MODE == "local" and _desktop_mode() and SESSION_MODE != "team":
    GEMINI_API_KEY = ""

# WhatsApp Business API (ключ лише з .env / team vault, не комітити)
WHATSAPP_ACCESS_TOKEN = (os.environ.get("WHATSAPP_ACCESS_TOKEN") or "").strip()
WHATSAPP_PHONE_NUMBER_ID = (os.environ.get("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
WHATSAPP_RECIPIENT = (os.environ.get("WHATSAPP_RECIPIENT") or "").strip()
WHATSAPP_API_VERSION = (os.environ.get("WHATSAPP_API_VERSION") or "v20.0").strip()
if MEDHAR_MODE == "local" and _desktop_mode() and SESSION_MODE != "team":
    WHATSAPP_ACCESS_TOKEN = ""
    WHATSAPP_PHONE_NUMBER_ID = ""
    WHATSAPP_RECIPIENT = ""

# Dropbox API path (НЕ Windows-шлях):
# App folder (Програми/patientss) → DROPBOX_ROOT_FOLDER порожній → /patients/{ПІБ}/...
# Full Dropbox → DROPBOX_ROOT_FOLDER=м-с → /м-с/patients/{ПІБ}/...
# Токен sl.u.… короткоживучий (~4 год). Для постійної роботи задайте
# DROPBOX_APP_KEY + DROPBOX_APP_SECRET + DROPBOX_REFRESH_TOKEN.
DROPBOX_ACCESS_TOKEN = (os.environ.get("DROPBOX_ACCESS_TOKEN") or "").strip()
DROPBOX_APP_KEY = (os.environ.get("DROPBOX_APP_KEY") or "").strip()
DROPBOX_APP_SECRET = (os.environ.get("DROPBOX_APP_SECRET") or "").strip()
DROPBOX_REFRESH_TOKEN = (os.environ.get("DROPBOX_REFRESH_TOKEN") or "").strip()
if MEDHAR_MODE == "local" and _desktop_mode() and SESSION_MODE != "team":
    DROPBOX_ACCESS_TOKEN = ""
    DROPBOX_APP_KEY = ""
    DROPBOX_APP_SECRET = ""
    DROPBOX_REFRESH_TOKEN = ""

# None / відсутній ключ і порожній рядок → App folder root (Програми/patientss)
_raw_dropbox_root = os.environ.get("DROPBOX_ROOT_FOLDER")
DROPBOX_ROOT_FOLDER = _normalize_dropbox_root(
    "" if _raw_dropbox_root is None else _raw_dropbox_root
)
