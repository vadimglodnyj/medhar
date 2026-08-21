"""
Картотека пацієнтів: patients → treatments → visits + restrictions + media.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Optional

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename
from config import REMINDER_WITHIN_DAYS, TREATMENT_MEDIA_MAX_BYTES
import config as _cfg
from utils import db_cache
from utils.db_backend import connect as db_connect
from utils.db_backend import retry_if_locked
from utils.sync_schema import (
    _add_col,
    ensure_sync_schema,
    enqueue_outbox,
    new_sync_id,
    not_deleted_sql,
    notify_write as _sync_notify_write,
    queue_media_upload,
    sync_enabled,
    touch_row,
    utc_now,
)

logger = logging.getLogger(__name__)

RESTRICTION_KINDS = {
    "referral": "Направлення на консультацію",
    "consultation": "Консультація",
    "abroad": "Лікування за кордоном",
    "inpatient": "Стаціонарне",
    "rehab": "Реабілітація",
    "outpatient": "Амбулаторне",
    "day_hospital": "Денний стаціонар",
    "vacation": "Відпустка",
    "vlk": "ВЛК",
    "phys_exempt": "Звільнення від фіз. навантаження",
}

TREATMENT_CAUSE_COMBAT = "combat"
TREATMENT_CAUSE_SOMATIC = "somatic"
TREATMENT_CAUSE_OPTIONS = (
    (TREATMENT_CAUSE_COMBAT, "Бойове"),
    (TREATMENT_CAUSE_SOMATIC, "Соматичне"),
)

# Коди з base.xlsx «Підрозділ (скорочено)» → вигляд у WhatsApp після «2 БОП,»
UNIT_SHORT_DISPLAY = {
    "1РОП": "1 РОП",
    "2РОП": "2 РОП",
    "3РОП": "3 РОП",
    "МП": "МП",
    "УБ": "УБ",
    "ШБ": "ШБ",
    "РВП": "РВП",
    "РВСП": "РВСП",
    "ВЗ": "ВЗ",
    "ВБКСП": "ВБКСП",
    "ІСВ": "ІСВ",
    "ВМТЗ": "ВМТЗ",
    "ВТООтаВТ": "ВТООтаВТ",
    "МБ (120мм)": "МБ (120мм)",
    "МБ (60мм, 82мм)": "МБ (60мм, 82мм)",
}

# Fallback: канонічна назва фільтра журналу → короткий код
CANONICAL_UNIT_TO_SHORT = {
    "Штаб": "ШБ",
    "1-ша рота оперативного призначення (на бронетранспортерах)": "1РОП",
    "2-га рота оперативного призначення (на бронетранспортерах)": "2РОП",
    "3-тя рота оперативного призначення (на бронетранспортерах)": "3РОП",
    "Мінометна батарея (120 мм міномети)": "МБ (120мм)",
    "Мінометна батарея (60 мм (82 мм) міномети)": "МБ (60мм, 82мм)",
    "Рота вогневої підтримки": "РВП",
    "Взвод зв'язку": "ВЗ",
    "Розвідувальний взвод спеціального призначення": "РВСП",
    "Інженерно-саперне відділення": "ІСВ",
    "Взвод безпілотних комплексів спеціального призначення": "ВБКСП",
    "Взвод матеріально-технічного забезпечення": "ВМТЗ",
    "Взвод технічного обслуговування озброєння та військової техніки": "ВТООтаВТ",
    "Медичний пункт": "МП",
}

WA_EVENT_OPEN = "open"
WA_EVENT_EXTEND = "extend"
WA_EVENT_DISCHARGE = "discharge"

# Підкатегорія дії для WhatsApp залежно від типу лікування
CARE_WA_EVENTS = {
    "outpatient": [
        (WA_EVENT_OPEN, "Відкрив"),
        (WA_EVENT_EXTEND, "Продовжив"),
        (WA_EVENT_DISCHARGE, "Завершив"),
    ],
    "day_hospital": [
        (WA_EVENT_OPEN, "Відкрив"),
        (WA_EVENT_EXTEND, "Продовжив"),
        (WA_EVENT_DISCHARGE, "Завершив"),
    ],
    "inpatient": [
        (WA_EVENT_OPEN, "Госпіталізований"),
        (WA_EVENT_EXTEND, "Переведений"),
        (WA_EVENT_DISCHARGE, "Виписаний"),
    ],
}

WA_EVENT_LABELS = {
    WA_EVENT_OPEN: "Відкрив",
    WA_EVENT_EXTEND: "Продовжив",
    WA_EVENT_DISCHARGE: "Завершив",
}

# Після стількох повних днів у стаціонарі (від початку або останнього дзвінка) — дзвінок медика
INPATIENT_MEDIC_CALL_DAYS = 10

_PATIENT_EXTRA_COLS = (
    ("rank", "TEXT NOT NULL DEFAULT ''"),
    ("position", "TEXT NOT NULL DEFAULT ''"),
    ("unit_short", "TEXT NOT NULL DEFAULT ''"),
    ("birth_date", "TEXT NOT NULL DEFAULT ''"),
    ("phone", "TEXT NOT NULL DEFAULT ''"),
    ("ipn", "TEXT NOT NULL DEFAULT ''"),
    ("service_category", "TEXT NOT NULL DEFAULT ''"),
    ("enlistment_date", "TEXT NOT NULL DEFAULT ''"),
    ("komisariat", "TEXT NOT NULL DEFAULT ''"),
)

SERVICE_CATEGORY_MOBILIZED = "мобілізований"
SERVICE_CATEGORY_CONTRACT = "контрактник"
SERVICE_CATEGORY_OPTIONS = (
    (SERVICE_CATEGORY_MOBILIZED, "Мобілізований"),
    (SERVICE_CATEGORY_CONTRACT, "Контрактник"),
)

VLK_REFERRAL_BASIS_OPTIONS = (
    ("dbr", "ДБР"),
    ("doctor", "Рекомендація лікаря"),
)

MEDIA_KIND_INJURY_CERT = "injury_cert"

_ALLOWED_MEDIA_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}


# Схема створюється один раз на процес (див. ensure_card_schema).
_schema_ready = False


def _commit(conn) -> None:
    """Фіксує зміни й скидає кеш списків, щоб вони не показували старі дані."""
    conn.commit()
    db_cache.invalidate_all()


def _normalize_spaces(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_treatment_cause(value: str) -> str:
    raw = _normalize_spaces(value).casefold()
    if raw in (TREATMENT_CAUSE_COMBAT, "бойове", "бойова", "бойовий"):
        return TREATMENT_CAUSE_COMBAT
    if raw in (TREATMENT_CAUSE_SOMATIC, "соматичне", "соматична", "соматичний"):
        return TREATMENT_CAUSE_SOMATIC
    return ""


def treatment_cause_label(value: str) -> str:
    key = normalize_treatment_cause(value)
    for code, label in TREATMENT_CAUSE_OPTIONS:
        if code == key:
            return label
    return ""


def _connect() -> sqlite3.Connection:
    os.makedirs(_cfg.DATA_DIR, exist_ok=True)
    conn = db_connect()
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def parse_ui_date(value: str) -> Optional[datetime]:
    raw = _normalize_spaces(value)
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 8:
        raw = f"{digits[0:2]}.{digits[2:4]}.{digits[4:8]}"
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def format_ui_date(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y")


def today_ui() -> str:
    return format_ui_date(datetime.now())


def add_days_inclusive(start: datetime, days: int) -> datetime:
    """N календарних днів включно: 1 день = той самий день, 7 днів = start+6."""
    n = max(1, int(days))
    return start + timedelta(days=n - 1)


def calendar_days_inclusive(start: datetime, end: datetime) -> int:
    if end < start:
        start, end = end, start
    return (end.date() - start.date()).days + 1


def ensure_card_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """
    Створює таблиці картотеки та колонку treatment_id у зверненнях.

    Схема не змінюється під час роботи, тому перевіряємо її один раз на процес:
    для Turso це десятки мережевих запитів на кожен виклик.
    """
    global _schema_ready
    own = conn is None
    if own and _schema_ready:
        # Після оновлення програми колонки могли додатись — перевіримо ключові.
        try:
            probe = _connect()
            mcols = {
                row[1]
                for row in probe.execute("PRAGMA table_info(treatment_media)").fetchall()
            }
            probe.close()
            if "dropbox_path" not in mcols:
                _schema_ready = False
            else:
                return
        except Exception:
            return
    if own:
        conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pib TEXT NOT NULL UNIQUE,
                rank_unit TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS injury_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                injury_date TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                skip_cert TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                sync_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                deleted_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_injury_cases_patient ON injury_cases(patient_id);
            CREATE TABLE IF NOT EXISTS treatments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                started_on TEXT NOT NULL DEFAULT '',
                closed_on TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS treatment_restrictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                treatment_id INTEGER NOT NULL,
                visit_id INTEGER,
                kind TEXT NOT NULL DEFAULT 'phys_exempt',
                start_on TEXT NOT NULL DEFAULT '',
                end_on TEXT NOT NULL DEFAULT '',
                days INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (treatment_id) REFERENCES treatments(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS treatment_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                treatment_id INTEGER NOT NULL,
                visit_id INTEGER,
                filename TEXT NOT NULL,
                original_name TEXT NOT NULL DEFAULT '',
                mime TEXT NOT NULL DEFAULT '',
                uploaded_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (treatment_id) REFERENCES treatments(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_treatments_patient ON treatments(patient_id);
            CREATE INDEX IF NOT EXISTS idx_restrictions_treatment ON treatment_restrictions(treatment_id);
            CREATE INDEX IF NOT EXISTS idx_restrictions_end ON treatment_restrictions(end_on);
            CREATE INDEX IF NOT EXISTS idx_media_treatment ON treatment_media(treatment_id);
            """
        )
        cols = {row[1] for row in conn.execute("PRAGMA table_info(outpatient_entries)").fetchall()}
        if cols and "treatment_id" not in cols:
            conn.execute(
                "ALTER TABLE outpatient_entries ADD COLUMN treatment_id INTEGER"
            )
        cols = {row[1] for row in conn.execute("PRAGMA table_info(outpatient_entries)").fetchall()}
        for col, decl in (
            ("care_type", "TEXT NOT NULL DEFAULT ''"),
            ("leave_start", "TEXT NOT NULL DEFAULT ''"),
            ("leave_end", "TEXT NOT NULL DEFAULT ''"),
            ("leave_days", "TEXT NOT NULL DEFAULT ''"),
            ("wa_event", "TEXT NOT NULL DEFAULT ''"),
            ("from_lpz", "TEXT NOT NULL DEFAULT ''"),
            ("vlk_passed", "TEXT NOT NULL DEFAULT ''"),
            ("medic_call_date", "TEXT NOT NULL DEFAULT ''"),
        ):
            if cols and col not in cols:
                conn.execute(f"ALTER TABLE outpatient_entries ADD COLUMN {col} {decl}")
                cols.add(col)

        pcols = {row[1] for row in conn.execute("PRAGMA table_info(patients)").fetchall()}
        for col, decl in _PATIENT_EXTRA_COLS:
            if pcols and col not in pcols:
                conn.execute(f"ALTER TABLE patients ADD COLUMN {col} {decl}")
                pcols.add(col)
        tcols = {row[1] for row in conn.execute("PRAGMA table_info(treatments)").fetchall()}
        if tcols and "needs_injury_cert" not in tcols:
            conn.execute(
                "ALTER TABLE treatments ADD COLUMN needs_injury_cert TEXT NOT NULL DEFAULT ''"
            )
            tcols.add("needs_injury_cert")
        if tcols and "injury_case_id" not in tcols:
            conn.execute(
                "ALTER TABLE treatments ADD COLUMN injury_case_id INTEGER NOT NULL DEFAULT 0"
            )
            tcols.add("injury_case_id")
        if tcols and "cause" not in tcols:
            conn.execute(
                "ALTER TABLE treatments ADD COLUMN cause TEXT NOT NULL DEFAULT ''"
            )
            tcols.add("cause")
        mcols = {row[1] for row in conn.execute("PRAGMA table_info(treatment_media)").fetchall()}
        if mcols and "kind" not in mcols:
            conn.execute(
                "ALTER TABLE treatment_media ADD COLUMN kind TEXT NOT NULL DEFAULT ''"
            )
            mcols.add("kind")
        if mcols and "injury_case_id" not in mcols:
            conn.execute(
                "ALTER TABLE treatment_media ADD COLUMN injury_case_id INTEGER NOT NULL DEFAULT 0"
            )
            mcols.add("injury_case_id")
        if mcols and "dropbox_path" not in mcols:
            conn.execute(
                "ALTER TABLE treatment_media ADD COLUMN dropbox_path TEXT NOT NULL DEFAULT ''"
            )
            mcols.add("dropbox_path")
        ensure_sync_schema(conn)
        if isinstance(conn, sqlite3.Connection):
            _backfill_injury_cases(conn)
            _backfill_treatment_cause(conn)
            _schema_ready = True
        conn.commit()
    finally:
        if own:
            conn.close()


def find_patient_id_by_pib(pib: str) -> Optional[int]:
    """Знаходить id пацієнта за ПІБ (без створення)."""
    ensure_card_schema()
    pib_n = _normalize_spaces(pib)
    if not pib_n:
        return None
    conn = _connect()
    try:
        rows = conn.execute("SELECT id, pib FROM patients").fetchall()
        for row in rows:
            if _normalize_spaces(row["pib"]).casefold() == pib_n.casefold():
                return int(row["id"])
        return None
    finally:
        conn.close()


