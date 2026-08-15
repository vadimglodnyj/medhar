"""
Картотека пацієнтів: patients → treatments → visits + restrictions + media.
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Optional

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from config import (
    DATA_DIR,
    OUTPATIENT_JOURNAL_DB,
    REMINDER_WITHIN_DAYS,
    TREATMENT_MEDIA_DIR,
    TREATMENT_MEDIA_MAX_BYTES,
)

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

_ALLOWED_MEDIA_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}


def _normalize_spaces(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(OUTPATIENT_JOURNAL_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def parse_ui_date(value: str) -> Optional[datetime]:
    raw = _normalize_spaces(value)
    if not raw:
        return None
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
    """Створює таблиці картотеки та колонку treatment_id у зверненнях."""
    own = conn is None
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
        ):
            if cols and col not in cols:
                conn.execute(f"ALTER TABLE outpatient_entries ADD COLUMN {col} {decl}")
                cols.add(col)

        pcols = {row[1] for row in conn.execute("PRAGMA table_info(patients)").fetchall()}
        for col, decl in _PATIENT_EXTRA_COLS:
            if pcols and col not in pcols:
                conn.execute(f"ALTER TABLE patients ADD COLUMN {col} {decl}")
                pcols.add(col)
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


def get_or_create_patient(pib: str, rank_unit: str = "") -> int:
    ensure_card_schema()
    pib_n = _normalize_spaces(pib)
    if not pib_n:
        raise ValueError("ПІБ порожній")
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
                    conn.commit()
                return pid
        cur = conn.execute(
            "INSERT INTO patients (pib, rank_unit) VALUES (?, ?)",
            (pib_n, _normalize_spaces(rank_unit)),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


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
                service_category = ?, enlistment_date = ?, komisariat = ?
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
                int(patient_id),
            ),
        )
        conn.commit()
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
    # Консультація / направлення — лише одна дата (дата звернення)
    if kind in ("consultation", "referral"):
        start = _normalize_spaces(date) or start
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
                return f"переведений{when} з {from_lpz_n} в {lpz_n}"
            return f"продовжив стаціонарне лікування{when}{in_lpz}"
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
) -> int:
    ensure_card_schema()
    title_n = _normalize_spaces(title) or "Лікування"
    start = started_on or today_ui()
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO treatments (patient_id, title, status, started_on, notes)
            VALUES (?, ?, 'active', ?, ?)
            """,
            (int(patient_id), title_n, start, _normalize_spaces(notes)),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def resolve_treatment_for_visit(
    *,
    pib: str,
    rank_unit: str,
    treatment_id: str = "",
    new_treatment_title: str = "",
    visit_date: str = "",
    diagnosis: str = "",
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
        pid = get_or_create_patient(pib, rank_unit)
        return create_treatment(pid, title, started_on=visit_date or today_ui())
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
            """
            SELECT t.*, p.pib AS patient_pib FROM treatments t
            JOIN patients p ON p.id = t.patient_id
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
    ensure_card_schema()
    q = _normalize_spaces(query).casefold()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM treatments t WHERE t.patient_id = p.id) AS treatments_count,
                   (SELECT COUNT(*) FROM treatments t
                    WHERE t.patient_id = p.id AND t.status = 'active') AS active_count
            FROM patients p
            ORDER BY p.pib COLLATE NOCASE
            """
        ).fetchall()
        out = [dict(r) for r in rows]
    finally:
        conn.close()
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
            """
            SELECT t.*, p.pib, p.rank_unit AS patient_rank_unit, p.id AS patient_id_ref,
                   p.rank AS patient_rank, p.position AS patient_position,
                   p.unit_short AS patient_unit_short, p.birth_date AS patient_birth_date,
                   p.phone AS patient_phone, p.ipn AS patient_ipn
            FROM treatments t
            JOIN patients p ON p.id = t.patient_id
            WHERE t.id = ?
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
            """
            SELECT t.*,
                   (SELECT COUNT(*) FROM outpatient_entries e
                    WHERE e.treatment_id = t.id) AS visits_count
            FROM treatments t
            WHERE t.patient_id = ?
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
    return items


def list_visits_for_treatment(treatment_id: int) -> list:
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM outpatient_entries
            WHERE treatment_id = ?
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
            "UPDATE treatments SET status = ?, closed_on = ? WHERE id = ?",
            (status, closed_on, int(treatment_id)),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


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
        cur = conn.execute(
            "DELETE FROM treatment_restrictions WHERE visit_id = ?",
            (int(visit_id),),
        )
        conn.commit()
        return int(cur.rowcount or 0)
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
        cur = conn.execute(
            """
            INSERT INTO treatment_restrictions
            (treatment_id, visit_id, kind, start_on, end_on, days, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(treatment_id),
                int(visit_id) if visit_id else None,
                kind,
                format_ui_date(start_dt),
                format_ui_date(end_dt),
                days_n,
                _normalize_spaces(note),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
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
        cur = conn.execute(
            "DELETE FROM treatment_restrictions WHERE id = ?",
            (int(restriction_id),),
        )
        conn.commit()
        return cur.rowcount > 0
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
    """Нагадування про закінчення періодів за leave_end звернень."""
    ensure_card_schema()
    within = int(within_days if within_days is not None else REMINDER_WITHIN_DAYS)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = today + timedelta(days=within)
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
            ORDER BY e.leave_end
            """
        ).fetchall()
    finally:
        conn.close()
    reminders = []
    for r in rows:
        end_dt = parse_ui_date(r["end_on"] or "")
        if not end_dt:
            continue
        end_day = end_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if end_day < today or end_day > horizon:
            continue
        left = (end_day.date() - today.date()).days
        kind = r["kind"] or ""
        reminders.append({
            **dict(r),
            "kind_label": RESTRICTION_KINDS.get(kind, kind or "Період"),
            "days_left": left,
            "days_left_label": (
                "сьогодні" if left == 0 else ("завтра" if left == 1 else f"через {left} дн.")
            ),
        })
    reminders.sort(key=lambda x: (x.get("days_left", 99), x.get("pib", "")))
    return reminders


def save_media_bytes(
    treatment_id: int,
    data: bytes,
    original_name: str,
    *,
    visit_id: Optional[int] = None,
    mime_hint: str = "",
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
    folder = os.path.join(TREATMENT_MEDIA_DIR, str(int(treatment_id)))
    os.makedirs(folder, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(folder, stored)
    with open(path, "wb") as fh:
        fh.write(data)
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO treatment_media
            (treatment_id, visit_id, filename, original_name, mime)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(treatment_id),
                int(visit_id) if visit_id else None,
                stored,
                original,
                mime,
            ),
        )
        conn.commit()
        mid = int(cur.lastrowid)
    finally:
        conn.close()
    return {
        "id": mid,
        "treatment_id": int(treatment_id),
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
    )


def list_media(treatment_id: int) -> list:
    ensure_card_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM treatment_media
            WHERE treatment_id = ?
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
    return os.path.join(
        TREATMENT_MEDIA_DIR,
        str(media["treatment_id"]),
        media["filename"],
    )


def delete_media(media_id: int) -> bool:
    media = get_media(media_id)
    if not media:
        return False
    path = media_disk_path(media)
    conn = _connect()
    try:
        cur = conn.execute(
            "DELETE FROM treatment_media WHERE id = ?", (int(media_id),)
        )
        conn.commit()
        ok = cur.rowcount > 0
    finally:
        conn.close()
    if ok and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return ok