def patient_id_map_by_pib() -> dict:
    """Словник casefold(ПІБ) → patient_id для швидкого лінкування в журналі."""

    def load() -> dict:
        ensure_card_schema()
        conn = _connect()
        try:
            out = {}
            for row in conn.execute("SELECT id, pib FROM patients").fetchall():
                key = _normalize_spaces(row["pib"]).casefold()
                if key:
                    out[key] = int(row["id"])
            return out
        finally:
            conn.close()

    return dict(db_cache.get_or_load(db_cache.PIB_MAP, load))


def get_or_create_patient(pib: str, rank_unit: str = "") -> int:
    ensure_card_schema()
    pib_n = _normalize_spaces(pib)
    if not pib_n:
        raise ValueError("ПІБ порожній")

    def _write() -> int:
        conn = _connect()
        try:
            rows = conn.execute("SELECT id, pib, rank_unit FROM patients").fetchall()
            for row in rows:
                if _normalize_spaces(row["pib"]).casefold() == pib_n.casefold():
                    pid = int(row["id"])
                    ru = _normalize_spaces(rank_unit)
                    if ru and not _normalize_spaces(row["rank_unit"]):
                        conn.execute(
                            "UPDATE patients SET rank_unit = ? WHERE id = ?",
                            (ru, pid),
                        )
                        _sync_notify_write(conn, "patients", pid)
                        _commit(conn)
                    return pid
            sid = new_sync_id()
            ts = utc_now()
            cur = conn.execute(
                "INSERT INTO patients (pib, rank_unit, sync_id, updated_at) VALUES (?, ?, ?, ?)",
                (pib_n, _normalize_spaces(rank_unit), sid, ts),
            )
            pid = int(cur.lastrowid)
            enqueue_outbox(conn, "patients", sid, "upsert")
            _commit(conn)
            return pid
        finally:
            conn.close()

    return int(retry_if_locked(_write))


def normalize_service_category(value: str) -> str:
    """Нормалізує категорію служби до мобілізований / контрактник."""
    raw = _normalize_spaces(value).casefold()
    if not raw:
        return ""
    if "контр" in raw:
        return SERVICE_CATEGORY_CONTRACT
    if "моб" in raw:
        return SERVICE_CATEGORY_MOBILIZED
    return ""


def format_rank_and_category(rank: str, category: str) -> str:
    rank_n = _normalize_spaces(rank)
    cat_n = normalize_service_category(category) or _normalize_spaces(category)
    if rank_n and cat_n:
        return f"{rank_n}, {cat_n}"
    return rank_n or cat_n


def update_patient(patient_id: int, **fields) -> bool:
    """Оновлює лише передані поля картки (ключі з PATIENT editable set)."""
    ensure_card_schema()
    allowed = {
        "rank", "position", "unit_short", "birth_date", "phone", "ipn", "rank_unit",
        "service_category", "enlistment_date", "komisariat",
    }
    patch = {
        k: _normalize_spaces(v)
        for k, v in fields.items()
        if k in allowed and v is not None
    }
    if "service_category" in patch:
        patch["service_category"] = normalize_service_category(patch["service_category"]) or patch["service_category"]
    if not patch:
        return False
    patient = get_patient(patient_id)
    if not patient:
        return False
    merged = {k: patient.get(k) or "" for k in allowed}
    merged.update(patch)
    if not merged["rank_unit"] and (merged["rank"] or merged["position"]):
        merged["rank_unit"] = ", ".join(
            p for p in (merged["rank"], merged["position"]) if p
        )
    conn = _connect()
    try:
        cur = conn.execute(
            """
            UPDATE patients SET
                rank = ?, position = ?, unit_short = ?, birth_date = ?,
                phone = ?, ipn = ?, rank_unit = ?,
                service_category = ?, enlistment_date = ?, komisariat = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                merged["rank"],
                merged["position"],
                merged["unit_short"],
                merged["birth_date"],
                merged["phone"],
                merged["ipn"],
                merged["rank_unit"],
                merged["service_category"],
                merged["enlistment_date"],
                merged["komisariat"],
                utc_now(),
                int(patient_id),
            ),
        )
        if cur.rowcount > 0:
            _sync_notify_write(conn, "patients", int(patient_id))
        _commit(conn)
        return cur.rowcount > 0
    finally:
        conn.close()


def apply_base_fields_to_patient(
    patient_id: int,
    base_person: dict,
    *,
    overwrite: bool = False,
) -> bool:
    """Застосовує дані з base.xlsx (словник lookup) до картки."""
    if not base_person:
        return False
    patient = get_patient(patient_id)
    if not patient:
        return False
    rank = _normalize_spaces(base_person.get("zvanie") or "")
    position = _normalize_spaces(base_person.get("zaimana_posada") or "")
    unit_short = _normalize_spaces(base_person.get("unit_short") or "")
    birth_date = _normalize_spaces(base_person.get("birth_date") or "")
    service_category = normalize_service_category(
        base_person.get("service_category") or base_person.get("sluzhba_type") or ""
    )
    patch = {}
    for field, val in (
        ("rank", rank),
        ("position", position),
        ("unit_short", unit_short),
        ("birth_date", birth_date),
        ("service_category", service_category),
    ):
        if not val:
            continue
        if overwrite or not _normalize_spaces(patient.get(field) or ""):
            patch[field] = val
    if patch.get("rank") or patch.get("position"):
        r = patch.get("rank", patient.get("rank") or "")
        p = patch.get("position", patient.get("position") or "")
        if overwrite or not _normalize_spaces(patient.get("rank_unit") or ""):
            patch["rank_unit"] = ", ".join(x for x in (r, p) if x)
    if not patch:
        return False
    return update_patient(patient_id, **patch)


def apply_treatments_fields_to_patient(
    patient_id: int,
    treatments_person: dict,
    *,
    overwrite: bool = False,
) -> bool:
    """Підтягує телефон / категорію / дату народження з Бази лікувань."""
    if not treatments_person:
        return False
    patient = get_patient(patient_id)
    if not patient:
        return False
    phone = _normalize_spaces(treatments_person.get("phone") or "")
    birth_date = _normalize_spaces(treatments_person.get("birth_date") or "")
    service_category = normalize_service_category(
        treatments_person.get("service_category") or treatments_person.get("kategoriia") or ""
    )
    patch = {}
    for field, val in (
        ("phone", phone),
        ("birth_date", birth_date),
        ("service_category", service_category),
    ):
        if not val:
            continue
        if overwrite or not _normalize_spaces(patient.get(field) or ""):
            patch[field] = val
    if not patch:
        return False
    return update_patient(patient_id, **patch)


def latest_diagnosis_for_patient(patient_id: int) -> str:
    """Останній непорожній діагноз зі звернень пацієнта."""
    ensure_card_schema()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT oe.diagnosis
            FROM outpatient_entries oe
            JOIN treatments t ON t.id = oe.treatment_id
            WHERE t.patient_id = ?
              AND TRIM(COALESCE(oe.diagnosis, '')) != ''
            ORDER BY oe.id DESC
            LIMIT 1
            """,
            (int(patient_id),),
        ).fetchone()
        if row and row["diagnosis"]:
            return _normalize_spaces(row["diagnosis"])
        row = conn.execute(
            """
            SELECT title FROM treatments
            WHERE patient_id = ? AND TRIM(COALESCE(title, '')) != ''
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(patient_id),),
        ).fetchone()
        return _normalize_spaces(row["title"] if row else "")
    finally:
        conn.close()


def count_patients() -> int:
    ensure_card_schema()
    conn = _connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM patients").fetchone()
        return int(row["n"] if row else 0)
    finally:
        conn.close()


def format_unit_short_display(unit_short: str) -> str:
    raw = _normalize_spaces(unit_short)
    if not raw:
        return ""
    if raw in UNIT_SHORT_DISPLAY:
        return UNIT_SHORT_DISPLAY[raw]
    # вже з пробілом «1 РОП»
    compact = raw.replace(" ", "")
    for code, label in UNIT_SHORT_DISPLAY.items():
        if code.replace(" ", "") == compact:
            return label
    return raw


def format_wa_unit_line(unit_short: str = "", canonical_unit: str = "") -> str:
    short = _normalize_spaces(unit_short)
    if not short and canonical_unit:
        short = CANONICAL_UNIT_TO_SHORT.get(canonical_unit, "")
    display = format_unit_short_display(short)
    if display:
        return f"2 БОП, {display}"
    return "2 БОП"


def _wa_token_to_instrumental(token: str) -> str:
    """Родовий → орудний для типових назв спеціалізацій з likar_specializations.json."""
    t = token.strip()
    if not t:
        return t
    low = t.casefold()
    # уже орудний
    if low.endswith(("ом", "ем", "им", "ім", "ою", "ею")):
        return t
    # прикметники ч.р.: судинного → судинним, сімейного → сімейним
    if low.endswith("ього"):
        return t[:-4] + "ім"
    if low.endswith("ого"):
        return t[:-3] + "им"
    # іменники: лікаря → лікарем, кардіолога → кардіологом
    if low.endswith("я"):
        return t[:-1] + "ем"
    if low.endswith("а"):
        return t[:-1] + "ом"
    return t


def _wa_token_to_nominative(token: str) -> str:
    """Родовий → називний: лікаря → лікар, терапевта → терапевт, судинного → судинний."""
    t = token.strip()
    if not t:
        return t
    low = t.casefold()
    # уже називний / не чіпаємо жіночі / множину в складених назвах
    if low.endswith(("ої", "єї", "их", "іх", "ів", "їв", "ам", "ям")):
        return t
    if low in {
        "практики", "фізкультури", "медицини", "діагностики", "станів",
        "з", "та", "і", "й",
    }:
        return t
    if low.endswith("ього"):
        return t[:-4] + "ій"
    if low.endswith("ого"):
        return t[:-3] + "ий"
    if low.endswith("я"):
        return t[:-1]
    if low.endswith("а"):
        return t[:-1]
    return t


def _map_specialist_tokens(specialist: str, token_fn) -> str:
    text = _normalize_spaces(specialist)
    if not text:
        return ""
    parts: list[str] = []
    for chunk in text.split(" — "):
        words = []
        for word in chunk.split(" "):
            if "-" in word:
                words.append("-".join(token_fn(p) for p in word.split("-")))
            else:
                words.append(token_fn(word))
        parts.append(" ".join(words))
    return " — ".join(parts)


def specialist_to_instrumental(specialist: str) -> str:
    """
    «лікаря-кардіолога» → «лікарем-кардіологом» для фрази «консультований …».
    Родовий після «до …» не чіпаємо — ця функція лише для орудного.
    """
    return _map_specialist_tokens(specialist, _wa_token_to_instrumental)


def specialist_to_nominative(specialist: str) -> str:
    """«лікаря-терапевта» → «лікар-терапевт» для відображення на сторінці."""
    return _map_specialist_tokens(specialist, _wa_token_to_nominative)


def wa_action_phrase(
    event: str,
    care_type: str = "",
    lpz: str = "",
    date: str = "",
    leave_start: str = "",
    leave_end: str = "",
    specialist: str = "",
    from_lpz: str = "",
) -> str:
    """Фраза дії для WhatsApp (з датою / періодом)."""
    lpz_n = _normalize_spaces(lpz)
    from_lpz_n = _normalize_spaces(from_lpz)
    in_lpz = f" в {lpz_n}" if lpz_n else ""
    event = (event or WA_EVENT_OPEN).strip()
    kind = (care_type or "").strip()
    specialist_n = _normalize_spaces(specialist)
    # «до лікаря-кардіолога» (родовий) vs «консультований лікарем-кардіологом» (орудний)
    by_spec = f" {specialist_to_instrumental(specialist_n)}" if specialist_n else ""
    to_spec = f" до {specialist_n}" if specialist_n else ""

    start = _normalize_spaces(leave_start) or _normalize_spaces(date)
    end = _normalize_spaces(leave_end)
    # Консультація — дата звернення. Направлення — дата направлення (leave_start).
    if kind == "consultation":
        start = _normalize_spaces(date) or start
        end = ""
    elif kind == "referral":
        start = _normalize_spaces(leave_start) or _normalize_spaces(date) or start
        end = ""
    if start and end and start != end:
        when = f" з {start} по {end}"
    elif start:
        when = f" {start}"
    else:
        when = ""

    if event == WA_EVENT_DISCHARGE:
        if kind == "inpatient":
            # «12.08.2026 виписаний з ДУ "ГМКЦ МВС України" м.Київ»
            discharge_date = end or _normalize_spaces(date) or start
            if discharge_date and lpz_n:
                return f"{discharge_date} виписаний з {lpz_n}"
            if lpz_n:
                return f"виписаний з {lpz_n}"
            if discharge_date:
                return f"{discharge_date} виписаний"
            return "виписаний"
        # період лікарняного / обмеження; інакше — дата закриття
        if not (start and end and start != end) and start:
            when = f" {start}"
        if kind in ("", "outpatient", "day_hospital"):
            return f"закрив лікарняний лист{when}" if when else "закрив лікарняний лист"
        if kind == "phys_exempt":
            return f"завершив звільнення від фіз. навантаження{when}" if when else "завершив звільнення від фіз. навантаження"
        if kind == "vacation":
            return f"завершив відпустку{when}" if when else "завершив відпустку"
        if kind == "vlk":
            # «13.08.2026 завершив проходження ВЛК в ДУ "ТМО…"»
            # дата завершення — leave_end / date звернення / closed_on, не дата початку
            done_date = end or _normalize_spaces(date)
            if done_date and lpz_n:
                return f"{done_date} завершив проходження ВЛК в {lpz_n}"
            if done_date:
                return f"{done_date} завершив проходження ВЛК"
            if lpz_n:
                return f"завершив проходження ВЛК в {lpz_n}"
            return "завершив проходження ВЛК"
        if kind == "rehab":
            return f"завершив реабілітацію{when}{in_lpz}" if (when or lpz_n) else "завершив реабілітацію"
        if kind == "abroad":
            return f"завершив лікування за кордоном{when}" if when else "завершив лікування за кордоном"
        if kind in ("consultation", "referral"):
            return f"завершив лікування{when}" if when else "завершив лікування"
        return f"закрив лікарняний лист{when}" if when else "закрив лікарняний лист"

    if event == WA_EVENT_EXTEND:
        if kind == "referral":
            return f"повторно направлений на консультацію{to_spec}{when}{in_lpz}"
        if kind == "consultation":
            return f"повторно консультований{by_spec}{when}{in_lpz}"
        if kind == "inpatient":
            if from_lpz_n and lpz_n and from_lpz_n.casefold() != lpz_n.casefold():
                return f"переведений{when} з {from_lpz_n} до {lpz_n}"
            if lpz_n:
                return f"переведений{when} до {lpz_n}"
            return f"переведений{when}".strip()
        if kind == "phys_exempt":
            return f"продовжив звільнення від фіз. навантаження{when}{in_lpz}"
        if kind == "vacation":
            return f"продовжив відпустку{when}{in_lpz}"
        if kind == "vlk":
            return f"продовжив ВЛК{when}{in_lpz}"
        return f"продовжив лікарняний лист{when}{in_lpz}"

    # open — лише цивільні ЛПЗ / спеціалісти (без «лікар МП батальйону»)
    if kind == "referral":
        return f"направлений на консультацію{to_spec}{when}{in_lpz}"
    if kind == "consultation":
        return f"консультований{by_spec}{when}{in_lpz}"
    if kind == "inpatient":
        return f"госпіталізований{when}{in_lpz}"
    if kind == "abroad":
        return f"направлений на лікування за кордон{when}{in_lpz}"
    if kind == "rehab":
        return f"направлений на реабілітацію{when}{in_lpz}"
    if kind == "day_hospital":
        return f"направлений у денний стаціонар{when}{in_lpz}"
    if kind == "vacation":
        return f"відпустка{when}{in_lpz}"
    if kind == "vlk":
        # «розпочав проходження ВЛК 13.08.2026 в ДУ "ТМО…"»
        start_date = start or _normalize_spaces(date)
        if start_date and lpz_n:
            return f"розпочав проходження ВЛК {start_date} в {lpz_n}"
        if start_date:
            return f"розпочав проходження ВЛК {start_date}"
        if lpz_n:
            return f"розпочав проходження ВЛК в {lpz_n}"
        return "розпочав проходження ВЛК"
    if kind == "phys_exempt":
        return f"звільнений від фіз. навантаження{when}{in_lpz}"
    if not kind:
        # Локальний огляд МП у WhatsApp не описуємо
        return ""
    # outpatient / default
    return f"відкрив лікарняний лист{when}{in_lpz}"


def _wa_strip_label_prefix(value: str, prefixes: tuple[str, ...]) -> str:
    text = _normalize_spaces(value)
    low = text.casefold()
    for prefix in prefixes:
        if low.startswith(prefix.casefold()):
            return text[len(prefix):].lstrip(" :—-")
    return text


def parse_recommendation_items(value: str) -> list:
    """
    Розбиває довгий текст рекомендацій ЛПЗ на короткі пункти.
    """
    text = _wa_strip_label_prefix(
        value or "",
        (
            "Рекомендації:",
            "Рекомендації",
            "Рекомендовано:",
            "Рекомендовано",
            "рекомендовано",
        ),
    )
    if not text:
        return []

    marked = re.sub(r"(?i)\s*,?\s*(?=Аналіз\s*;)", "\n", text)
    marked = re.sub(r"(?i)\s+(?=Консультація\b)", "\n", marked)
    marked = re.sub(r"(?i)\s+(?=Обстеження\b)", "\n", marked)
    marked = re.sub(r"(?i)\s+(?=Направлений\b)", "\n", marked)

    parts: list = []
    for raw in marked.split("\n"):
        chunk = _normalize_spaces(raw).strip(" ,;")
        if not chunk:
            continue
        chunk = re.sub(r"(?i)^(Аналіз\s*;\s*)+", "Аналіз; ", chunk)
        chunk = re.sub(r"\s*\([^()]*(?:\([^()]*\)[^()]*)*\)", "", chunk)
        chunk = _normalize_spaces(chunk).strip(" ,;")
        chunk = re.sub(r"(?i)^Аналіз\s*;\s*", "", chunk).strip()
        if chunk:
            parts.append(chunk)
    return parts


def format_wa_recommendations(value: str) -> str:
    """Список пунктів для WhatsApp (з • якщо більше одного)."""
    parts = parse_recommendation_items(value)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "\n".join(f"• {p}" for p in parts)


def build_whatsapp_message(
    patient: dict,
    *,
    action: str,
    diagnosis: str = "",
    recommendations: str = "",
    canonical_unit: str = "",
) -> str:
    """
    Текст для групи WhatsApp з розміткою *жирний*:
    підрозділ / ПІБ / звання / дата / телефон / дія / діагноз / рекомендації.
    """
    unit_line = format_wa_unit_line(
        patient.get("unit_short") or "",
        canonical_unit=canonical_unit,
    )
    pib = _normalize_spaces(patient.get("pib") or "") or "—"
    rank = _normalize_spaces(patient.get("rank") or "")
    if not rank:
        # fallback: «солдат, стрілець» → «солдат»
        ru = _normalize_spaces(patient.get("rank_unit") or "")
        if ru:
            rank = ru.split(",")[0].strip()
    service_category = normalize_service_category(patient.get("service_category") or "")
    rank_line = format_rank_and_category(rank, service_category) or "—"
    birth = _normalize_spaces(patient.get("birth_date") or "") or "—"
    phone = _normalize_spaces(patient.get("phone") or "") or "—"
    action_line = _normalize_spaces(action)
    diag = _wa_strip_label_prefix(
        diagnosis or "—",
        ("Діагноз:", "Діагноз"),
    ) or "—"
    rec = format_wa_recommendations(recommendations or "")

    lines = [
        f"*{unit_line}*",
        "",
        f"*ПІБ:* {pib}",
        f"*Звання:* {rank_line}",
        f"*Дата народження:* {birth}",
        f"*Телефон:* {phone}",
    ]
    if action_line:
        lines.extend(["", f"*Дія:* {action_line}"])
    lines.extend(["", "*Діагноз:*", diag])
    if rec:
        lines.extend(["", "*Рекомендації:*", rec])
    return "\n".join(lines)


def create_treatment(
    patient_id: int,
    title: str,
    *,
    started_on: str = "",
    notes: str = "",
    cause: str = "",
) -> int:
    ensure_card_schema()
    title_n = _normalize_spaces(title) or "Лікування"
    start = started_on or today_ui()
    cause_n = normalize_treatment_cause(cause)
    sid = new_sync_id()
    ts = utc_now()

    def _write() -> int:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO treatments
                (patient_id, title, status, started_on, notes, cause, needs_injury_cert,
                 injury_case_id, sync_id, updated_at)
                VALUES (?, ?, 'active', ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    int(patient_id),
                    title_n,
                    start,
                    _normalize_spaces(notes),
                    cause_n,
                    "1" if cause_n == TREATMENT_CAUSE_COMBAT else "",
                    sid,
                    ts,
                ),
            )
            tid = int(cur.lastrowid)
            enqueue_outbox(conn, "treatments", sid, "upsert")
            if cause_n == TREATMENT_CAUSE_COMBAT:
                _attach_treatment_to_injury_case_conn(
                    conn,
                    tid,
                    patient_id=int(patient_id),
                    title=title_n,
                )
            _commit(conn)
            return tid
        finally:
            conn.close()

    return int(retry_if_locked(_write))


def resolve_treatment_for_visit(
    *,
    pib: str,
    rank_unit: str,
    treatment_id: str = "",
    new_treatment_title: str = "",
    visit_date: str = "",
    diagnosis: str = "",
    cause: str = "",
) -> Optional[int]:
    """
    Гібрид: існуючий treatment_id або нове лікування.
    Якщо нічого не вказано — None.
    """
    tid_raw = _normalize_spaces(treatment_id)
    new_title = _normalize_spaces(new_treatment_title)
    if tid_raw and tid_raw not in ("0", "new"):
        try:
            return int(tid_raw)
        except ValueError:
            pass
    if tid_raw == "new" or new_title:
        title = new_title or _normalize_spaces(diagnosis) or "Лікування"
        cause_n = normalize_treatment_cause(cause)
        if not cause_n:
            raise ValueError("Оберіть характер лікування: бойове або соматичне.")
        pid = get_or_create_patient(pib, rank_unit)
        return create_treatment(
            pid, title, started_on=visit_date or today_ui(), cause=cause_n
        )
    # якщо є діагноз і пацієнт — не створюємо автоматично без явного «нове»
    get_or_create_patient(pib, rank_unit)
    return None


def list_treatments_for_pib(pib: str, *, active_only: bool = True) -> list:
    ensure_card_schema()
    pib_n = _normalize_spaces(pib)
    if not pib_n:
        return []
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT t.*, p.pib AS patient_pib FROM treatments t
            JOIN patients p ON p.id = t.patient_id
            WHERE {not_deleted_sql("t")} AND {not_deleted_sql("p")}
            ORDER BY t.id DESC
            """
        ).fetchall()
        out = []
        for r in rows:
            if _normalize_spaces(r["patient_pib"]).casefold() != pib_n.casefold():
                continue
            if active_only and r["status"] != "active":
                continue
            out.append(dict(r))
        return out
    finally:
        conn.close()


def list_patients(query: str = "") -> list:
    def load() -> list:
        ensure_card_schema()
        conn = _connect()
        try:
            rows = conn.execute(
                f"""
                SELECT p.*,
                       (SELECT COUNT(*) FROM treatments t
                        WHERE t.patient_id = p.id AND {not_deleted_sql("t")}) AS treatments_count,
                       (SELECT COUNT(*) FROM treatments t
                        WHERE t.patient_id = p.id AND t.status = 'active'
                          AND {not_deleted_sql("t")}) AS active_count
                FROM patients p
                WHERE {not_deleted_sql("p")}
                ORDER BY p.pib COLLATE NOCASE
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    q = _normalize_spaces(query).casefold()
    # Копії, бо викликач може доповнювати записи для шаблону.
    out = [dict(r) for r in db_cache.get_or_load(db_cache.PATIENTS, load)]
    if q:
        out = [r for r in out if q in _normalize_spaces(r.get("pib", "")).casefold()]
    return out


def get_patient(patient_id: int) -> Optional[dict]:
    ensure_card_schema()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM patients WHERE id = ?", (int(patient_id),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_treatment(treatment_id: int) -> Optional[dict]:
    ensure_card_schema()
    conn = _connect()
    try:
        row = conn.execute(
            f"""
            SELECT t.*, p.pib, p.rank_unit AS patient_rank_unit, p.id AS patient_id_ref,
                   p.rank AS patient_rank, p.position AS patient_position,
                   p.unit_short AS patient_unit_short, p.birth_date AS patient_birth_date,
                   p.phone AS patient_phone, p.ipn AS patient_ipn
            FROM treatments t
            JOIN patients p ON p.id = t.patient_id
            WHERE t.id = ? AND {not_deleted_sql("t")}
            """,
            (int(treatment_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_treatments_for_patient(patient_id: int) -> list:
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT t.*,
                   (SELECT COUNT(*) FROM outpatient_entries e
                    WHERE e.treatment_id = t.id AND {not_deleted_sql("e")}) AS visits_count
            FROM treatments t
            WHERE t.patient_id = ? AND {not_deleted_sql("t")}
            ORDER BY CASE t.status WHEN 'active' THEN 0 ELSE 1 END, t.id DESC
            """,
            (int(patient_id),),
        ).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()
    for item in items:
        item["days_count"] = treatment_day_count(item)
        item["status_label"] = "Активне" if item.get("status") == "active" else "Завершене"
        item["cause_label"] = treatment_cause_label(item.get("cause") or "")
    return items


def list_visits_for_treatment(treatment_id: int) -> list:
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM outpatient_entries
            WHERE treatment_id = ? AND {not_deleted_sql()}
            ORDER BY id ASC
            """,
            (int(treatment_id),),
        ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            ct = item.get("care_type") or ""
            item["care_type_label"] = RESTRICTION_KINDS.get(ct, ct)
            we = _normalize_spaces(item.get("wa_event") or "")
            item["wa_event_label"] = ""
            for key, label in CARE_WA_EVENTS.get(ct, []):
                if key == we:
                    item["wa_event_label"] = label
                    break
            if not item["wa_event_label"] and we:
                item["wa_event_label"] = WA_EVENT_LABELS.get(we, we)
            item["exam_result_items"] = parse_recommendation_items(
                item.get("exam_result") or ""
            )
            item["referral_to_label"] = specialist_to_nominative(
                item.get("referral_to") or ""
            ) or (item.get("referral_to") or "")
            out.append(item)
        return out
    finally:
        conn.close()


def set_treatment_status(treatment_id: int, status: str) -> bool:
    ensure_card_schema()
    status = "closed" if status == "closed" else "active"
    conn = _connect()
    try:
        closed_on = today_ui() if status == "closed" else ""
        cur = conn.execute(
            "UPDATE treatments SET status = ?, closed_on = ?, updated_at = ? WHERE id = ?",
            (status, closed_on, utc_now(), int(treatment_id)),
        )
        if cur.rowcount > 0:
            _sync_notify_write(conn, "treatments", int(treatment_id))
        _commit(conn)
        return cur.rowcount > 0
    finally:
        conn.close()


def set_treatment_title(treatment_id: int, title: str) -> bool:
    """Змінює назву лікування."""
    title_n = _normalize_spaces(title)
    if not title_n:
        return False
    treatment = get_treatment(int(treatment_id))
    if not treatment:
        return False
    ensure_card_schema()
    conn = _connect()
    try:
        sid = treatment.get("sync_id") or new_sync_id()
        conn.execute(
            """
            UPDATE treatments
            SET title = ?, sync_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (title_n, sid, utc_now(), int(treatment_id)),
        )
        if not treatment.get("sync_id"):
            conn.execute(
                "UPDATE treatments SET sync_id = ? WHERE id = ?",
                (sid, int(treatment_id)),
            )
        touch_row(conn, "treatments", sid)
        enqueue_outbox(conn, "treatments", sid, "upsert")
        _commit(conn)
    finally:
        conn.close()
    return True


def set_treatment_cause(treatment_id: int, cause: str) -> bool:
    """Бойове → довідка + оплата; соматичне → ні довідка, ні оплата."""
    cause_n = normalize_treatment_cause(cause)
    if not cause_n:
        return False
    treatment = get_treatment(int(treatment_id))
    if not treatment:
        return False
    ensure_card_schema()
    conn = _connect()
    try:
        sid = treatment.get("sync_id") or new_sync_id()
        conn.execute(
            """
            UPDATE treatments
            SET cause = ?, needs_injury_cert = ?, sync_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                cause_n,
                "1" if cause_n == TREATMENT_CAUSE_COMBAT else "0",
                sid,
                utc_now(),
                int(treatment_id),
            ),
        )
        if not treatment.get("sync_id"):
            conn.execute(
                "UPDATE treatments SET sync_id = ? WHERE id = ?",
                (sid, int(treatment_id)),
            )
        touch_row(conn, "treatments", sid)
        enqueue_outbox(conn, "treatments", sid, "upsert")
        _commit(conn)
    finally:
        conn.close()
    if cause_n == TREATMENT_CAUSE_COMBAT:
        cid = attach_treatment_to_injury_case(int(treatment_id))
        if cid:
            update_injury_case(cid, skip_cert=False)
    return True


def _mark_row_deleted(conn, table: str, row_id: int) -> bool:
    row = conn.execute(
        f"SELECT id, sync_id FROM {table} WHERE id = ?",
        (int(row_id),),
    ).fetchone()
    if not row:
        return False
    if sync_enabled():
        sid = (row["sync_id"] if hasattr(row, "keys") else row[1]) or new_sync_id()
        if not (row["sync_id"] if hasattr(row, "keys") else row[1]):
            conn.execute(
                f"UPDATE {table} SET sync_id = ? WHERE id = ?",
                (sid, int(row_id)),
            )
        touch_row(conn, table, sid, deleted=True)
        enqueue_outbox(conn, table, sid, "delete")
    else:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (int(row_id),))
    return True


def delete_treatment(treatment_id: int) -> bool:
    """Видаляє лікування. Звернення журналу лишаються, але відв’язуються."""
    ensure_card_schema()
    tid = int(treatment_id)
    conn = _connect()
    try:
        existing = conn.execute(
            f"SELECT id FROM treatments WHERE id = ? AND {not_deleted_sql()}",
            (tid,),
        ).fetchone()
        if not existing:
            return False
        visit_rows = conn.execute(
            f"""
            SELECT id, sync_id FROM outpatient_entries
            WHERE treatment_id = ? AND {not_deleted_sql()}
            """,
            (tid,),
        ).fetchall()
        ts = utc_now()
        for visit in visit_rows:
            vid = int(visit["id"] if hasattr(visit, "keys") else visit[0])
            vsid = (visit["sync_id"] if hasattr(visit, "keys") else visit[1]) or new_sync_id()
            conn.execute(
                """
                UPDATE outpatient_entries
                SET treatment_id = NULL, sync_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (vsid, ts, vid),
            )
            if sync_enabled():
                touch_row(conn, "outpatient_entries", vsid)
                enqueue_outbox(conn, "outpatient_entries", vsid, "upsert")
        media_rows = conn.execute(
            f"SELECT id FROM treatment_media WHERE treatment_id = ? AND {not_deleted_sql()}",
            (tid,),
        ).fetchall()
        for media in media_rows:
            mid = int(media["id"] if hasattr(media, "keys") else media[0])
            _mark_row_deleted(conn, "treatment_media", mid)
        rest_rows = conn.execute(
            f"""
            SELECT id FROM treatment_restrictions
            WHERE treatment_id = ? AND {not_deleted_sql()}
            """,
            (tid,),
        ).fetchall()
        for rest in rest_rows:
            rid = int(rest["id"] if hasattr(rest, "keys") else rest[0])
            _mark_row_deleted(conn, "treatment_restrictions", rid)
        if not _mark_row_deleted(conn, "treatments", tid):
            return False
        _commit(conn)
        return True
    finally:
        conn.close()


def diagnosis_is_combat(text: str) -> bool:
    """Чи діагноз / назва випадку — бойова травма (патерни МВТ, ВОСП тощо)."""
    blob = _normalize_spaces(text)
    if not blob:
        return False
    try:
        from utils.payments_unpaid import match_combat_diagnosis

        return match_combat_diagnosis(blob) is not None
    except Exception:
        return False


def extract_injury_date_from_text(text: str) -> str:
    raw = _normalize_spaces(text)
    if not raw:
        return ""
    try:
        from utils.circumstances_parser import extract_injury_date

        found = extract_injury_date(raw) or ""
    except Exception:
        found = ""
    dt = parse_ui_date(found)
    return format_ui_date(dt) if dt else ""


def _injury_case_row(conn, case_id: int) -> Optional[dict]:
    row = conn.execute(
        f"SELECT * FROM injury_cases WHERE id = ? AND {not_deleted_sql()}",
        (int(case_id),),
    ).fetchone()
    return dict(row) if row else None


def get_injury_case(case_id: int) -> Optional[dict]:
    if not case_id:
        return None
    ensure_card_schema()
    conn = _connect()
    try:
        return _injury_case_row(conn, case_id)
    finally:
        conn.close()


def list_injury_certs(case_id: int) -> list[dict]:
    if not case_id:
        return []
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM treatment_media
            WHERE injury_case_id = ? AND kind = ? AND {not_deleted_sql()}
            ORDER BY id DESC
            """,
            (int(case_id), MEDIA_KIND_INJURY_CERT),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_treatments_for_injury_case(case_id: int) -> list[dict]:
    if not case_id:
        return []
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM treatments
            WHERE injury_case_id = ? AND {not_deleted_sql()}
            ORDER BY id ASC
            """,
            (int(case_id),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_injury_cases_for_patient(patient_id: int) -> list[dict]:
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT c.*,
                   (
                     SELECT COUNT(*) FROM treatment_media m
                     WHERE m.injury_case_id = c.id
                       AND m.kind = '{MEDIA_KIND_INJURY_CERT}'
                       AND {not_deleted_sql("m")}
                   ) AS cert_n
            FROM injury_cases c
            WHERE c.patient_id = ? AND {not_deleted_sql("c")}
            ORDER BY c.injury_date DESC, c.id DESC
            """,
            (int(patient_id),),
        ).fetchall()
        items = [dict(r) for r in rows]
        for item in items:
            cid = int(item.get("id") or 0)
            item["certs"] = list_injury_certs(cid)
            item["treatments"] = list_treatments_for_injury_case(cid)
            item["missing"] = _normalize_spaces(item.get("skip_cert")) != "1" and int(
                item.get("cert_n") or 0
            ) == 0
        return items
    finally:
        conn.close()


def list_payment_journal_episodes() -> list[dict]:
    """
    Звернення, що підлягають оплаті: лікування прив’язане до поранення
    і тип — стаціонар / реабілітація / відпустка.
    """
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT
              e.id AS visit_id,
              e.care_type,
              e.leave_start,
              e.leave_end,
              e.date AS visit_date,
              e.diagnosis,
              t.id AS treatment_id,
              t.title AS treatment_title,
              c.id AS injury_case_id,
              c.injury_date,
              c.title AS injury_title,
              p.pib, p.rank, p.position, p.unit_short, p.rank_unit
            FROM outpatient_entries e
            JOIN treatments t ON t.id = e.treatment_id
            JOIN injury_cases c ON c.id = t.injury_case_id
            JOIN patients p ON p.id = t.patient_id
            WHERE COALESCE(t.injury_case_id, 0) != 0
              AND t.cause = 'combat'
              AND e.care_type IN ('inpatient', 'rehab', 'vacation')
              AND {not_deleted_sql("e")}
              AND {not_deleted_sql("t")}
              AND {not_deleted_sql("c")}
              AND {not_deleted_sql("p")}
            ORDER BY p.pib, e.id
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_injury_case(
    patient_id: int,
    *,
    injury_date: str = "",
    title: str = "",
) -> int:
    ensure_card_schema()

    def _write() -> int:
        conn = _connect()
        try:
            cid = _create_injury_case_conn(
                conn,
                int(patient_id),
                injury_date=injury_date,
                title=title,
            )
            _commit(conn)
            return cid
        finally:
            conn.close()

    return int(retry_if_locked(_write))


def update_injury_case(case_id: int, **fields) -> Optional[dict]:
    ensure_card_schema()
    conn = _connect()
    try:
        existing = _injury_case_row(conn, case_id)
        if not existing:
            return None
        patch = {}
        if "injury_date" in fields:
            dt = parse_ui_date(fields.get("injury_date") or "")
            patch["injury_date"] = format_ui_date(dt) if dt else _normalize_spaces(
                fields.get("injury_date") or ""
            )
        if "title" in fields:
            patch["title"] = _normalize_spaces(fields.get("title")) or existing.get("title") or "Поранення"
        if "skip_cert" in fields:
            patch["skip_cert"] = "1" if fields.get("skip_cert") else ""
        if not patch:
            return existing
        sets = ", ".join(f"{k} = ?" for k in patch)
        conn.execute(
            f"UPDATE injury_cases SET {sets} WHERE id = ?",
            list(patch.values()) + [int(case_id)],
        )
        sid = existing.get("sync_id") or new_sync_id()
        if not existing.get("sync_id"):
            conn.execute(
                "UPDATE injury_cases SET sync_id = ? WHERE id = ?",
                (sid, int(case_id)),
            )
        touch_row(conn, "injury_cases", sid)
        enqueue_outbox(conn, "injury_cases", sid, "upsert")
        _commit(conn)
        return _injury_case_row(conn, case_id)
    finally:
        conn.close()


def _set_treatment_injury_case(conn, treatment_id: int, case_id: int) -> None:
    row = conn.execute(
        f"SELECT sync_id FROM treatments WHERE id = ? AND {not_deleted_sql()}",
        (int(treatment_id),),
    ).fetchone()
    if not row:
        return
    sid = (row["sync_id"] if hasattr(row, "keys") else row[0]) or new_sync_id()
    conn.execute(
        """
        UPDATE treatments
        SET injury_case_id = ?, needs_injury_cert = '1', cause = ?, sync_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (int(case_id or 0), TREATMENT_CAUSE_COMBAT, sid, utc_now(), int(treatment_id)),
    )
    touch_row(conn, "treatments", sid)
    enqueue_outbox(conn, "treatments", sid, "upsert")


def find_matching_injury_case(patient_id: int, injury_date: str) -> int:
    ensure_card_schema()
    date_n = ""
    dt = parse_ui_date(injury_date)
    if dt:
        date_n = format_ui_date(dt)
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT id, injury_date FROM injury_cases
            WHERE patient_id = ? AND {not_deleted_sql()}
            ORDER BY id DESC
            """,
            (int(patient_id),),
        ).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()
    if date_n:
        for item in items:
            other = parse_ui_date(item.get("injury_date") or "")
            if other and format_ui_date(other) == date_n:
                return int(item["id"])
        undated = [i for i in items if not parse_ui_date(i.get("injury_date") or "")]
        if len(undated) == 1:
            update_injury_case(int(undated[0]["id"]), injury_date=date_n)
            return int(undated[0]["id"])
        return 0
    if items:
        return int(items[0]["id"])
    return 0


def _find_matching_injury_case_conn(conn, patient_id: int, injury_date: str) -> int:
    date_n = ""
    dt = parse_ui_date(injury_date)
    if dt:
        date_n = format_ui_date(dt)
    else:
        date_n = extract_injury_date_from_text(injury_date) or _normalize_spaces(injury_date)
    rows = conn.execute(
        f"""
        SELECT id, injury_date, sync_id FROM injury_cases
        WHERE patient_id = ? AND {not_deleted_sql()}
        ORDER BY id DESC
        """,
        (int(patient_id),),
    ).fetchall()
    items = [dict(r) for r in rows]
    if date_n:
        for item in items:
            other = parse_ui_date(item.get("injury_date") or "")
            if other and format_ui_date(other) == date_n:
                return int(item["id"])
        undated = [i for i in items if not parse_ui_date(i.get("injury_date") or "")]
        if len(undated) == 1:
            cid = int(undated[0]["id"])
            conn.execute(
                "UPDATE injury_cases SET injury_date = ?, updated_at = ? WHERE id = ?",
                (date_n, utc_now(), cid),
            )
            sid = undated[0].get("sync_id") or ""
            if sid:
                touch_row(conn, "injury_cases", sid)
                enqueue_outbox(conn, "injury_cases", sid, "upsert")
            return cid
        return 0
    if items:
        return int(items[0]["id"])
    return 0


def _create_injury_case_conn(
    conn,
    patient_id: int,
    *,
    injury_date: str = "",
    title: str = "",
) -> int:
    date_n = extract_injury_date_from_text(injury_date) or (
        format_ui_date(parse_ui_date(injury_date)) if parse_ui_date(injury_date) else ""
    )
    if not date_n:
        dt = parse_ui_date(injury_date)
        date_n = format_ui_date(dt) if dt else _normalize_spaces(injury_date)
    title_n = _normalize_spaces(title) or "Поранення"
    sid = new_sync_id()
    ts = utc_now()
    cur = conn.execute(
        """
        INSERT INTO injury_cases
        (patient_id, injury_date, title, skip_cert, created_at, sync_id, updated_at)
        VALUES (?, ?, ?, '', ?, ?, ?)
        """,
        (int(patient_id), date_n, title_n, ts, sid, ts),
    )
    cid = int(cur.lastrowid)
    enqueue_outbox(conn, "injury_cases", sid, "upsert")
    return cid


def _attach_treatment_to_injury_case_conn(
    conn,
    treatment_id: int,
    *,
    patient_id: int = 0,
    title: str = "",
    diagnosis: str = "",
    case_id: int = 0,
) -> int:
    if case_id:
        _set_treatment_injury_case(conn, int(treatment_id), int(case_id))
        return int(case_id)
    pid = int(patient_id or 0)
    title_n = _normalize_spaces(title)
    if not pid or not title_n:
        row = conn.execute(
            f"SELECT patient_id, title FROM treatments WHERE id = ? AND {not_deleted_sql()}",
            (int(treatment_id),),
        ).fetchone()
        if not row:
            return 0
        pid = int(row["patient_id"] if hasattr(row, "keys") else row[0] or 0)
        title_n = _normalize_spaces(
            row["title"] if hasattr(row, "keys") else row[1]
        )
    if not pid:
        return 0
    date = extract_injury_date_from_text(title_n) or extract_injury_date_from_text(
        diagnosis
    )
    found = _find_matching_injury_case_conn(conn, pid, date)
    if not found:
        found = _create_injury_case_conn(
            conn,
            pid,
            injury_date=date,
            title=title_n or "Поранення",
        )
    _set_treatment_injury_case(conn, int(treatment_id), int(found))
    return int(found)


def attach_treatment_to_injury_case(
    treatment_id: int,
    *,
    diagnosis: str = "",
    case_id: int = 0,
) -> int:
    """Прив'язує лікування до випадку поранення (один PDF на всі епізоди)."""
    if not treatment_id:
        return 0
    ensure_card_schema()

    def _write() -> int:
        conn = _connect()
        try:
            cid = _attach_treatment_to_injury_case_conn(
                conn,
                int(treatment_id),
                diagnosis=diagnosis,
                case_id=int(case_id or 0),
            )
            if cid:
                _commit(conn)
            return int(cid or 0)
        finally:
            conn.close()

    return int(retry_if_locked(_write))


def maybe_flag_injury_cert(treatment_id: int, diagnosis: str = "") -> None:
    """Бойове лікування → випадок поранення і довідка. Соматичне — ні."""
    if not treatment_id:
        return
    treatment = get_treatment(int(treatment_id))
    if not treatment:
        return
    cause = normalize_treatment_cause(treatment.get("cause") or "")
    if cause == TREATMENT_CAUSE_SOMATIC:
        return
    if cause != TREATMENT_CAUSE_COMBAT:
        return
    if int(treatment.get("injury_case_id") or 0):
        return
    attach_treatment_to_injury_case(int(treatment_id), diagnosis=diagnosis)


def set_treatment_needs_injury_cert(treatment_id: int, needed: bool) -> bool:
    """Увімкнути / вимкнути очікування довідки для випадку поранення."""
    treatment = get_treatment(int(treatment_id))
    if not treatment:
        return False
    if needed:
        cid = int(treatment.get("injury_case_id") or 0) or attach_treatment_to_injury_case(
            int(treatment_id)
        )
        if cid:
            update_injury_case(cid, skip_cert=False)
        return True
    cid = int(treatment.get("injury_case_id") or 0)
    if cid:
        update_injury_case(cid, skip_cert=True)
        return True
    ensure_card_schema()
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT sync_id FROM treatments WHERE id = ? AND {not_deleted_sql()}",
            (int(treatment_id),),
        ).fetchone()
        if not row:
            return False
        sid = (row["sync_id"] if hasattr(row, "keys") else row[0]) or new_sync_id()
        conn.execute(
            "UPDATE treatments SET needs_injury_cert = '0', sync_id = ?, updated_at = ? WHERE id = ?",
            (sid, utc_now(), int(treatment_id)),
        )
        touch_row(conn, "treatments", sid)
        enqueue_outbox(conn, "treatments", sid, "upsert")
        _commit(conn)
        return True
    finally:
        conn.close()


def set_media_kind(media_id: int, kind: str, *, injury_case_id: int = 0) -> bool:
    ensure_card_schema()
    kind_n = _normalize_spaces(kind)
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT * FROM treatment_media WHERE id = ? AND {not_deleted_sql()}",
            (int(media_id),),
        ).fetchone()
        if not row:
            return False
        item = dict(row)
        sid = item.get("sync_id") or new_sync_id()
        cid = int(injury_case_id or item.get("injury_case_id") or 0)
        conn.execute(
            """
            UPDATE treatment_media
            SET kind = ?, injury_case_id = ?, sync_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (kind_n, cid, sid, utc_now(), int(media_id)),
        )
        touch_row(conn, "treatment_media", sid)
        enqueue_outbox(conn, "treatment_media", sid, "upsert")
        _commit(conn)
        return True
    finally:
        conn.close()


def injury_cert_status(treatment: dict, visits: list = None, media: list = None) -> dict:
    """Довідка потрібна лише для бойового лікування."""
    treatment = treatment or {}
    cid = int(treatment.get("injury_case_id") or 0)
    case = get_injury_case(cid) if cid else None
    certs = list_injury_certs(cid) if cid else []
    cause = normalize_treatment_cause(treatment.get("cause") or "")
    skip = _normalize_spaces((case or {}).get("skip_cert")) == "1"
    if cause == TREATMENT_CAUSE_SOMATIC:
        needed = False
    elif cause == TREATMENT_CAUSE_COMBAT:
        needed = not skip
    else:
        needed = (not skip) and bool(cid)
    missing = needed and not certs
    related = list_treatments_for_injury_case(cid) if cid else []
    return {
        "needed": needed,
        "auto": cause == TREATMENT_CAUSE_COMBAT,
        "flag": _normalize_spaces(treatment.get("needs_injury_cert")),
        "missing": missing,
        "certs": certs,
        "case": case or {},
        "case_id": cid,
        "injury_date": (case or {}).get("injury_date") or "",
        "related_treatments": related,
        "cause": cause,
        "cause_label": treatment_cause_label(cause),
        "patient_cases": list_injury_cases_for_patient(int(treatment.get("patient_id") or 0))
        if treatment.get("patient_id")
        else [],
    }


def list_missing_injury_certs() -> list[dict]:
    """Випадки поранення без PDF — один рядок на поранення, не на кожне лікування."""
    ensure_card_schema()
    backfill_injury_cert_case_ids()

    def load() -> list:
        conn = _connect()
        try:
            rows = conn.execute(
                f"""
                SELECT c.id, c.injury_date, c.title, c.skip_cert, c.created_at,
                       p.pib, p.id AS patient_id,
                       (
                         SELECT COUNT(*) FROM treatment_media m
                         WHERE m.kind = '{MEDIA_KIND_INJURY_CERT}'
                           AND {not_deleted_sql("m")}
                           AND (
                             m.injury_case_id = c.id
                             OR (
                               COALESCE(m.injury_case_id, 0) = 0
                               AND m.treatment_id IN (
                                 SELECT t.id FROM treatments t
                                 WHERE t.injury_case_id = c.id
                                   AND {not_deleted_sql("t")}
                               )
                             )
                           )
                       ) AS cert_n,
                       (
                         SELECT MIN(t.id) FROM treatments t
                         WHERE t.injury_case_id = c.id AND {not_deleted_sql("t")}
                       ) AS treatment_id,
                       (
                         SELECT MIN(t.started_on) FROM treatments t
                         WHERE t.injury_case_id = c.id AND {not_deleted_sql("t")}
                       ) AS started_on
                FROM injury_cases c
                JOIN patients p ON p.id = c.patient_id
                WHERE {not_deleted_sql("c")}
                  AND {not_deleted_sql("p")}
                  AND (c.skip_cert IS NULL OR c.skip_cert != '1')
                  AND EXISTS (
                    SELECT 1 FROM treatments t
                    WHERE t.injury_case_id = c.id
                      AND t.cause = '{TREATMENT_CAUSE_COMBAT}'
                      AND {not_deleted_sql("t")}
                  )
                ORDER BY c.id DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for row in db_cache.get_or_load(db_cache.INJURY_CERTS, load):
        if int(row.get("cert_n") or 0) > 0:
            continue
        start_dt = parse_ui_date(row.get("injury_date") or "") or parse_ui_date(
            row.get("started_on") or ""
        )
        wait = 0
        if start_dt:
            wait = max(0, (today.date() - start_dt.date()).days)
        wait_label = "сьогодні" if wait == 0 else (
            "1 день" if wait == 1 else f"{wait} дн."
        )
        out.append(
            {
                "injury_case_id": int(row.get("id") or 0),
                "treatment_id": int(row.get("treatment_id") or 0),
                "patient_id": int(row.get("patient_id") or 0),
                "pib": row.get("pib") or "",
                "title": row.get("title") or "Поранення",
                "started_on": row.get("injury_date") or row.get("started_on") or "",
                "status": "active",
                "waiting_days": wait,
                "waiting_label": wait_label,
            }
        )
    out.sort(key=lambda x: (-int(x.get("waiting_days") or 0), x.get("pib") or ""))
    return out


def backfill_injury_cert_case_ids() -> int:
    """
    Проставляє injury_case_id у PDF-довідках з лікування, якщо було 0.
    Інакше попередження «очікують PDF» не зникає навіть коли файл є.
    """
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT m.id, m.sync_id, t.injury_case_id AS case_id
            FROM treatment_media m
            JOIN treatments t ON t.id = m.treatment_id
            WHERE m.kind = ?
              AND COALESCE(m.injury_case_id, 0) = 0
              AND COALESCE(t.injury_case_id, 0) > 0
              AND {not_deleted_sql("m")}
              AND {not_deleted_sql("t")}
            """,
            (MEDIA_KIND_INJURY_CERT,),
        ).fetchall()
        n = 0
        for row in rows:
            mid = int(row["id"])
            cid = int(row["case_id"] or 0)
            if not cid:
                continue
            sid = row["sync_id"] or new_sync_id()
            conn.execute(
                """
                UPDATE treatment_media
                SET injury_case_id = ?, sync_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (cid, sid, utc_now(), mid),
            )
            touch_row(conn, "treatment_media", sid)
            enqueue_outbox(conn, "treatment_media", sid, "upsert")
            n += 1
        if n:
            _commit(conn)
        return n
    finally:
        conn.close()


def repair_cert_kind_for_combat_pdfs() -> int:
    """
    PDF з іменем 21*.pdf на бойовому лікуванні → kind=injury_cert + injury_case_id.
    Ловить випадки, коли файл уже в БД/хмарі, але kind залишився порожнім.
    """
    ensure_card_schema()
    conn = _connect()
    n = 0
    try:
        rows = conn.execute(
            f"""
            SELECT m.id, m.sync_id, m.kind, m.injury_case_id, m.original_name,
                   t.injury_case_id AS case_id, t.cause
            FROM treatment_media m
            JOIN treatments t ON t.id = m.treatment_id
            WHERE {not_deleted_sql("m")}
              AND {not_deleted_sql("t")}
              AND t.cause = '{TREATMENT_CAUSE_COMBAT}'
              AND COALESCE(t.injury_case_id, 0) > 0
              AND lower(COALESCE(m.original_name, '')) LIKE '21%.pdf'
              AND (
                COALESCE(m.kind, '') != '{MEDIA_KIND_INJURY_CERT}'
                OR COALESCE(m.injury_case_id, 0) = 0
              )
            """
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        cid = int(row["case_id"] or row["injury_case_id"] or 0)
        if set_media_kind(int(row["id"]), MEDIA_KIND_INJURY_CERT, injury_case_id=cid):
            n += 1
    if n:
        db_cache.invalidate_all()
    return n


def import_missing_certs_from_dropbox() -> dict:
    """
    Для поранень без довідки в БД шукає 21_*.pdf у локальному Dropbox patients/
    (папка може мати чужий #id з іншого ПК) і прив’язує як injury_cert.
    """
    ensure_card_schema()
    from utils.dropbox_sync import (
        find_injury_cert_pdfs_under_patient,
        patients_local_root,
    )

    linked = 0
    imported = 0
    if not patients_local_root():
        return {"linked": 0, "imported": 0}

    # свіжий список без кешу
    db_cache.invalidate_all()
    missing = list_missing_injury_certs()
    for item in missing:
        cid = int(item.get("injury_case_id") or 0)
        tid = int(item.get("treatment_id") or 0)
        pib = item.get("pib") or ""
        title = item.get("title") or ""
        if not cid or not tid or not pib:
            continue
        pdfs = find_injury_cert_pdfs_under_patient(pib=pib, treatment_title=title)
        if not pdfs:
            continue
        pdf_path = pdfs[0]
        original = os.path.basename(pdf_path)

        # уже є медіа з тим самим іменем — лише проставити kind
        conn = _connect()
        try:
            row = conn.execute(
                f"""
                SELECT id, kind FROM treatment_media
                WHERE treatment_id = ?
                  AND {not_deleted_sql()}
                  AND (
                    lower(original_name) = lower(?)
                    OR lower(filename) = lower(?)
                  )
                ORDER BY id DESC LIMIT 1
                """,
                (tid, original, original),
            ).fetchone()
        finally:
            conn.close()
        if row:
            if _normalize_spaces(row["kind"]) != MEDIA_KIND_INJURY_CERT:
                if set_media_kind(int(row["id"]), MEDIA_KIND_INJURY_CERT, injury_case_id=cid):
                    linked += 1
            # шлях Dropbox
            try:
                from utils.dropbox_sync import patients_local_root as _root

                root = _root()
                if root and pdf_path.startswith(root):
                    rel = pdf_path[len(root) :].replace("\\", "/").lstrip("/")
                    set_media_dropbox_path(int(row["id"]), "/patients/" + rel)
            except Exception:
                pass
            continue

        try:
            with open(pdf_path, "rb") as fh:
                data = fh.read()
            media = save_media_bytes(
                tid,
                data,
                original,
                kind=MEDIA_KIND_INJURY_CERT,
                injury_case_id=cid,
            )
            mid = int(media.get("id") or 0)
            if mid:
                try:
                    root = patients_local_root()
                    if root and pdf_path.startswith(root):
                        rel = pdf_path[len(root) :].replace("\\", "/").lstrip("/")
                        set_media_dropbox_path(mid, "/patients/" + rel)
                except Exception:
                    pass
                imported += 1
        except Exception as e:
            logger.warning(
                "import cert from Dropbox failed %s: %s", pdf_path, e
            )

    if linked or imported:
        db_cache.invalidate_all()
    return {"linked": linked, "imported": imported}


def republish_injury_certs_for_sync() -> dict:
    """
    Локальні довідки (kind=injury_cert) повторно ставить у outbox з новим updated_at,
    щоб Turso отримав kind/injury_case_id (старі push часто пішли ще з kind='').
    Також підставляє dropbox_path зі знайденого файлу в patients/, без нової папки.
    """
    ensure_card_schema()
    from utils.dropbox_sync import (
        api_media_path_to_local,
        find_dropbox_api_path_under_patient,
        find_media_under_patient,
        patient_media_dropbox_path,
    )

    republished = 0
    paths_set = 0
    for media in list_injury_cert_media():
        mid = int(media.get("id") or 0)
        tid = int(media.get("treatment_id") or 0)
        if not mid or not tid:
            continue
        treatment = get_treatment(tid) or {}
        pib = treatment.get("pib") or ""
        title = treatment.get("title") or "treatment"
        t_sync = _normalize_spaces(treatment.get("sync_id") or "")
        original = media.get("original_name") or ""
        filename = media.get("filename") or ""
        dbx = _normalize_spaces(media.get("dropbox_path") or "")

        if not dbx:
            # 1) локальна папка Dropbox (старий шаблон #id)
            local_hit = find_media_under_patient(
                pib=pib, original_name=original, filename=filename
            )
            api_path = ""
            if local_hit:
                # зібрати API-шлях відносний до patients/
                try:
                    from utils.dropbox_sync import patients_local_root

                    root = patients_local_root()
                    if root and local_hit.startswith(root):
                        rel = local_hit[len(root) :].replace("\\", "/").lstrip("/")
                        api_path = "/patients/" + rel
                except Exception:
                    api_path = ""
            if not api_path:
                try:
                    api_path = (
                        find_dropbox_api_path_under_patient(
                            pib=pib, original_name=original, filename=filename
                        )
                        or ""
                    )
                except Exception:
                    api_path = ""
            if not api_path and tid:
                # кандидати старий (#id) і новий (sync_id)
                for use_sid in (t_sync, ""):
                    for name in (original, filename):
                        if not name:
                            continue
                        cand = patient_media_dropbox_path(
                            pib=pib,
                            treatment_title=title,
                            treatment_id=tid,
                            filename=name,
                            treatment_sync_id=use_sid,
                        )
                        mapped = api_media_path_to_local(cand)
                        if mapped and os.path.isfile(mapped):
                            api_path = cand
                            break
                    if api_path:
                        break
            if api_path and set_media_dropbox_path(mid, api_path):
                paths_set += 1
                media = get_media(mid) or media

        # завжди перепублікувати kind + injury_case_id у хмару
        cid = int(media.get("injury_case_id") or 0)
        if not cid:
            cid = int(treatment.get("injury_case_id") or 0)
        if set_media_kind(mid, MEDIA_KIND_INJURY_CERT, injury_case_id=cid):
            republished += 1

    return {"republished": republished, "paths_set": paths_set}


def _backfill_injury_cases(conn) -> None:
    """Старі лікування з бойовим діагнозом / PDF збирає в один випадок поранення."""
    tcols = {str(row[1]) for row in conn.execute("PRAGMA table_info(treatments)").fetchall()}
    if "injury_case_id" not in tcols:
        return
    icols = {str(row[1]) for row in conn.execute("PRAGMA table_info(injury_cases)").fetchall()}
    if not icols:
        return
    _add_col(conn, "injury_cases", "sync_id", "TEXT NOT NULL DEFAULT ''", icols)
    _add_col(conn, "injury_cases", "updated_at", "TEXT NOT NULL DEFAULT ''", icols)
    _add_col(conn, "injury_cases", "deleted_at", "TEXT NOT NULL DEFAULT ''", icols)
    rows = conn.execute(
        f"""
        SELECT id, patient_id, title, needs_injury_cert, injury_case_id
        FROM treatments
        WHERE {not_deleted_sql()} AND COALESCE(injury_case_id, 0) = 0
        """
    ).fetchall()
    for row in rows:
        item = dict(row)
        tid = int(item.get("id") or 0)
        title = item.get("title") or ""
        needs = _normalize_spaces(item.get("needs_injury_cert"))
        if needs == "0":
            continue
        has_pdf = conn.execute(
            f"""
            SELECT 1 FROM treatment_media
            WHERE treatment_id = ? AND kind = ? AND {not_deleted_sql()}
            LIMIT 1
            """,
            (tid, MEDIA_KIND_INJURY_CERT),
        ).fetchone()
        if needs != "1" and not has_pdf and not diagnosis_is_combat(title):
            continue
        # attach без рекурсії ensure: мінімальний find/create в цьому conn
        pid = int(item.get("patient_id") or 0)
        date = extract_injury_date_from_text(title)
        found = 0
        cases = conn.execute(
            f"""
            SELECT id, injury_date FROM injury_cases
            WHERE patient_id = ? AND {not_deleted_sql()}
            ORDER BY id DESC
            """,
            (pid,),
        ).fetchall()
        case_items = [dict(c) for c in cases]
        if date:
            for c in case_items:
                other = parse_ui_date(c.get("injury_date") or "")
                if other and format_ui_date(other) == date:
                    found = int(c["id"])
                    break
            if not found:
                undated = [c for c in case_items if not parse_ui_date(c.get("injury_date") or "")]
                if len(undated) == 1:
                    found = int(undated[0]["id"])
                    conn.execute(
                        "UPDATE injury_cases SET injury_date = ? WHERE id = ?",
                        (date, found),
                    )
        elif case_items:
            found = int(case_items[0]["id"])
        if not found:
            sid = new_sync_id()
            ts = utc_now()
            cur = conn.execute(
                """
                INSERT INTO injury_cases
                (patient_id, injury_date, title, skip_cert, created_at, sync_id, updated_at)
                VALUES (?, ?, ?, '', ?, ?, ?)
                """,
                (pid, date, title or "Поранення", ts, sid, ts),
            )
            found = int(cur.lastrowid)
            enqueue_outbox(conn, "injury_cases", sid, "upsert")
        tsid = item.get("sync_id") or new_sync_id()
        conn.execute(
            """
            UPDATE treatments
            SET injury_case_id = ?, needs_injury_cert = '1', cause = ?, updated_at = ?
            WHERE id = ?
            """,
            (found, TREATMENT_CAUSE_COMBAT, utc_now(), tid),
        )
        conn.execute(
            f"""
            UPDATE treatment_media
            SET injury_case_id = ?
            WHERE treatment_id = ? AND kind = ? AND {not_deleted_sql()}
            """,
            (found, tid, MEDIA_KIND_INJURY_CERT),
        )


def _backfill_treatment_cause(conn) -> None:
    tcols = {str(row[1]) for row in conn.execute("PRAGMA table_info(treatments)").fetchall()}
    if "cause" not in tcols:
        return
    conn.execute(
        f"""
        UPDATE treatments
        SET cause = '{TREATMENT_CAUSE_COMBAT}'
        WHERE {not_deleted_sql()}
          AND COALESCE(injury_case_id, 0) != 0
          AND TRIM(COALESCE(cause, '')) = ''
        """
    )




def discharge_period(treatment: dict, visits: list) -> tuple[str, str, str]:
    """
    Період для WhatsApp при завершенні: (care_type, start, end).
    Старт — started_on лікування або перший leave_start; кінець — leave_end / closed_on.
    """
    care = ""
    start = _normalize_spaces((treatment or {}).get("started_on") or "")
    end = _normalize_spaces((treatment or {}).get("closed_on") or "")
    for v in visits or []:
        ct = _normalize_spaces(v.get("care_type") or "")
        if ct and ct not in ("consultation", "referral"):
            care = ct
        elif ct and not care:
            care = ct
        ls = _normalize_spaces(v.get("leave_start") or "")
        le = _normalize_spaces(v.get("leave_end") or "")
        if ls and (not start or (parse_ui_date(ls) and parse_ui_date(start) and parse_ui_date(ls) < parse_ui_date(start))):
            start = ls
        if le:
            end = le
    if not end:
        end = today_ui()
    if not care:
        last = (visits or [{}])[-1] if visits else {}
        care = _normalize_spaces(last.get("care_type") or "") or "outpatient"
    return care, start, end


def treatment_day_count(treatment: dict) -> int:
    start = parse_ui_date(treatment.get("started_on") or "")
    if not start:
        visits = list_visits_for_treatment(int(treatment["id"])) if treatment.get("id") else []
        for v in visits:
            start = parse_ui_date(v.get("date") or "")
            if start:
                break
    if not start:
        return 0
    if treatment.get("status") == "closed":
        end = parse_ui_date(treatment.get("closed_on") or "") or datetime.now()
    else:
        end = datetime.now()
    return calendar_days_inclusive(start, end)


def delete_restrictions_for_visit(visit_id: int) -> int:
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, sync_id FROM treatment_restrictions WHERE visit_id = ?",
            (int(visit_id),),
        ).fetchall()
        count = 0
        for row in rows:
            rid = int(row["id"])
            sid = row["sync_id"] or ""
            if sync_enabled():
                if not sid:
                    sid = new_sync_id()
                    conn.execute(
                        "UPDATE treatment_restrictions SET sync_id = ? WHERE id = ?",
                        (sid, rid),
                    )
                touch_row(conn, "treatment_restrictions", sid, deleted=True)
                enqueue_outbox(conn, "treatment_restrictions", sid, "delete")
                count += 1
            else:
                conn.execute(
                    "DELETE FROM treatment_restrictions WHERE id = ?", (rid,)
                )
                count += 1
        _commit(conn)
        return count
    finally:
        conn.close()


def add_restriction(
    treatment_id: int,
    *,
    kind: str,
    start_on: str,
    days: int = 0,
    end_on: str = "",
    visit_id: Optional[int] = None,
    note: str = "",
) -> int:
    ensure_card_schema()
    kind = kind if kind in RESTRICTION_KINDS else "outpatient"
    start_dt = parse_ui_date(start_on) or datetime.now()
    end_dt = parse_ui_date(end_on)
    days_n = 0
    try:
        days_n = int(str(days).strip() or "0")
    except ValueError:
        days_n = 0
    if end_dt is None:
        if days_n <= 0:
            days_n = 1
        end_dt = add_days_inclusive(start_dt, days_n)
    else:
        days_n = calendar_days_inclusive(start_dt, end_dt)
    conn = _connect()
    try:
        sid = new_sync_id()
        ts = utc_now()
        cur = conn.execute(
            """
            INSERT INTO treatment_restrictions
            (treatment_id, visit_id, kind, start_on, end_on, days, note, sync_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(treatment_id),
                int(visit_id) if visit_id else None,
                kind,
                format_ui_date(start_dt),
                format_ui_date(end_dt),
                days_n,
                _normalize_spaces(note),
                sid,
                ts,
            ),
        )
        rid = int(cur.lastrowid)
        enqueue_outbox(conn, "treatment_restrictions", sid, "upsert")
        _commit(conn)
        return rid
    finally:
        conn.close()


def list_restrictions(treatment_id: int) -> list:
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM treatment_restrictions
            WHERE treatment_id = ?
            ORDER BY id DESC
            """,
            (int(treatment_id),),
        ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["kind_label"] = RESTRICTION_KINDS.get(item.get("kind"), item.get("kind"))
            out.append(item)
        return out
    finally:
        conn.close()


def delete_restriction(restriction_id: int) -> bool:
    ensure_card_schema()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT sync_id FROM treatment_restrictions WHERE id = ?",
            (int(restriction_id),),
        ).fetchone()
        if not row:
            return False
        sid = row["sync_id"] or new_sync_id()
        if sync_enabled():
            touch_row(conn, "treatment_restrictions", sid, deleted=True)
            enqueue_outbox(conn, "treatment_restrictions", sid, "delete")
        else:
            conn.execute(
                "DELETE FROM treatment_restrictions WHERE id = ?",
                (int(restriction_id),),
            )
        _commit(conn)
        return True
    finally:
        conn.close()


def restriction_totals(treatment_id: int) -> dict:
    rows = list_restrictions(treatment_id)
    totals = {k: 0 for k in RESTRICTION_KINDS}
    for r in rows:
        kind = r.get("kind")
        if kind in totals:
            totals[kind] += int(r.get("days") or 0)
    return totals


def visit_care_totals(treatment_id: int) -> dict:
    """Суми днів за типом з звернень лікування (leave_days)."""
    totals = {k: 0 for k in RESTRICTION_KINDS}
    for v in list_visits_for_treatment(int(treatment_id)):
        kind = v.get("care_type") or ""
        if kind not in totals:
            continue
        try:
            totals[kind] += int(str(v.get("leave_days") or "0").strip() or "0")
        except ValueError:
            continue
    return totals


def visit_has_leave_period(visit: dict) -> bool:
    if not visit:
        return False
    return bool(
        _normalize_spaces(visit.get("leave_end") or "")
        or _normalize_spaces(visit.get("leave_start") or "")
        or _normalize_spaces(visit.get("leave_days") or "")
    )


def wa_event_for_visit(visit: dict) -> str:
    """
    Явно обрана подія в зверненні має пріоритет.
    Інакше: перше звернення з періодом → open, наступні → extend.
    """
    if not visit:
        return WA_EVENT_OPEN
    explicit = _normalize_spaces(visit.get("wa_event") or "")
    if explicit in (WA_EVENT_OPEN, WA_EVENT_EXTEND, WA_EVENT_DISCHARGE):
        return explicit
    tid = visit.get("treatment_id")
    if not tid:
        return WA_EVENT_OPEN
    try:
        vid = int(visit.get("id") or 0)
        tid_i = int(tid)
    except (TypeError, ValueError):
        return WA_EVENT_OPEN
    prior = [
        v for v in list_visits_for_treatment(tid_i)
        if int(v.get("id") or 0) < vid and visit_has_leave_period(v)
    ]
    if prior and visit_has_leave_period(visit):
        return WA_EVENT_EXTEND
    return WA_EVENT_OPEN


def suggest_extend_prefill(treatment_id: int) -> Optional[dict]:
    """Дані для форми «Продовжити» (нове звернення в тому ж лікуванні)."""
    treatment = get_treatment(int(treatment_id))
    if not treatment:
        return None
    visits = list_visits_for_treatment(int(treatment_id))
    last_leave = None
    for v in reversed(visits):
        if visit_has_leave_period(v):
            last_leave = v
            break
    last = last_leave or (visits[-1] if visits else None)
    leave_start = today_ui()
    if last and last.get("leave_end"):
        end_dt = parse_ui_date(last.get("leave_end") or "")
        if end_dt:
            leave_start = format_ui_date(end_dt + timedelta(days=1))
    patient = get_patient(int(treatment["patient_id"])) or {}
    return {
        "treatment_id": str(int(treatment_id)),
        "pib": treatment.get("pib") or patient.get("pib") or "",
        "rank_unit": patient.get("rank_unit") or treatment.get("patient_rank_unit") or "",
        "diagnosis": (last or {}).get("diagnosis") or treatment.get("title") or "",
        "referral_to": (last or {}).get("referral_to") or "",
        "lpz": (last or {}).get("lpz") or "",
        "exam_result": "Продовження лікування",
        "care_type": (last or {}).get("care_type") or "",
        "wa_event": WA_EVENT_EXTEND,
        "leave_start": leave_start,
        "leave_end": "",
        "leave_days": "",
        "notes": "",
    }


def list_upcoming_reminders(within_days: int = None) -> list:
    """
    Нагадування про закінчення поточного періоду лікування.

    Якщо після стаціонару вже відкрита відпустка / наступний епізод —
    беремо лише останній leave_end цього лікування, а не кожне звернення.
    """
    ensure_card_schema()
    within = int(within_days if within_days is not None else REMINDER_WITHIN_DAYS)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = today + timedelta(days=within)

    def load() -> list:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT e.id AS visit_id, e.leave_end AS end_on, e.leave_start AS start_on,
                       e.leave_days AS days, e.care_type AS kind, e.diagnosis,
                       e.treatment_id, t.title AS treatment_title, t.status AS treatment_status,
                       p.pib, p.id AS patient_id
                FROM outpatient_entries e
                JOIN treatments t ON t.id = e.treatment_id
                JOIN patients p ON p.id = t.patient_id
                WHERE t.status = 'active'
                  AND e.leave_end IS NOT NULL
                  AND TRIM(e.leave_end) != ''
                  AND """
                + not_deleted_sql("e")
                + """
                  AND """
                + not_deleted_sql("t")
                + """
                  AND """
                + not_deleted_sql("p")
                + """
                ORDER BY e.leave_end
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    rows = db_cache.get_or_load(db_cache.REMINDERS, load)
    latest_by_treatment: dict[int, dict] = {}
    for r in rows:
        end_dt = parse_ui_date(r.get("end_on") or "")
        if not end_dt:
            continue
        try:
            tid = int(r.get("treatment_id") or 0)
        except (TypeError, ValueError):
            continue
        if not tid:
            continue
        prev = latest_by_treatment.get(tid)
        prev_end = prev.get("_end_dt") if prev else None
        prev_vid = int(prev.get("visit_id") or 0) if prev else 0
        vid = int(r.get("visit_id") or 0)
        if not prev or end_dt > prev_end or (end_dt == prev_end and vid > prev_vid):
            latest_by_treatment[tid] = {**r, "_end_dt": end_dt}

    reminders = []
    for r in latest_by_treatment.values():
        end_day = r["_end_dt"].replace(hour=0, minute=0, second=0, microsecond=0)
        if end_day < today or end_day > horizon:
            continue
        left = (end_day.date() - today.date()).days
        kind = r.get("kind") or ""
        item = {k: v for k, v in r.items() if k != "_end_dt"}
        item["kind_label"] = RESTRICTION_KINDS.get(kind, kind or "Період")
        item["days_left"] = left
        item["days_left_label"] = (
            "сьогодні" if left == 0 else ("завтра" if left == 1 else f"через {left} дн.")
        )
        reminders.append(item)
    reminders.sort(key=lambda x: (x.get("days_left", 99), x.get("pib", "")))
    return reminders


def _inpatient_anchor_date(visit: dict) -> Optional[datetime]:
    """Дата, від якої рахуємо дні до дзвінка медика: останній дзвінок або початок стаціонару."""
    call = parse_ui_date(visit.get("medic_call_date") or "")
    if call:
        return call
    start = parse_ui_date(visit.get("leave_start") or "")
    if start:
        return start
    return parse_ui_date(visit.get("date") or visit.get("visit_date") or "")


def list_active_inpatients(*, call_after_days: int = None) -> list[dict]:
    """
    Поточні стаціонари (активне лікування, останнє inpatient-звернення без виписки).
    days_since — повні дні від початку / останнього дзвінка медика.
    needs_call — True, якщо days_since > call_after_days (за замовчуванням 10).
    """
    ensure_card_schema()
    threshold = int(
        call_after_days if call_after_days is not None else INPATIENT_MEDIC_CALL_DAYS
    )
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT e.id AS visit_id, e.date AS visit_date, e.pib, e.diagnosis,
                   e.lpz, e.from_lpz, e.leave_start, e.leave_end, e.wa_event,
                   e.medic_call_date, e.treatment_id,
                   t.title AS treatment_title, t.status AS treatment_status,
                   t.cause AS treatment_cause,
                   p.id AS patient_id, p.phone, p.unit_short, p.rank_unit, p.rank
            FROM outpatient_entries e
            JOIN treatments t ON t.id = e.treatment_id
            JOIN patients p ON p.id = t.patient_id
            WHERE t.status = 'active'
              AND e.care_type = 'inpatient'
              AND {not_deleted_sql("e")}
              AND {not_deleted_sql("t")}
              AND {not_deleted_sql("p")}
            ORDER BY e.id DESC
            """
        ).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()

    latest: dict[int, dict] = {}
    for item in items:
        try:
            tid = int(item.get("treatment_id") or 0)
        except (TypeError, ValueError):
            continue
        if not tid or tid in latest:
            continue
        event = _normalize_spaces(item.get("wa_event") or "")
        end = _normalize_spaces(item.get("leave_end") or "")
        if event == WA_EVENT_DISCHARGE or end:
            latest[tid] = None
            continue
        latest[tid] = item

    out: list[dict] = []
    for row in latest.values():
        if not row:
            continue
        anchor = _inpatient_anchor_date(row)
        if not anchor:
            days_since = 0
        else:
            days_since = (today.date() - anchor.date()).days
            if days_since < 0:
                days_since = 0
        item = dict(row)
        item["anchor_date"] = format_ui_date(anchor) if anchor else ""
        item["days_since"] = days_since
        item["days_until_call"] = max(0, threshold - days_since)
        item["needs_call"] = days_since > threshold
        item["call_threshold"] = threshold
        item["wa_event_label"] = {
            WA_EVENT_OPEN: "Госпіталізований",
            WA_EVENT_EXTEND: "Переведений",
            WA_EVENT_DISCHARGE: "Виписаний",
        }.get(_normalize_spaces(item.get("wa_event") or ""), item.get("wa_event") or "—")
        item["cause_label"] = treatment_cause_label(item.get("treatment_cause") or "")
        out.append(item)

    out.sort(
        key=lambda x: (
            0 if x.get("needs_call") else 1,
            -(int(x.get("days_since") or 0)),
            (x.get("pib") or "").casefold(),
        )
    )
    return out


def count_inpatients_needing_medic_call(*, call_after_days: int = None) -> int:
    return sum(1 for r in list_active_inpatients(call_after_days=call_after_days) if r.get("needs_call"))


def set_medic_call_date(visit_id: int, call_date: str) -> bool:
    """Фіксує дату дзвінка медика для звернення; лічильник 10 днів стартує знову."""
    ensure_card_schema()
    try:
        vid = int(visit_id)
    except (TypeError, ValueError):
        return False
    dt = parse_ui_date(call_date or "")
    if not dt:
        return False
    value = format_ui_date(dt)

    def _write() -> bool:
        conn = _connect()
        try:
            row = conn.execute(
                f"""
                SELECT id, sync_id, care_type FROM outpatient_entries
                WHERE id = ? AND {not_deleted_sql()}
                """,
                (vid,),
            ).fetchone()
            if not row:
                return False
            if _normalize_spaces(row["care_type"] or "") != "inpatient":
                return False
            ts = utc_now()
            conn.execute(
                """
                UPDATE outpatient_entries
                SET medic_call_date = ?, updated_at = ?
                WHERE id = ?
                """,
                (value, ts, vid),
            )
            sid = (row["sync_id"] if row else "") or new_sync_id()
            if not row["sync_id"]:
                conn.execute(
                    "UPDATE outpatient_entries SET sync_id = ? WHERE id = ?",
                    (sid, vid),
                )
            enqueue_outbox(conn, "outpatient_entries", sid, "upsert")
            conn.commit()
            db_cache.invalidate_all()
            return True
        finally:
            conn.close()

    return bool(retry_if_locked(_write))


def list_active_combat_treatments() -> list[dict]:
    """Активні лікування у зв'язку з пораненням (бойове)."""
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT t.id AS treatment_id, t.title AS treatment_title, t.started_on,
                   t.cause, t.injury_case_id,
                   c.injury_date, c.title AS injury_title,
                   p.id AS patient_id, p.pib, p.phone, p.unit_short, p.rank_unit, p.rank,
                   (
                     SELECT e.care_type FROM outpatient_entries e
                     WHERE e.treatment_id = t.id
                       AND TRIM(COALESCE(e.care_type, '')) != ''
                       AND {not_deleted_sql("e")}
                     ORDER BY e.id DESC LIMIT 1
                   ) AS current_care_type,
                   (
                     SELECT e.lpz FROM outpatient_entries e
                     WHERE e.treatment_id = t.id
                       AND TRIM(COALESCE(e.care_type, '')) != ''
                       AND {not_deleted_sql("e")}
                     ORDER BY e.id DESC LIMIT 1
                   ) AS current_lpz,
                   (
                     SELECT e.leave_start FROM outpatient_entries e
                     WHERE e.treatment_id = t.id
                       AND TRIM(COALESCE(e.care_type, '')) != ''
                       AND {not_deleted_sql("e")}
                     ORDER BY e.id DESC LIMIT 1
                   ) AS current_leave_start,
                   (
                     SELECT e.leave_end FROM outpatient_entries e
                     WHERE e.treatment_id = t.id
                       AND TRIM(COALESCE(e.care_type, '')) != ''
                       AND {not_deleted_sql("e")}
                     ORDER BY e.id DESC LIMIT 1
                   ) AS current_leave_end
            FROM treatments t
            JOIN patients p ON p.id = t.patient_id
            LEFT JOIN injury_cases c ON c.id = t.injury_case_id
              AND {not_deleted_sql("c")}
            WHERE t.status = 'active'
              AND t.cause = ?
              AND {not_deleted_sql("t")}
              AND {not_deleted_sql("p")}
            ORDER BY p.pib COLLATE NOCASE, t.id DESC
            """,
            (TREATMENT_CAUSE_COMBAT,),
        ).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()

    for item in items:
        item["cause_label"] = treatment_cause_label(item.get("cause") or "")
        last_care = _normalize_spaces(item.get("current_care_type") or "")
        item["current_care_type"] = last_care
        item["current_care_label"] = RESTRICTION_KINDS.get(last_care, last_care or "—")
        item["current_lpz"] = _normalize_spaces(item.get("current_lpz") or "")
        item["current_leave_start"] = _normalize_spaces(item.get("current_leave_start") or "")
        item["current_leave_end"] = _normalize_spaces(item.get("current_leave_end") or "")
    return items


def list_polyclinic_referrals(on_date: str) -> list[dict]:
    """
    Звернення з типом «Направлення на консультацію» на обрану дату (поле date).
    """
    ensure_card_schema()
    dt = parse_ui_date(on_date or "")
    if not dt:
        return []
    day = format_ui_date(dt)
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT e.id AS visit_id, e.date AS visit_date, e.pib, e.diagnosis,
                   e.referral_to, e.lpz, e.exam_result, e.notes, e.treatment_id,
                   t.title AS treatment_title,
                   p.id AS patient_id, p.phone, p.unit_short, p.rank_unit, p.rank
            FROM outpatient_entries e
            LEFT JOIN treatments t ON t.id = e.treatment_id
              AND {not_deleted_sql("t")}
            LEFT JOIN patients p ON p.id = t.patient_id
              AND {not_deleted_sql("p")}
            WHERE e.care_type = 'referral'
              AND e.date = ?
              AND {not_deleted_sql("e")}
            ORDER BY e.pib COLLATE NOCASE, e.id
            """,
            (day,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_active_vlk_people() -> list[dict]:
    """Люди з активним лікуванням, у яких останнє звернення ВЛК ще не «завершив»."""
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT e.id, e.pib, e.lpz, e.diagnosis, e.date, e.leave_start,
                   e.wa_event, e.vlk_passed, e.treatment_id, t.title AS treatment_title,
                   p.id AS patient_id
            FROM outpatient_entries e
            JOIN treatments t ON t.id = e.treatment_id
            JOIN patients p ON p.id = t.patient_id
            WHERE t.status = 'active'
              AND e.care_type = 'vlk'
              AND {not_deleted_sql("e")}
              AND {not_deleted_sql("t")}
              AND {not_deleted_sql("p")}
            ORDER BY e.id DESC
            """
        ).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()
    latest = {}
    for item in items:
        tid = item.get("treatment_id")
        if tid in latest:
            continue
        event = _normalize_spaces(item.get("wa_event") or "")
        if event == WA_EVENT_DISCHARGE:
            latest[tid] = None
            continue
        latest[tid] = item
    out = [row for row in latest.values() if row]
    out.sort(key=lambda x: (x.get("pib") or "").casefold())
    return out


def save_media_bytes(
    treatment_id: int,
    data: bytes,
    original_name: str,
    *,
    visit_id: Optional[int] = None,
    mime_hint: str = "",
    kind: str = "",
    injury_case_id: int = 0,
) -> dict:
    """Зберігає медіа з bytes (для Gemini / Dropbox-синхронізації)."""
    ensure_card_schema()
    raw_name = _normalize_spaces(original_name) or "file"
    ext = os.path.splitext(raw_name)[1].lower()
    if not ext and mime_hint:
        mime_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "application/pdf": ".pdf",
        }
        ext = mime_map.get(mime_hint.lower().strip(), "")
    if ext not in _ALLOWED_MEDIA_EXT:
        raise ValueError("Дозволені лише зображення (jpg/jpeg/png/webp/gif) та PDF")
    if not data:
        raise ValueError("Порожній файл")
    if len(data) > TREATMENT_MEDIA_MAX_BYTES:
        raise ValueError("Файл більший за 10 МБ")
    safe_stem = secure_filename(os.path.splitext(raw_name)[0]) or "file"
    original = f"{safe_stem}{ext}"
    mime = _ALLOWED_MEDIA_EXT[ext]
    tid = int(treatment_id or 0)
    cid = int(injury_case_id or 0)
    if not tid and cid:
        related = list_treatments_for_injury_case(cid)
        tid = int(related[0]["id"]) if related else 0
    if not tid:
        raise ValueError("Немає лікування, до якого прив’язати файл")
    folder = os.path.join(_cfg.TREATMENT_MEDIA_DIR, str(tid))
    os.makedirs(folder, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(folder, stored)
    with open(path, "wb") as fh:
        fh.write(data)
    conn = _connect()
    try:
        sid = new_sync_id()
        ts = utc_now()
        cur = conn.execute(
            """
            INSERT INTO treatment_media
            (treatment_id, visit_id, filename, original_name, mime, kind, injury_case_id,
             sync_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tid,
                int(visit_id) if visit_id else None,
                stored,
                original,
                mime,
                _normalize_spaces(kind),
                cid,
                sid,
                ts,
            ),
        )
        mid = int(cur.lastrowid)
        enqueue_outbox(conn, "treatment_media", sid, "upsert")
        queue_media_upload(conn, mid)
        _commit(conn)
    finally:
        conn.close()
    return {
        "id": mid,
        "treatment_id": tid,
        "injury_case_id": cid,
        "filename": stored,
        "original_name": original,
        "mime": mime,
        "bytes": data,
        "disk_path": path,
    }


def save_media_file(
    treatment_id: int,
    file_storage: FileStorage,
    *,
    visit_id: Optional[int] = None,
    kind: str = "",
    injury_case_id: int = 0,
) -> dict:
    ensure_card_schema()
    if not file_storage or not file_storage.filename:
        raise ValueError("Файл не вибрано")
    data = file_storage.read()
    return save_media_bytes(
        treatment_id,
        data,
        file_storage.filename,
        visit_id=visit_id,
        mime_hint=(file_storage.mimetype or ""),
        kind=kind,
        injury_case_id=injury_case_id,
    )

def list_media(treatment_id: int) -> list:
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM treatment_media
            WHERE treatment_id = ? AND {not_deleted_sql()}
            ORDER BY id DESC
            """,
            (int(treatment_id),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_all_media() -> list:
    """Усі медіа (для повторної синхронізації з Dropbox)."""
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM treatment_media ORDER BY treatment_id, id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_injury_cert_media() -> list[dict]:
    """Усі PDF довідок про обставини травми (не видалені)."""
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM treatment_media
            WHERE kind = ? AND {not_deleted_sql()}
            ORDER BY id DESC
            """,
            (MEDIA_KIND_INJURY_CERT,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_media(media_id: int) -> Optional[dict]:
    ensure_card_schema()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM treatment_media WHERE id = ?", (int(media_id),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def media_disk_path(media: dict) -> str:
    tid = int(media.get("treatment_id") or 0)
    folder = str(tid) if tid else f"injury_{int(media.get('injury_case_id') or 0)}"
    return os.path.join(
        _cfg.TREATMENT_MEDIA_DIR,
        folder,
        media["filename"],
    )


def set_media_dropbox_path(media_id: int, dropbox_path: str) -> bool:
    """Зберігає API-шлях Dropbox у рядок медіа (синхронізується на інші ПК)."""
    ensure_card_schema()
    path = _normalize_spaces(dropbox_path or "")
    if not path:
        return False
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT * FROM treatment_media WHERE id = ? AND {not_deleted_sql()}",
            (int(media_id),),
        ).fetchone()
        if not row:
            return False
        item = dict(row)
        if _normalize_spaces(item.get("dropbox_path") or "") == path:
            return True
        sid = item.get("sync_id") or new_sync_id()
        conn.execute(
            """
            UPDATE treatment_media
            SET dropbox_path = ?, sync_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (path, sid, utc_now(), int(media_id)),
        )
        touch_row(conn, "treatment_media", sid)
        enqueue_outbox(conn, "treatment_media", sid, "upsert")
        _commit(conn)
        return True
    finally:
        conn.close()


def _cache_media_bytes(local: str, data: bytes) -> Optional[str]:
    try:
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, "wb") as fh:
            fh.write(data)
        return local
    except OSError:
        return None


def _cache_media_copy(local: str, src: str) -> Optional[str]:
    if not src or not os.path.isfile(src):
        return None
    try:
        os.makedirs(os.path.dirname(local), exist_ok=True)
        if not os.path.isfile(local):
            import shutil

            shutil.copy2(src, local)
        return local if os.path.isfile(local) else src
    except OSError:
        return src if os.path.isfile(src) else None


def resolve_media_file_path(media: dict, treatment: Optional[dict] = None) -> Optional[str]:
    """
    Локальний кеш AppData → збережений dropbox_path → папка Dropbox → API.

    На інших ПК локальний treatment_id інший, тому шлях «назва (#id)» не збігається;
    шукаємо за dropbox_path / sync_id / ім’ям файлу під ПІБ.
    """
    local = media_disk_path(media)
    if os.path.isfile(local):
        return local

    tid = int(media.get("treatment_id") or 0)
    if treatment is None and tid:
        treatment = get_treatment(tid) or {}
    treatment = treatment or {}
    pib = treatment.get("pib") or "patient"
    title = treatment.get("title") or "treatment"
    t_sync = _normalize_spaces(treatment.get("sync_id") or "")
    original = media.get("original_name") or ""
    filename = media.get("filename") or ""
    stored_dbx = _normalize_spaces(media.get("dropbox_path") or "")

    try:
        from utils.dropbox_sync import (
            api_media_path_to_local,
            download_bytes_from_dropbox,
            find_dropbox_api_path_under_patient,
            find_media_under_patient,
            patient_media_dropbox_path,
            resolve_media_local_path,
        )
    except ImportError:
        return None

    candidates: list[str] = []
    if stored_dbx:
        candidates.append(stored_dbx)
        mapped = api_media_path_to_local(stored_dbx)
        if mapped and os.path.isfile(mapped):
            cached = _cache_media_copy(local, mapped)
            return cached or mapped

    # Старий і новий шаблон папки лікування
    if tid:
        path = resolve_media_local_path(
            pib=pib,
            treatment_title=title,
            treatment_id=tid,
            original_name=original,
            filename=filename,
            treatment_sync_id=t_sync,
        )
        if path and os.path.isfile(path):
            return _cache_media_copy(local, path) or path
        for name in (original, filename):
            if not name:
                continue
            candidates.append(
                patient_media_dropbox_path(
                    pib=pib,
                    treatment_title=title,
                    treatment_id=tid,
                    filename=name,
                    treatment_sync_id=t_sync,
                )
            )
            if t_sync:
                candidates.append(
                    patient_media_dropbox_path(
                        pib=pib,
                        treatment_title=title,
                        treatment_id=tid,
                        filename=name,
                        treatment_sync_id="",
                    )
                )

    scanned = find_media_under_patient(
        pib=pib, original_name=original, filename=filename
    )
    if scanned and os.path.isfile(scanned):
        return _cache_media_copy(local, scanned) or scanned

    try:
        api_found = find_dropbox_api_path_under_patient(
            pib=pib, original_name=original, filename=filename
        )
    except Exception:
        api_found = None
    if api_found:
        candidates.insert(0, api_found)

    # Dropbox API — коли файл лише в хмарі / online-only
    seen = set()
    for api_path in candidates:
        if not api_path or api_path in seen:
            continue
        seen.add(api_path)
        mapped = api_media_path_to_local(api_path)
        if mapped and os.path.isfile(mapped):
            return _cache_media_copy(local, mapped) or mapped
        try:
            blob = download_bytes_from_dropbox(api_path)
        except Exception:
            blob = None
        if blob:
            cached = _cache_media_bytes(local, blob)
            if cached:
                if not stored_dbx and media.get("id"):
                    try:
                        set_media_dropbox_path(int(media["id"]), api_path)
                    except Exception:
                        pass
                return cached
    return None


def delete_media(media_id: int) -> bool:
    media = get_media(media_id)
    if not media:
        return False
    path = media_disk_path(media)
    conn = _connect()
    try:
        sid = media.get("sync_id") or ""
        if sync_enabled():
            if not sid:
                sid = new_sync_id()
                conn.execute(
                    "UPDATE treatment_media SET sync_id = ? WHERE id = ?",
                    (sid, int(media_id)),
                )
            touch_row(conn, "treatment_media", sid, deleted=True)
            enqueue_outbox(conn, "treatment_media", sid, "delete")
            ok = True
        else:
            cur = conn.execute(
                "DELETE FROM treatment_media WHERE id = ?", (int(media_id),)
            )
            ok = cur.rowcount > 0
        _commit(conn)
    finally:
        conn.close()
    if ok and os.path.isfile(path) and not sync_enabled():
        try:
            os.remove(path)
        except OSError:
            pass
    return ok
