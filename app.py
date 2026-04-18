#!/usr/bin/env python3
"""
Головний Flask додаток для генерації медичних документів
"""

import os
import sys
import shutil
import tempfile
import threading
import json
import pandas as pd
from flask import Flask, render_template, request, send_file, make_response, flash, jsonify
from werkzeug.utils import secure_filename

from docxtpl import DocxTemplate
from docx import Document as DocxDocument
from docx.text.paragraph import Paragraph as DocxParagraph
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
import io
import re
import logging
from datetime import datetime

# Додаємо поточну папку до шляху
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Імпортуємо конфігурацію
from config import *

from utils.circumstances_parser import parse_circumstances
from utils.ukrainian_pib_genitive import (
    build_pib_rodovyi_for_document,
    format_nominative_pib_display,
    nominative_pib_to_genitive_line,
)

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Порожня папка даних — щоб користувач міг одразу покласти Excel
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    pass

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Глобальна змінна для кешування даних
treatments_cache = None
cache_timestamp = None
# Сигнатура файлів для інвалідації кешу після зміни Excel
treatments_cache_file_signature = None
_treatments_load_lock = threading.Lock()
treatments_load_in_progress = False
treatments_last_load_error = None
SERVICE_SIGNATORIES_FILE = os.path.join(DATA_DIR, "service_signatories.json")
VLK_SIGNATORIES_FILE = os.path.join(DATA_DIR, "vlk_signatories.json")
BASE_PERSONNEL_FILE = os.path.join(DATA_DIR, "base.xlsx")

def _excel_cell_str(value, default=""):
    """Текст з комірки Excel/pandas без 'nan' у рядку."""
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    if not s or s.lower() == "nan" or s == "<na>":
        return default
    return s


def load_service_signatories():
    """Повертає збережені поля підписантів для службової характеристики."""
    defaults = {
        "pidpysant_1_zvannya": "",
        "pidpysant_1_pib": "",
        "pidpysant_2_zvannya": "",
        "pidpysant_2_pib": "",
    }
    try:
        if not os.path.isfile(SERVICE_SIGNATORIES_FILE):
            return defaults
        with open(SERVICE_SIGNATORIES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return defaults
        for k in defaults.keys():
            defaults[k] = _normalize_spaces(raw.get(k, ""))
        return defaults
    except Exception as e:
        logger.warning("Не вдалося зчитати service_signatories.json: %s", e)
        return defaults


def save_service_signatories(data: dict):
    """Зберігає поля підписантів (атомарний запис)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "pidpysant_1_zvannya": _normalize_spaces(data.get("pidpysant_1_zvannya", "")),
        "pidpysant_1_pib": _normalize_spaces(data.get("pidpysant_1_pib", "")),
        "pidpysant_2_zvannya": _normalize_spaces(data.get("pidpysant_2_zvannya", "")),
        "pidpysant_2_pib": _normalize_spaces(data.get("pidpysant_2_pib", "")),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    tmp_path = SERVICE_SIGNATORIES_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, SERVICE_SIGNATORIES_FILE)


def load_vlk_signatories():
    """Повертає збережені поля підписантів для рапорту ВЛК."""
    defaults = {
        "tvo_kombrig_zvannya": "",
        "tvo_kombrig_pib": "",
        "tvo_kombat_zvannya": "",
        "tvo_kombat_pib": "",
    }
    try:
        if not os.path.isfile(VLK_SIGNATORIES_FILE):
            return defaults
        with open(VLK_SIGNATORIES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return defaults
        for k in defaults.keys():
            defaults[k] = _normalize_spaces(raw.get(k, ""))
        return defaults
    except Exception as e:
        logger.warning("Не вдалося зчитати vlk_signatories.json: %s", e)
        return defaults


def save_vlk_signatories(data: dict):
    """Зберігає поля підписантів ВЛК (атомарний запис)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "tvo_kombrig_zvannya": _normalize_spaces(data.get("tvo_kombrig_zvannya", "")),
        "tvo_kombrig_pib": _normalize_spaces(data.get("tvo_kombrig_pib", "")),
        "tvo_kombat_zvannya": _normalize_spaces(data.get("tvo_kombat_zvannya", "")),
        "tvo_kombat_pib": _normalize_spaces(data.get("tvo_kombat_pib", "")),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    tmp_path = VLK_SIGNATORIES_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, VLK_SIGNATORIES_FILE)


def _row_full_name(row) -> str:
    """ПІБ для відображення та пошуку: спочатку колонка ПІБ, інакше з трьох частин."""
    pib = _excel_cell_str(row.get("ПІБ"))
    if pib:
        return pib
    sur = _excel_cell_str(row.get("Прізвище"))
    first = _excel_cell_str(row.get("Ім'я"))
    pat = _excel_cell_str(row.get("По батькові"))
    return " ".join(p for p in (sur, first, pat) if p).strip()


def list_treatments_year_files_sorted():
    """Усі `treatments_YYYY.xlsx` у DATA_DIR, відсортовані за роком зростання."""
    if not os.path.isdir(DATA_DIR):
        return []
    found = []
    for name in os.listdir(DATA_DIR):
        m = TREATMENTS_YEAR_FILE_RE.match(name)
        if not m:
            continue
        year = int(m.group(1))
        path = os.path.join(DATA_DIR, name)
        if os.path.isfile(path):
            found.append((year, path))
    found.sort(key=lambda x: x[0])
    return found


def treatments_path_for_year(year: int) -> str:
    """Шлях до файлу бази за конкретний рік (для завантаження / відображення)."""
    return os.path.join(DATA_DIR, f"treatments_{int(year)}.xlsx")


def _treatments_excel_file_signature():
    """Час модифікації всіх релевантних Excel (для скидання кешу)."""
    paths = []
    if os.path.exists(TREATMENTS_FINAL_FILE):
        paths.append(TREATMENTS_FINAL_FILE)
    for _, p in list_treatments_year_files_sorted():
        paths.append(p)
    return tuple((p, os.path.getmtime(p)) for p in paths)


def _validate_treatments_upload_dataframe(df):
    """Перевірка структури завантаженого файлу перед заміною на диску."""
    if df is None or len(df) < 1:
        return False, "Файл порожній або не містить рядків даних"
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()
    has_triple = all(c in df.columns for c in ["Прізвище", "Ім'я", "По батькові"])
    has_pib = "ПІБ" in df.columns
    if not has_triple and not has_pib:
        return (
            False,
            "Потрібні колонки «Прізвище», «Ім'я», «По батькові» або колонка «ПІБ»",
        )
    return True, None


def _invalidate_treatments_cache_unlocked():
    """Скинути кеш після зміни файлів (викликати під _treatments_load_lock)."""
    global treatments_cache, cache_timestamp, treatments_cache_file_signature
    treatments_cache = None
    cache_timestamp = None
    treatments_cache_file_signature = None


def load_treatments_data():
    """Завантажує дані з Excel: опційний архів + 2024 + 2025 (об'єднано) + 2026 (поточний рік)."""
    global treatments_cache, cache_timestamp, treatments_cache_file_signature
    global treatments_load_in_progress, treatments_last_load_error

    sig = _treatments_excel_file_signature()
    now = datetime.now()

    if treatments_cache is not None and cache_timestamp is not None:
        cache_fresh = (now - cache_timestamp).total_seconds() < 300  # 5 хвилин
        sig_ok = treatments_cache_file_signature == sig
        if cache_fresh and sig_ok:
            return treatments_cache

    with _treatments_load_lock:
        sig = _treatments_excel_file_signature()
        now = datetime.now()
        if treatments_cache is not None and cache_timestamp is not None:
            cache_fresh = (now - cache_timestamp).total_seconds() < 300
            sig_ok = treatments_cache_file_signature == sig
            if cache_fresh and sig_ok:
                return treatments_cache

        treatments_load_in_progress = True
        treatments_last_load_error = None
        try:
            return _load_treatments_data_unlocked()
        except Exception as e:
            treatments_last_load_error = str(e)
            raise
        finally:
            treatments_load_in_progress = False


def _load_treatments_data_unlocked():
    """Внутрішнє завантаження Excel (викликати лише під _treatments_load_lock)."""
    global treatments_cache, cache_timestamp, treatments_cache_file_signature

    try:
        year_files = list_treatments_year_files_sorted()
        has_final = os.path.exists(TREATMENTS_FINAL_FILE)
        if not year_files and not has_final:
            raise FileNotFoundError(
                f"У папці {DATA_DIR!r} немає файлів treatments_YYYY.xlsx і немає treatments_final.xlsx"
            )

        logger.info(
            "Завантаження Excel: %s + %s",
            "treatments_final.xlsx" if has_final else "(без архіву)",
            ", ".join(f"treatments_{y}.xlsx" for y, _ in year_files) if year_files else "(немає річних файлів)",
        )

        frames = []

        if has_final:
            df_final = pd.read_excel(TREATMENTS_FINAL_FILE)
            df_final.columns = df_final.columns.str.strip()
            logger.info(f"Архів treatments_final.xlsx: {len(df_final)} записів")
            frames.append(df_final)

        for year, path in year_files:
            df_y = pd.read_excel(path)
            df_y.columns = df_y.columns.str.strip()
            logger.info(f"treatments_{year}.xlsx: {len(df_y)} записів")
            frames.append(df_y)

        if not frames:
            raise ValueError("Немає жодного кадру даних для об'єднання")

        treatments_df = pd.concat(frames, ignore_index=True)

        logger.info("Видаляємо дублікати (пріоритет у пізніших джерелів за роком файлу)...")
        initial_count = len(treatments_df)

        treatments_df['ПІБ_чисте'] = (
            treatments_df['Прізвище'].fillna('').astype(str) + ' ' +
            treatments_df['Ім\'я'].fillna('').astype(str) + ' ' +
            treatments_df['По батькові'].fillna('').astype(str)
        ).str.replace(r'\s+', ' ', regex=True).str.strip().str.lower()

        if 'Дата надходження в поточний Л/З' in treatments_df.columns:
            treatments_df['Дата надходження в поточний Л/З'] = pd.to_datetime(
                treatments_df['Дата надходження в поточний Л/З'], errors='coerce'
            )
            treatments_df = treatments_df.drop_duplicates(
                subset=['ПІБ_чисте', 'Дата надходження в поточний Л/З'],
                keep='last',
            )
        else:
            treatments_df = treatments_df.drop_duplicates(subset=['ПІБ_чисте'], keep='last')

        logger.info(f"Видалено дублікатів: {initial_count - len(treatments_df)}; записів після: {len(treatments_df)}")
        
        # Обробка дат
        date_columns = ['Дата надходження в поточний Л/З', 'Дата виписки', 'Дата народження', 'Дата первинної госпіталізації', 'Дата виписки з поточного Л/З']
        for col in date_columns:
            if col in treatments_df.columns:
                treatments_df[col] = pd.to_datetime(treatments_df[col], errors='coerce')
        
        # Обробка ПІБ (якщо ще не створено)
        for col in ['Прізвище', 'Ім\'я', 'По батькові']:
            if col in treatments_df.columns:
                treatments_df[col] = treatments_df[col].fillna('').astype(str)
                treatments_df[col] = treatments_df[col].replace(
                    to_replace=r'(?i)^(nan|<na>|none)$', value='', regex=True
                )

        # Завжди збираємо ПІБ з трьох полів, якщо вони є — так не залишаються порожні NaN з колонки «ПІБ» у Excel
        if all(c in treatments_df.columns for c in ['Прізвище', 'Ім\'я', 'По батькові']):
            treatments_df['ПІБ'] = (
                treatments_df['Прізвище'].astype(str).str.strip() + ' ' +
                treatments_df['Ім\'я'].astype(str).str.strip() + ' ' +
                treatments_df['По батькові'].astype(str).str.strip()
            ).str.replace(r'\s+', ' ', regex=True).str.strip()
        elif 'ПІБ' in treatments_df.columns:
            treatments_df['ПІБ'] = treatments_df['ПІБ'].map(lambda x: _excel_cell_str(x))

        # Після нормалізації ПІБ оновлюємо ключ пошуку
        treatments_df['ПІБ_чисте'] = treatments_df['ПІБ'].str.replace(r'\s+', ' ', regex=True).str.strip().str.lower()
        
        # Оновлюємо кеш (сигнатура файлів — зміни на диску скидають кеш)
        treatments_cache = treatments_df
        cache_timestamp = datetime.now()
        treatments_cache_file_signature = _treatments_excel_file_signature()

        logger.info(f"Дані успішно завантажено. Записів: {len(treatments_df)}")
        return treatments_df
        
    except Exception as e:
        logger.error(f"Помилка при завантаженні даних: {e}")
        raise

def format_rank_genitive(rank):
    """Конвертує звання в родовому відмінку для використання в шапці"""
    if not rank or not rank.strip():
        return ""
    
    rank_base, has_medical_service_suffix = split_medical_service_rank(rank)
    rank_lower = rank_base.lower().strip()
    
    # Словник для перетворення звань в родовому відмінку
    genitive_ranks = {
        'солдат': 'солдата',
        'старший солдат': 'старшого солдата',
        'молодший сержант': 'молодшого сержанта',
        'сержант': 'сержанта',
        'старший сержант': 'старшого сержанта',
        'головний сержант': 'головного сержанта',
        'штаб-сержант': 'штаб-сержанта',
        'майстер-сержант': 'майстер-сержанта',
        'старший майстер-сержант': 'старшого майстер-сержанта',
        'головний майстер-сержант': 'головного майстер-сержанта',
        'молодший лейтенант': 'молодшого лейтенанта',
        'лейтенант': 'лейтенанта',
        'старший лейтенант': 'старшого лейтенанта',
        'капітан': 'капітана',
        'майор': 'майора',
        'підполковник': 'підполковника',
        'полковник': 'полковника',
    }
    
    # Перевіряємо точне співпадіння
    if rank_lower in genitive_ranks:
        formatted_rank = genitive_ranks[rank_lower]
        return ensure_medical_service_rank(formatted_rank) if has_medical_service_suffix else formatted_rank
    
    # Якщо не знайдено точне співпадіння, повертаємо оригінал
    return ensure_medical_service_rank(rank_base) if has_medical_service_suffix else rank_base


def format_rank_dative(rank):
    """Конвертує звання в давальному відмінку для службової характеристики."""
    if not rank or not rank.strip():
        return ""

    rank_base, has_medical_service_suffix = split_medical_service_rank(rank)
    rank_lower = rank_base.lower().strip()
    dative_ranks = {
        'солдат': 'солдату',
        'старший солдат': 'старшому солдату',
        'молодший сержант': 'молодшому сержанту',
        'сержант': 'сержанту',
        'старший сержант': 'старшому сержанту',
        'головний сержант': 'головному сержанту',
        'штаб-сержант': 'штаб-сержанту',
        'майстер-сержант': 'майстер-сержанту',
        'старший майстер-сержант': 'старшому майстер-сержанту',
        'головний майстер-сержант': 'головному майстер-сержанту',
        'молодший лейтенант': 'молодшому лейтенанту',
        'лейтенант': 'лейтенанту',
        'старший лейтенант': 'старшому лейтенанту',
        'капітан': 'капітану',
        'майор': 'майору',
        'підполковник': 'підполковнику',
        'полковник': 'полковнику',
    }
    formatted_rank = dative_ranks.get(rank_lower, rank_base)
    return ensure_medical_service_rank(formatted_rank) if has_medical_service_suffix else formatted_rank


def split_medical_service_rank(rank):
    """Повертає базове звання та ознаку наявності приставки медичної служби."""
    normalized_rank = _normalize_spaces(rank)
    if not normalized_rank:
        return "", False

    has_medical_service_suffix = bool(re.search(r'\s+м[\\/]+с$', normalized_rank, flags=re.IGNORECASE))
    if not has_medical_service_suffix:
        return normalized_rank, False

    base_rank = re.sub(r'\s+м[\\/]+с$', '', normalized_rank, flags=re.IGNORECASE).strip()
    return base_rank, True


def ensure_medical_service_rank(rank):
    """Нормалізує звання з приставкою медичної служби до формату `м/с`."""
    base_rank, has_medical_service_suffix = split_medical_service_rank(rank)
    if not base_rank:
        return ""
    if has_medical_service_suffix:
        return f"{base_rank} м/с"
    return f"{base_rank} м/с"


def _normalize_spaces(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '')).strip()


def _ensure_final_period(text: str) -> str:
    t = _normalize_spaces(text).rstrip()
    if not t:
        return ""
    t = t.rstrip(' ,;:')
    if t.endswith('.'):
        return t
    return t + '.'


def _title_first_letter(text: str) -> str:
    t = _normalize_spaces(text)
    if not t:
        return ""
    return t[0].upper() + t[1:]


def _format_komisariat_and_date(raw_value: str) -> str:
    """
    Нормалізація рядка "комісаріат + дата":
    - обов'язково містить дату дд.мм.рррр;
    - додає кому перед датою;
    - завершує словом "року.".
    """
    value = _normalize_spaces(raw_value)
    m = re.search(r'(\d{2}\.\d{2}\.\d{4})', value)
    if not m:
        return ""

    date_value = m.group(1)
    if not validate_date_format(date_value):
        return ""

    before = _normalize_spaces(value[:m.start()])
    if not before:
        return ""
    before = before.rstrip(' ,.;:')
    return f"{before}, {date_value} року."


def _split_pib(full_name: str):
    parts = _normalize_spaces(full_name).split(' ')
    if len(parts) < 3:
        return "", "", ""
    return parts[0], parts[1], " ".join(parts[2:])


def _format_initials_name(prizvyshche: str, imya: str, po_batkovi: str) -> str:
    if not prizvyshche:
        return ""
    i = (imya[:1] + ".") if imya else ""
    p = (po_batkovi[:1] + ".") if po_batkovi else ""
    return _normalize_spaces(f"{prizvyshche} {i}{p}")


def _surname_first_capital(text: str) -> str:
    """Формат прізвища: лише перша літера велика."""
    t = _normalize_spaces(text)
    if not t:
        return ""
    t = t.lower()
    return t[:1].upper() + t[1:]


def _surname_to_dative(surname_nominative: str) -> str:
    """
    Наближене перетворення прізвища з називного у давальний відмінок.
    Використовується для службової характеристики (формат ініціалів).
    """
    s = _normalize_spaces(surname_nominative).lower()
    if not s:
        return ""

    # Глодний -> Глодному, Синій -> Синьому
    if s.endswith("ий"):
        return _surname_first_capital(s[:-2] + "ому")
    if s.endswith("ій"):
        return _surname_first_capital(s[:-2] + "ьому")

    # Ковальчук -> Ковальчуку, Шевченко -> Шевченку, Іванов -> Іванову
    if s.endswith("ко"):
        return _surname_first_capital(s + "у")
    if s.endswith(("ов", "ев", "єв", "ин", "ін", "їн", "ук", "юк", "чук", "чак", "ак", "як", "ич", "ець", "єць", "ань", "ень")):
        return _surname_first_capital(s + "у")

    # Для жіночих/м'яких форм на -а/-я -> -і
    if s.endswith("а"):
        return _surname_first_capital(s[:-1] + "і")
    if s.endswith("я"):
        return _surname_first_capital(s[:-1] + "і")

    # Базовий варіант для більшості чоловічих прізвищ на приголосний
    return _surname_first_capital(s + "у")


def _first_name_to_dative(name_nominative: str) -> str:
    """Наближене перетворення імені у давальний відмінок."""
    n = _normalize_spaces(name_nominative).lower()
    if not n:
        return ""
    if n.endswith("ій"):
        return _surname_first_capital(n[:-2] + "ію")
    if n.endswith("й"):
        return _surname_first_capital(n[:-1] + "ю")
    if n.endswith("а"):
        return _surname_first_capital(n[:-1] + "і")
    if n.endswith("я"):
        return _surname_first_capital(n[:-1] + "і")
    if n.endswith("о"):
        return _surname_first_capital(n[:-1] + "у")
    if n.endswith("р"):
        return _surname_first_capital(n + "у")
    return _surname_first_capital(n + "у")


def _pib_to_dative(full_name: str) -> str:
    """Наближене перетворення ПІБ у давальний відмінок."""
    parts = _normalize_spaces(full_name).split(" ")
    if not parts:
        return ""
    if len(parts) == 1:
        return _surname_to_dative(parts[0])
    if len(parts) == 2:
        first, surname = parts
        return _normalize_spaces(f"{_first_name_to_dative(first)} {_surname_to_dative(surname)}")
    first, surname, patronymic = parts[0], parts[1], " ".join(parts[2:])
    pat = patronymic
    if patronymic.lower().endswith("ич"):
        pat = patronymic + "у"
    elif patronymic.lower().endswith("на"):
        pat = patronymic[:-1] + "і"
    return _normalize_spaces(f"{_first_name_to_dative(first)} {_surname_to_dative(surname)} {pat}")


def _uppercase_last_word(full_name: str) -> str:
    """Робить останнє слово (прізвище) у ПІБ ВЕЛИКИМИ літерами."""
    parts = _normalize_spaces(full_name).split(" ")
    if not parts:
        return ""
    parts[-1] = parts[-1].upper()
    return _normalize_spaces(" ".join(parts))


def _split_rank_and_name(full_value: str):
    """Розбиває рядок виду 'підполковник Олександр РЯСНИЙ' на (звання, ПІБ)."""
    text = _normalize_spaces(full_value)
    if not text:
        return "", ""
    known_ranks = [
        "головний майстер-сержант",
        "старший майстер-сержант",
        "майстер-сержант",
        "головний сержант",
        "старший сержант",
        "молодший сержант",
        "старший лейтенант",
        "молодший лейтенант",
        "старший солдат",
        "штаб-сержант",
        "підполковник",
        "полковник",
        "лейтенант",
        "капітан",
        "сержант",
        "солдат",
        "майор",
    ]
    lower = text.lower()
    for rank in known_ranks:
        if lower.startswith(rank + " "):
            return text[:len(rank)], text[len(rank):].strip()
    parts = text.split(" ", 1)
    return (parts[0], parts[1]) if len(parts) > 1 else (parts[0], "")


def _rank_short(rank_value: str) -> str:
    """Скорочення звання для блоку НМС."""
    rank_base, _ = split_medical_service_rank(rank_value)
    short_map = {
        "полковник": "п-к",
        "підполковник": "п/п-к",
        "майор": "м-р",
        "капітан": "к-н",
        "старший лейтенант": "ст. л-т",
        "лейтенант": "л-т",
        "молодший лейтенант": "мол. л-т",
        "старший сержант": "ст. с-т",
        "сержант": "с-т",
        "молодший сержант": "мол. с-т",
        "солдат": "с-т",
    }
    return short_map.get(rank_base.lower().strip(), rank_base)


def _pib_short_with_initials(full_name: str) -> str:
    """Формат 'О. РЯСНИЙ' з повного імені/ПІБ."""
    parts = _normalize_spaces(full_name).split(" ")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0].upper()
    first_name = parts[0]
    surname = parts[-1].upper()
    return _normalize_spaces(f"{first_name[:1].upper()}. {surname}")


def _extract_position_from_row(row) -> str:
    for col in (
        "Займана посада",
        "Посада",
        "Військова посада",
        "Штатна посада",
    ):
        val = _excel_cell_str(row.get(col))
        if val:
            return val
    return ""


def _to_genitive_unit_phrase(phrase: str) -> str:
    """
    Груба нормалізація назви підрозділу в родовий відмінок для шаблонного тексту посади.
    Приклади:
    - "1-ий взвод оперативного призначення" -> "1-го взводу оперативного призначення"
    - "Розвідувальний взвод спеціального призначення" -> "розвідувального взводу спеціального призначення"
    """
    text = _normalize_spaces(phrase)
    if not text:
        return ""

    words = text.split(" ")
    adjective_idx = 0
    noun_idx = 1 if len(words) > 1 else 0

    # 1-ий / 1-ша / 1-ше -> 1-го / 1-ої
    ordinal_re = re.compile(r'^(\d+)\s*-\s*([а-яіїєґa-z]+)$', flags=re.IGNORECASE)
    m = ordinal_re.match(words[0]) if words else None
    if m:
        # Варіанти:
        # - "1-ий взвод ..." (числівник + іменник)
        # - "1-ше розвідувальне відділення ..." (числівник + прикметник + іменник)
        second_lower = words[1].lower() if len(words) > 1 else ""
        second_looks_like_adj = second_lower.endswith(("ий", "ій", "е", "є"))
        if len(words) > 2 and second_looks_like_adj:
            adjective_idx = 1
            noun_idx = 2
        else:
            adjective_idx = -1
            noun_idx = 1 if len(words) > 1 else 0

        noun_for_gender = words[noun_idx].lower() if len(words) > noun_idx else ""
        number = m.group(1)
        fem_nouns = ("рота", "група", "команда", "батарея")
        ordinal_suffix = "ої" if noun_for_gender.startswith(fem_nouns) else "го"
        words[0] = f"{number}-{ordinal_suffix}"

    # Нормалізація прикметника
    if 0 <= adjective_idx < len(words):
        adj_lower = words[adjective_idx].lower()
        if adj_lower.endswith("ий"):
            words[adjective_idx] = words[adjective_idx][:-2] + "ого"
        elif adj_lower.endswith("ій"):
            words[adjective_idx] = words[adjective_idx][:-2] + "ього"
        elif adj_lower.endswith("а"):
            words[adjective_idx] = words[adjective_idx][:-1] + "ої"
        elif adj_lower.endswith("я"):
            words[adjective_idx] = words[adjective_idx][:-1] + "ьої"
        elif adj_lower.endswith("е"):
            words[adjective_idx] = words[adjective_idx][:-1] + "ого"
        elif adj_lower.endswith("є"):
            words[adjective_idx] = words[adjective_idx][:-1] + "ього"

    # Нормалізація іменника (друге слово у фразі)
    if 0 <= noun_idx < len(words):
        noun = words[noun_idx]
        noun_map = {
            "взвод": "взводу",
            "батальйон": "батальйону",
            "полк": "полку",
            "дивізіон": "дивізіону",
            "пункт": "пункту",
            "відділення": "відділення",
            "рота": "роти",
            "група": "групи",
            "команда": "команди",
            "служба": "служби",
        }
        noun_lower = noun.lower()
        words[noun_idx] = noun_map.get(noun_lower, noun)

    out = " ".join(words)
    return out[:1].lower() + out[1:] if out else ""


def _extract_position_from_base_row(row) -> str:
    """
    Формує шаблонний текст посади з base.xlsx:
    I (Посада) + F (Підрозділ 4) + E (Підрозділ 3) + D (Підрозділ 2, якщо є).
    """
    try:
        posada_i = _excel_cell_str(row.iloc[8])
        pidrozdil_f = _excel_cell_str(row.iloc[5])
        pidrozdil_e = _excel_cell_str(row.iloc[4])
        pidrozdil_d = _excel_cell_str(row.iloc[3])
    except Exception:
        return ""

    if not posada_i:
        return ""

    chunks = [posada_i]
    for part in (pidrozdil_f, pidrozdil_e, pidrozdil_d):
        norm = _to_genitive_unit_phrase(part)
        if norm:
            chunks.append(norm)
    return _normalize_spaces(" ".join(chunks))


def _extract_rank_from_base_row(row) -> str:
    """Звання з base.xlsx (пріоритет: колонка L, далі M)."""
    try:
        rank_l = _excel_cell_str(row.iloc[11])
        rank_m = _excel_cell_str(row.iloc[12])
    except Exception:
        return ""
    return rank_l or rank_m


def _lookup_person_in_base_excel(pib_nazivnyi: str) -> dict:
    """
    Пошук персональних даних за ПІБ у data/base.xlsx.
    Повертає словник: zvanie, zaimana_posada, prizvyshche, imya, po_batkovi.
    """
    empty = {
        "zvanie": "",
        "zaimana_posada": "",
        "prizvyshche": "",
        "imya": "",
        "po_batkovi": "",
    }
    if not pib_nazivnyi or not os.path.isfile(BASE_PERSONNEL_FILE):
        return empty
    try:
        base_df = pd.read_excel(BASE_PERSONNEL_FILE)
    except Exception as e:
        logger.warning("Не вдалося прочитати base.xlsx: %s", e)
        return empty

    if len(base_df.columns) < 16:
        return empty

    target_clean = re.sub(r'\s+', ' ', pib_nazivnyi).strip().lower()
    if not target_clean:
        return empty

    for _, row in base_df.iterrows():
        sur = _excel_cell_str(row.iloc[13])
        first = _excel_cell_str(row.iloc[14])
        pat = _excel_cell_str(row.iloc[15])
        row_pib = _normalize_spaces(f"{sur} {first} {pat}").lower()
        if row_pib and row_pib == target_clean:
            return {
                "zvanie": _extract_rank_from_base_row(row),
                "zaimana_posada": _extract_position_from_base_row(row),
                "prizvyshche": sur,
                "imya": first,
                "po_batkovi": pat,
            }
    return empty


def _lookup_position_in_base_excel(pib_nazivnyi: str) -> str:
    """Пошук посади за ПІБ у data/base.xlsx."""
    return _lookup_person_in_base_excel(pib_nazivnyi).get("zaimana_posada", "")


def _iter_doc_paragraphs_for_replace(doc: DocxDocument):
    """Ітерує всі абзаци документа, включно з таблицями."""
    for p in doc.paragraphs:
        yield p
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield p


def _extract_vlk_highlight_samples(template_path: str):
    """
    Повертає список жовтих фрагментів з шаблону у порядку згори-вниз.
    Кожна послідовність підсвічених run-ів вважається одним динамічним полем.
    """
    if not os.path.isfile(template_path):
        return []
    doc = DocxDocument(template_path)
    samples = []
    for p in _iter_doc_paragraphs_for_replace(doc):
        seq = []
        for run in p.runs:
            is_yellow = run.font.highlight_color == WD_COLOR_INDEX.YELLOW
            if is_yellow:
                seq.append(run.text or "")
            elif seq:
                text = _normalize_spaces("".join(seq))
                if text:
                    samples.append(text)
                seq = []
        if seq:
            text = _normalize_spaces("".join(seq))
            if text:
                samples.append(text)
    return samples


def _replace_vlk_highlights(doc: DocxDocument, values):
    """
    Замінює жовті фрагменти у документі значеннями з `values` за порядком.
    """
    idx = 0
    for p in _iter_doc_paragraphs_for_replace(doc):
        seq_runs = []
        for run in p.runs:
            is_yellow = run.font.highlight_color == WD_COLOR_INDEX.YELLOW
            if is_yellow:
                seq_runs.append(run)
                continue
            if seq_runs:
                replacement = values[idx] if idx < len(values) else ""
                seq_runs[0].text = replacement
                for extra in seq_runs[1:]:
                    extra.text = ""
                idx += 1
                seq_runs = []
        if seq_runs:
            replacement = values[idx] if idx < len(values) else ""
            seq_runs[0].text = replacement
            for extra in seq_runs[1:]:
                extra.text = ""
            idx += 1

def validate_date_format(date_string):
    """Валідує формат дати дд.мм.рррр"""
    if not date_string:
        return True
    
    pattern = r'^\d{2}\.\d{2}\.\d{4}$'
    if not re.match(pattern, date_string):
        return False
    
    try:
        day, month, year = map(int, date_string.split('.'))
        if month < 1 or month > 12 or day < 1 or day > 31:
            return False
        if year < 1900 or year > datetime.now().year:
            return False
        # Перевіряємо чи дата існує
        datetime(year, month, day)
        return True
    except ValueError:
        return False

def format_treatment_history(person_treatments, hide_diagnosis):
    """Форматує історію лікування"""
    if person_treatments.empty:
        default_line = "За час проходження військової служби не знаходився на стаціонарному або амбулаторному лікуванні у закладах Міністерства охорони здоров'я України та медичних територіальних об'єднань Міністерства внутрішніх справ України."
        return ["\t" + default_line]

    history_lines = []
    # Хронологічне сортування з явним парсингом дат (dayfirst) і NaT в кінець
    df_sorted = person_treatments.reset_index().rename(columns={'index': 'orig_idx'}).copy()
    df_sorted['__start_dt'] = pd.to_datetime(df_sorted['Дата надходження в поточний Л/З'], errors='coerce', dayfirst=True)
    df_sorted['__end_dt'] = pd.to_datetime(df_sorted['Дата виписки'], errors='coerce', dayfirst=True)

    # Пріоритет типів лікування для впорядкування подій одного дня
    priority_map = {
        'стабілізаційний пункт': 0,
        'стаціонар': 1,
        'стаціонарне': 1,
        'денний стаціонар': 2,
        'амбулаторно': 3,
        'амбулаторне': 3,
        'реабілітація': 4,
        'влк': 5,
        'відпустка': 6,
    }
    df_sorted['__type_prio'] = df_sorted['Вид лікування'].astype(str).str.lower().map(priority_map).fillna(9).astype(int)

    df_sorted = df_sorted.sort_values(
        by=['__start_dt', '__type_prio', '__end_dt', 'orig_idx'],
        kind='mergesort',
        na_position='last'
    )
    try:
        logger.info("Порядок лікувань після сортування (перші 50):")
        for i, rec in enumerate(df_sorted.head(50).to_dict('records')):
            logger.info(
                f"{i+1}) start_raw={rec.get('Дата надходження в поточний Л/З')} | "
                f"end_raw={rec.get('Дата виписки')} | "
                f"start_dt={rec.get('__start_dt')} | end_dt={rec.get('__end_dt')} | "
                f"type={rec.get('Вид лікування')} | prio={rec.get('__type_prio')} | place={rec.get('Місце госпіталізації')}"
            )
    except Exception as _e:
        logger.warning(f"Не вдалося вивести діагностичний порядок сортування: {_e}")

    for index, row in df_sorted.iterrows():
        start_date_obj = row['Дата надходження в поточний Л/З']
        end_date_obj = row['Дата виписки']
        
        # Безпечно обробляємо дати
        try:
            start_date = start_date_obj.strftime('%d.%m.%Y') if pd.notna(start_date_obj) else "[дата не вказана]"
        except:
            start_date = "[дата не вказана]"
            
        try:
            end_date = end_date_obj.strftime('%d.%m.%Y') if pd.notna(end_date_obj) else "по теперішній час"
        except:
            end_date = "по теперішній час"

        treatment_type = str(row.get('Вид лікування', '')).lower()
        place = row['Місце госпіталізації']
        diagnosis = row['Попередній діагноз']
        vlk_conclusion = row['Заключення ВЛК']
        line = ""

        if treatment_type == 'стабілізаційний пункт':
            circumstances_text = row.get('Обставини отримання поранення/ травмування', '') or ''
            info = parse_circumstances(circumstances_text)
            injury_date = info.get('injury_date') or start_date
            location = info.get('location')
            factor = info.get('factor')

            # Формуємо потрібну конструкцію
            part1 = f"{injury_date} під час виконання бойового завдання"
            if location:
                part1 += f" в районі н. п. {location}"
            if factor:
                part1 += f" отримав поранення внаслідок {factor}"
            else:
                part1 += " отримав поранення"

            part2 = f"{start_date} евакуйований для надання першої медичної допомоги в {place}"

            base_text = f"{part1}. {part2}."
            if hide_diagnosis:
                line = base_text
            else:
                # Додаємо крапку після діагнозу, якщо її немає
                diagnosis_text = diagnosis.strip() if diagnosis else ""
                if diagnosis_text and not diagnosis_text.endswith('.'):
                    diagnosis_text += "."
                line = base_text + f" Діагноз: {diagnosis_text}"
        
        elif treatment_type in ['стаціонар', 'стаціонарне']:
            base_text = f"З {start_date} по {end_date} перебував на стаціонарному лікуванні в {place}"
            if hide_diagnosis:
                line = base_text + "."
            else:
                # Додаємо крапку після діагнозу, якщо її немає
                diagnosis_text = diagnosis.strip() if diagnosis else ""
                if diagnosis_text and not diagnosis_text.endswith('.'):
                    diagnosis_text += "."
                line = base_text + f". Діагноз: {diagnosis_text}"
        
        elif treatment_type in ['амбулаторно', 'амбулаторне']:
            base_text = f"З {start_date} по {end_date} перебував на амбулаторному лікуванні в {place}"
            if hide_diagnosis:
                line = base_text + "."
            else:
                # Додаємо крапку після діагнозу, якщо її немає
                diagnosis_text = diagnosis.strip() if diagnosis else ""
                if diagnosis_text and not diagnosis_text.endswith('.'):
                    diagnosis_text += "."
                line = base_text + f". Діагноз: {diagnosis_text}"

        elif treatment_type == 'реабілітація':
            base_text = f"З {start_date} по {end_date} проходив реабілітаційне лікування в {place}"
            if hide_diagnosis:
                line = base_text + "."
            else:
                # Додаємо крапку після діагнозу, якщо її немає
                diagnosis_text = diagnosis.strip() if diagnosis else ""
                if diagnosis_text and not diagnosis_text.endswith('.'):
                    diagnosis_text += "."
                line = base_text + f". Діагноз: {diagnosis_text}"

        elif treatment_type == 'денний стаціонар':
            base_text = f"З {start_date} по {end_date} перебував на денному стаціонарі в {place}"
            if hide_diagnosis:
                line = base_text + "."
            else:
                # Додаємо крапку після діагнозу, якщо її немає
                diagnosis_text = diagnosis.strip() if diagnosis else ""
                if diagnosis_text and not diagnosis_text.endswith('.'):
                    diagnosis_text += "."
                line = base_text + f". Діагноз: {diagnosis_text}"

        elif treatment_type == 'лазарет':
            base_text = f"З {start_date} по {end_date} перебував на лікуванні в лазареті {place}"
            if hide_diagnosis:
                line = base_text + "."
            else:
                # Додаємо крапку після діагнозу, якщо її немає
                diagnosis_text = diagnosis.strip() if diagnosis else ""
                if diagnosis_text and not diagnosis_text.endswith('.'):
                    diagnosis_text += "."
                line = base_text + f". Діагноз: {diagnosis_text}"

        elif treatment_type == 'лікування за кордоном':
            base_text = f"З {start_date} по {end_date} проходив лікування за кордоном в {place}"
            if hide_diagnosis:
                line = base_text + "."
            else:
                # Додаємо крапку після діагнозу, якщо її немає
                diagnosis_text = diagnosis.strip() if diagnosis else ""
                if diagnosis_text and not diagnosis_text.endswith('.'):
                    diagnosis_text += "."
                line = base_text + f". Діагноз: {diagnosis_text}"
            
        elif treatment_type == 'влк':
            line = f"З {start_date} по {end_date} проходив військово-лікарську комісію (ВЛК) в {place}."
            
        elif treatment_type == 'відпустка':
            conclusion = vlk_conclusion if pd.notna(vlk_conclusion) and vlk_conclusion else "перебував у відпустці за станом здоров'я за рішенням ВЛК"
            line = f"З {start_date} по {end_date} {conclusion}."
        
        if line: 
            history_lines.append(line)

    if not history_lines:
        default_line = "За час проходження військової служби не знаходився на стаціонарному або амбулаторному лікуванні у закладах Міністерства охорони здоров'я України та медичних територіальних об'єднань Міністерства внутрішніх справ України."
        return ["\t" + default_line]
    
    # Повертаємо список рядків; таб додаємо на початку для візуального відступу
    return ["\t" + line for line in history_lines]

@app.route('/api/treatments_ready', methods=['GET'])
def treatments_ready():
    """Статус прогріву кешу Excel (для прелоадера на сторінці форми)."""
    return jsonify({
        'ready': treatments_cache is not None,
        'loading': treatments_load_in_progress and treatments_cache is None,
        'error': treatments_last_load_error,
    })


@app.route('/api/treatments_sources', methods=['GET'])
def treatments_sources():
    """Список підключених файлів treatments_YYYY.xlsx (без повних шляхів на диску)."""
    items = []
    for year, path in list_treatments_year_files_sorted():
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        items.append({
            'year': year,
            'filename': os.path.basename(path),
            'mtime': mtime,
        })
    cy = datetime.now().year
    dest = treatments_path_for_year(cy)
    return jsonify({
        'year_files': items,
        'has_final': os.path.isfile(TREATMENTS_FINAL_FILE),
        'calendar_year': cy,
        'current_year_target': cy,
        'current_year_filename': os.path.basename(dest),
        'current_year_exists': os.path.isfile(dest),
    })


@app.route('/api/treatments_upload', methods=['POST'])
def treatments_upload():
    """
    Заміна / створення data/treatments_YYYY.xlsx: перевірка, атомарний запис, скидання кешу.
    Старий файл того ж року перезаписується.
    """
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'Файл не передано (поле file)'}), 400
    up = request.files['file']
    if not up or not up.filename:
        return jsonify({'ok': False, 'error': 'Оберіть файл .xlsx'}), 400
    if not up.filename.lower().endswith('.xlsx'):
        return jsonify({'ok': False, 'error': 'Допускається лише розширення .xlsx'}), 400

    year = request.form.get('year', type=int)
    if year is None:
        year = datetime.now().year
    if year < 1990 or year > 2100:
        return jsonify({'ok': False, 'error': 'Некоректний рік (1990–2100)'}), 400

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.xlsx', dir=TEMP_DIR)
        os.close(fd)
        up.save(tmp_path)
        size = os.path.getsize(tmp_path)
        if size > TREATMENTS_UPLOAD_MAX_BYTES:
            return (
                jsonify({
                    'ok': False,
                    'error': f'Файл завеликий (макс. {TREATMENTS_UPLOAD_MAX_BYTES // (1024 * 1024)} МБ)',
                }),
                413,
            )
        try:
            df = pd.read_excel(tmp_path)
        except Exception as e:
            return jsonify({'ok': False, 'error': f'Не вдалося прочитати Excel: {e}'}), 400
        ok, err_msg = _validate_treatments_upload_dataframe(df)
        if not ok:
            return jsonify({'ok': False, 'error': err_msg}), 400

        dest = treatments_path_for_year(year)
        staging = os.path.join(DATA_DIR, f'.treatments_{year}.upload.tmp.xlsx')
        with _treatments_load_lock:
            shutil.copyfile(tmp_path, staging)
            os.replace(staging, dest)
            _invalidate_treatments_cache_unlocked()
        logger.info(
            "Оновлено %s з файлу %s (%s рядків)",
            os.path.basename(dest),
            secure_filename(up.filename),
            len(df),
        )
        return jsonify({
            'ok': True,
            'year': year,
            'filename': os.path.basename(dest),
            'rows': int(len(df)),
        })
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@app.route('/api/stats', methods=['GET'])
def get_stats():
    """API endpoint для отримання статистики бази даних"""
    try:
        treatments_df = load_treatments_data()
        stats = {
            "total_records": int(len(treatments_df)),
            "unique_patients": int(treatments_df["ПІБ_чисте"].nunique()) if "ПІБ_чисте" in treatments_df.columns else 0,
        }
        return jsonify(stats)
        
    except Exception as e:
        logger.error(f"Помилка при отриманні статистики: {e}")
        return jsonify({'error': str(e)})

@app.route('/api/pib_genitive', methods=['GET'])
def api_pib_genitive():
    """Автоматичний родовий відмінок з називного (для підказки у формі)."""
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'genitive': ''})
    try:
        gen = nominative_pib_to_genitive_line(q)
        return jsonify({'genitive': gen or ''})
    except Exception as e:
        logger.warning("api_pib_genitive: %s", e)
        return jsonify({'genitive': '', 'error': str(e)})


@app.route('/api/service_signatories', methods=['GET', 'POST'])
def api_service_signatories():
    """Зчитування/збереження підписантів службової характеристики."""
    if request.method == 'GET':
        return jsonify(load_service_signatories())

    data = request.get_json(silent=True) or {}
    required = (
        "pidpysant_1_zvannya",
        "pidpysant_1_pib",
        "pidpysant_2_zvannya",
        "pidpysant_2_pib",
    )
    payload = {k: _normalize_spaces(data.get(k, "")) for k in required}
    if not all(payload.values()):
        return jsonify({
            'ok': False,
            'error': "Заповніть усі поля підписантів перед збереженням",
        }), 400
    try:
        save_service_signatories(payload)
        return jsonify({'ok': True, **payload})
    except Exception as e:
        logger.error("Помилка збереження підписантів: %s", e)
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/vlk_signatories', methods=['GET', 'POST'])
def api_vlk_signatories():
    """Зчитування/збереження підписантів для рапорту ВЛК."""
    if request.method == 'GET':
        return jsonify(load_vlk_signatories())

    data = request.get_json(silent=True) or {}
    required = (
        "tvo_kombrig_zvannya",
        "tvo_kombrig_pib",
        "tvo_kombat_zvannya",
        "tvo_kombat_pib",
    )
    payload = {k: _normalize_spaces(data.get(k, "")) for k in required}
    if not all(payload.values()):
        return jsonify({
            'ok': False,
            'error': "Заповніть усі поля підписантів перед збереженням",
        }), 400
    try:
        save_vlk_signatories(payload)
        return jsonify({'ok': True, **payload})
    except Exception as e:
        logger.error("Помилка збереження VLK-підписантів: %s", e)
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/search_pib', methods=['GET'])
def search_pib():
    """API endpoint для пошуку ПІБ"""
    try:
        query = request.args.get('q', '').strip()
        context_mode = request.args.get('context', '').strip().lower()
        if len(query) < 2:
            return jsonify({'results': []})

        treatments_df = load_treatments_data()
        if "ПІБ_чисте" not in treatments_df.columns or "ПІБ" not in treatments_df.columns:
            return jsonify({'results': []})

        q_clean = re.sub(r"\s+", " ", query).strip().lower()
        matched = treatments_df[treatments_df["ПІБ_чисте"].str.contains(q_clean, na=False)]
        if matched.empty:
            return jsonify({'results': []})

        # Беремо по одному запису на кожного пацієнта для автокомпліту
        unique_patients = matched.drop_duplicates(subset=["ПІБ_чисте"], keep="first").head(10)
        results = []
        for _, row in unique_patients.iterrows():
            full_name = _row_full_name(row)
            if not full_name:
                continue
            rank = _excel_cell_str(row.get("Військове звання"))
            position = _extract_position_from_row(row)
            birth_date = row.get("Дата народження")
            sur = _excel_cell_str(row.get("Прізвище"))
            first = _excel_cell_str(row.get("Ім'я"))
            pat = _excel_cell_str(row.get("По батькові"))
            if not (sur and first and pat):
                sur, first, pat = _split_pib(full_name)

            # Для службової характеристики підтягуємо ті ж пріоритети, що і під час генерації.
            if context_mode == "service":
                base_person = _lookup_person_in_base_excel(full_name)
                rank = base_person.get("zvanie") or rank
                position = base_person.get("zaimana_posada") or position
                sur = base_person.get("prizvyshche") or sur
                first = base_person.get("imya") or first
                pat = base_person.get("po_batkovi") or pat

            birth_date_str = ""
            try:
                if pd.notna(birth_date):
                    birth_date_str = birth_date.strftime("%d.%m.%Y") if hasattr(birth_date, "strftime") else _excel_cell_str(birth_date)
            except Exception:
                birth_date_str = ""

            label = full_name
            if rank:
                label = f"{full_name} ({rank})"

            results.append({
                "label": label,
                "value": full_name,
                "rank": rank,
                "position": position,
                "prizvyshche": sur,
                "imya": first,
                "po_batkovi": pat,
                "birth_date": birth_date_str,
            })

        return jsonify({'results': results})
        
    except Exception as e:
        logger.error(f"Помилка при пошуку ПІБ: {e}")
        return jsonify({'results': [], 'error': str(e)})

def _welcome_template_context():
    """Контекст вітальної сторінки: чи є Excel у data/."""
    has_files = bool(list_treatments_year_files_sorted()) or os.path.isfile(
        TREATMENTS_FINAL_FILE
    )
    return {
        "data_ready": has_files,
        "data_dir": os.path.abspath(DATA_DIR),
    }


@app.route('/', methods=['GET'])
def index():
    """Вітальна сторінка з інструкцією щодо data/ та встановлення."""
    return render_template('welcome.html', **_welcome_template_context())

@app.route('/medical-characteristic', methods=['GET', 'POST'])
def medical_characteristic():
    """Генерація медичної характеристики"""
    if request.method == 'POST':
        pib_nazivnyi = request.form.get('pib_nazivnyi', '').strip()
        pib_rodovyi_input = request.form.get('pib_rodovyi', '').strip()
        hide_diagnosis_flag = request.form.get('no_diagnosis')
        enlistment_date = request.form.get('enlistment_date', '').strip()
        enlistment_date_custom = request.form.get('enlistment_date_custom', '').strip()
        observation_end = request.form.get('observation_end', '').strip()
        observation_end_custom = request.form.get('observation_end_custom', '').strip()
        signatory = request.form.get('signatory', '').strip()
        birth_date = request.form.get('birth_date', '').strip()
        
        # Обробка дати призову
        if enlistment_date == "custom":
            if not enlistment_date_custom:
                flash("Вкажіть конкретну дату зарахування", "error")
                return render_template('medical_characteristic.html')
            if not validate_date_format(enlistment_date_custom):
                flash("Невірний формат дати зарахування. Використовуйте формат дд.мм.рррр", "error")
                return render_template('medical_characteristic.html')
            final_enlistment_date = f"з {enlistment_date_custom} року"
        elif enlistment_date == "з моменту призову":
            final_enlistment_date = "з моменту призову"
        else:
            # Для конкретних дат додаємо префікс "з" і слово "року"
            final_enlistment_date = f"з {enlistment_date} року"

        # Обробка дати завершення нагляду
        if observation_end == "custom":
            if not observation_end_custom:
                flash("Вкажіть конкретну дату завершення нагляду", "error")
                return render_template('medical_characteristic.html')
            if not validate_date_format(observation_end_custom):
                flash("Невірний формат дати завершення нагляду. Використовуйте формат дд.мм.рррр", "error")
                return render_template('medical_characteristic.html')
            final_observation_end = f"по {observation_end_custom} року"
        elif observation_end == "по теперішній час":
            final_observation_end = "по теперішній час"
        else:
            final_observation_end = ""

        # Обробка підписанта (розділяємо на окремі поля для правильного вирівнювання в Word)
        if signatory == "acting_chief":
            signatory_position = "Т. в. о. начальника медичної служби"
            signatory_department = "секції тилу"
            signatory_rank = "капітан м/с"
            signatory_name = "Ірина ГОНЧАРОВА"
        elif signatory == "chief":
            signatory_position = "Начальник медичної служби"
            signatory_department = "секції тилу"
            signatory_rank = "майор м/с"
            signatory_name = "Юлія ЮРЧАК"
        elif signatory == "company_commander":
            signatory_position = "Командир медичної роти"
            signatory_department = None
            signatory_rank = "капітан м/с"
            signatory_name = "Євгеній МАЖАЄВ"
        else:
            signatory_position = None
            signatory_department = None
            signatory_rank = None
            signatory_name = None
        
        # Валідація обов'язкових полів
        if not pib_nazivnyi:
            flash("ПІБ (в називному відмінку) є обов'язковим полем", "error")
            return render_template('medical_characteristic.html')
        if not final_enlistment_date:
            flash("Дата зарахування є обов'язковим полем", "error")
            return render_template('medical_characteristic.html')
        if not signatory:
            flash("Оберіть підписанта", "error")
            return render_template('medical_characteristic.html')
        if birth_date and not validate_date_format(birth_date):
            flash("Невірний формат дати народження. Використовуйте формат дд.мм.рррр", "error")
            return render_template('medical_characteristic.html')
        
        try:
            treatments_df = load_treatments_data()
        except Exception as e:
            logger.error(f"Помилка при завантаженні даних: {e}")
            flash(f"Помилка при завантаженні даних: {e}", "error")
            return render_template('medical_characteristic.html')
        
        pib_nazivnyi_clean = re.sub(r'\s+', ' ', pib_nazivnyi).strip().lower()
        soldier_records = treatments_df[treatments_df['ПІБ_чисте'] == pib_nazivnyi_clean]

        context = {}

        if not soldier_records.empty:
            first_record = soldier_records.iloc[0]
            kategoriia = first_record['Категорія']
            birth_date_obj = first_record['Дата народження']
            zvanie = ensure_medical_service_rank(first_record['Військове звання'])
            try:
                birth_date_str = birth_date_obj.strftime('%d.%m.%Y') if pd.notna(birth_date_obj) else "[дата не вказана]"
            except Exception:
                birth_date_str = "[дата не вказана]"
            context = {
                'zvanie': zvanie,
                'sluzhba_type': 'за контрактом' if 'контр' in str(kategoriia).lower() else 'під час мобілізації',
                'birth_date': birth_date_str,
                'treatment_history': format_treatment_history(soldier_records, hide_diagnosis_flag),
            }
        else:
            context = {
                'zvanie': ensure_medical_service_rank(request.form.get('zvanie')),
                'sluzhba_type': request.form.get('sluzhba_type'),
                'birth_date': request.form.get('birth_date'),
                'treatment_history': ["\t" + "За час проходження військової служби не знаходився на стаціонарному або амбулаторному лікуванні у закладах Міністерства охорони здоров'я України та медичних територіальних об'єднань Міністерства внутрішніх справ України."],
            }
        
        pib_nazivnyi_display = format_nominative_pib_display(pib_nazivnyi)
        context['pib_nazivnyi'] = pib_nazivnyi_display
        context['pib_rodovyi'] = build_pib_rodovyi_for_document(
            pib_nazivnyi_display, pib_rodovyi_input
        )
        context['enlistment_date'] = final_enlistment_date
        context['observation_end'] = final_observation_end
        context['signatory_position'] = signatory_position
        context['signatory_rank'] = signatory_rank
        context['signatory_name'] = signatory_name
        
        # Додаємо signatory_department тільки якщо воно не None (тобто не для Мажаєва)
        if signatory_department is not None:
            context['signatory_department'] = signatory_department
            context['signatory_department_with_break'] = signatory_department + "\n"
        else:
            context['signatory_department_with_break'] = ""
        
        # Створюємо поле для звання та імені з табуляцією для вирівнювання по краях
        context['signatory_rank_and_name'] = f"{signatory_rank}\t\t\t\t\t{signatory_name}"
        
        # Додаємо звання в родовому відмінку для шапки
        context['zvanie_genitive'] = format_rank_genitive(context.get('zvanie', ''))
        
        # Вибір шаблону залежно від підписанта
        if signatory == "company_commander":
            # Використовуємо окремий шаблон для Мажаєва
            template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'medical_characteristic_mazhaev_template.docx')
        else:
            # Стандартний шаблон для Гончарової та Юрчак
            template_path = MEDICAL_CHARACTERISTIC_TEMPLATE
        
        # Рендеримо DOCX із маркерами розділення абзаців для історії лікування
        doc = DocxTemplate(template_path)
        # Готуємо плейсхолдер з маркером для безпечної пост-обробки
        context_for_tpl = dict(context)
        history_parts = [line.lstrip('\t') for line in context.get('treatment_history', [])]
        joined_with_marker = "[[PARA_SPLIT]]".join(history_parts)
        context_for_tpl['treatment_history'] = joined_with_marker
        doc.render(context_for_tpl)

        # Відкриваємо результат та розгортаємо маркер у окремі абзаци (безпечний спосіб)
        temp_stream = io.BytesIO()
        doc.save(temp_stream)
        temp_stream.seek(0)
        rendered = DocxDocument(temp_stream)

        # Обробка маркерів розбиття на абзаци (тільки для історії лікування)
        split_marker = "[[PARA_SPLIT]]"
        for p in list(rendered.paragraphs):
            if split_marker in p.text:
                parts = p.text.split(split_marker)

                # Запам'ятовуємо стиль і зразок шрифту з плейсхолдера
                placeholder_style = p.style
                sample_font = p.runs[0].font if p.runs else None

                def apply_formatting(paragraph: DocxParagraph):
                    paragraph.style = placeholder_style
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                    fmt = paragraph.paragraph_format
                    fmt.space_before = Pt(0)
                    fmt.space_after = Pt(0)

                # Вставляємо кожен пункт перед плейсхолдером у прямому порядку
                for part in parts:
                    new_p = p.insert_paragraph_before()
                    apply_formatting(new_p)
                    run = new_p.add_run()
                    if sample_font is not None:
                        if sample_font.name is not None:
                            run.font.name = sample_font.name
                        if sample_font.size is not None:
                            run.font.size = sample_font.size
                        run.font.bold = sample_font.bold
                        run.font.italic = sample_font.italic
                        run.font.underline = sample_font.underline
                    run.add_tab()
                    run.add_text(part)

                # Видаляємо оригінальний абзац з маркером
                p._element.getparent().remove(p._element)

        file_stream = io.BytesIO()
        rendered.save(file_stream)
        file_stream.seek(0)

        response = make_response(send_file(
            file_stream, as_attachment=True,
            download_name=f'Медична_характеристика_{pib_nazivnyi_display.replace(" ", "_")}.docx',
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        ))
        response.set_cookie('fileDownload', 'true', max_age=20)
        
        return response

    return render_template('medical_characteristic.html')


@app.route('/service-characteristic', methods=['GET', 'POST'])
def service_characteristic():
    """Генерація службової характеристики."""
    signatory_defaults = load_service_signatories()
    if request.method == 'POST':
        pib_nazivnyi = _normalize_spaces(request.form.get('pib_nazivnyi', ''))
        zvanie_input = _normalize_spaces(request.form.get('zvanie', ''))
        prizvyshche_input = _normalize_spaces(request.form.get('prizvyshche', ''))
        imya_input = _normalize_spaces(request.form.get('imya', ''))
        po_batkovi_input = _normalize_spaces(request.form.get('po_batkovi', ''))
        posada_input = _normalize_spaces(request.form.get('zaimana_posada', ''))
        komisariat_input = request.form.get('komisariat_ta_data_prizovu', '')
        osvita_input = request.form.get('osvita', '')

        pidpysant_1_zvannya = _normalize_spaces(request.form.get('pidpysant_1_zvannya', ''))
        pidpysant_1_pib = _normalize_spaces(request.form.get('pidpysant_1_pib', ''))
        pidpysant_2_zvannya = _normalize_spaces(request.form.get('pidpysant_2_zvannya', ''))
        pidpysant_2_pib = _normalize_spaces(request.form.get('pidpysant_2_pib', ''))

        if not pib_nazivnyi:
            flash("ПІБ (в називному відмінку) є обов'язковим полем", "error")
            return render_template('service_characteristic.html', signatory_defaults=signatory_defaults)
        if not pidpysant_1_zvannya or not pidpysant_1_pib:
            flash("Заповніть звання і ПІБ для 1-го підписанта", "error")
            return render_template('service_characteristic.html', signatory_defaults=signatory_defaults)
        if not pidpysant_2_zvannya or not pidpysant_2_pib:
            flash("Заповніть звання і ПІБ для 2-го підписанта", "error")
            return render_template('service_characteristic.html', signatory_defaults=signatory_defaults)

        komisariat_ta_data_prizovu = _format_komisariat_and_date(komisariat_input)
        if not komisariat_ta_data_prizovu:
            flash("Поле 'Яким військовим комісаріатом і коли...' має містити коректну дату у форматі дд.мм.рррр", "error")
            return render_template('service_characteristic.html', signatory_defaults=signatory_defaults)

        osvita = _ensure_final_period(_title_first_letter(osvita_input))
        if not osvita:
            flash("Поле 'Освіта' є обов'язковим", "error")
            return render_template('service_characteristic.html', signatory_defaults=signatory_defaults)

        zvanie = zvanie_input
        prizvyshche = prizvyshche_input
        imya = imya_input
        po_batkovi = po_batkovi_input
        zaimana_posada = posada_input

        # Для службової характеристики пріоритет даних за штатом з base.xlsx.
        base_person = _lookup_person_in_base_excel(pib_nazivnyi)
        if base_person.get("zvanie"):
            zvanie = base_person["zvanie"]
        if base_person.get("zaimana_posada"):
            zaimana_posada = base_person["zaimana_posada"]
        prizvyshche = prizvyshche or base_person.get("prizvyshche", "")
        imya = imya or base_person.get("imya", "")
        po_batkovi = po_batkovi or base_person.get("po_batkovi", "")

        try:
            treatments_df = load_treatments_data()
        except Exception as e:
            logger.error(f"Помилка при завантаженні даних: {e}")
            flash(f"Помилка при завантаженні даних: {e}", "error")
            return render_template('service_characteristic.html', signatory_defaults=signatory_defaults)

        pib_clean = re.sub(r'\s+', ' ', pib_nazivnyi).strip().lower()
        soldier_records = treatments_df[treatments_df['ПІБ_чисте'] == pib_clean]
        if not soldier_records.empty:
            first_record = soldier_records.iloc[0]
            zvanie = zvanie or _excel_cell_str(first_record.get('Військове звання'))
            prizvyshche = prizvyshche or _excel_cell_str(first_record.get('Прізвище'))
            imya = imya or _excel_cell_str(first_record.get("Ім'я"))
            po_batkovi = po_batkovi or _excel_cell_str(first_record.get('По батькові'))
            if not zaimana_posada:
                zaimana_posada = _extract_position_from_row(first_record)

        if not (prizvyshche and imya and po_batkovi):
            p_sur, p_first, p_pat = _split_pib(pib_nazivnyi)
            prizvyshche = prizvyshche or p_sur
            imya = imya or p_first
            po_batkovi = po_batkovi or p_pat

        if not zvanie:
            flash("Не вдалося визначити військове звання. Заповніть поле вручну.", "error")
            return render_template('service_characteristic.html', signatory_defaults=signatory_defaults)
        if not (prizvyshche and imya and po_batkovi):
            flash("Не вдалося визначити Прізвище / Ім'я / По батькові. Уточніть ПІБ або заповніть поля вручну.", "error")
            return render_template('service_characteristic.html', signatory_defaults=signatory_defaults)

        if not zaimana_posada:
            zaimana_posada = "У розпорядженні командира військової частини"
        zaimana_posada = _ensure_final_period(zaimana_posada)
        zaimana_posada_z_velykoi = _title_first_letter(zaimana_posada.rstrip('.'))

        pib_gen_line = build_pib_rodovyi_for_document(
            f"{prizvyshche} {imya} {po_batkovi}",
            "",
        )
        pib_gen_parts = pib_gen_line.split(' ')
        gen_surname = _surname_first_capital(pib_gen_parts[0] if pib_gen_parts else prizvyshche)
        dat_surname = _surname_to_dative(prizvyshche)
        fraza_zvannya_ta_pib_rodovyi = _normalize_spaces(
            f"{format_rank_genitive(zvanie)} {_format_initials_name(gen_surname, imya, po_batkovi)}"
        )
        fraza_zvannya_ta_pib_davalyi = _normalize_spaces(
            f"{format_rank_dative(zvanie)} {_format_initials_name(dat_surname, imya, po_batkovi)}"
        )

        context = {
            'zvanie': zvanie,
            'prizvyshche': prizvyshche.upper(),
            'imya': imya,
            'po_batkovi': po_batkovi,
            'zaimana_posada': zaimana_posada,
            'zaimana_posada_z_velykoi': zaimana_posada_z_velykoi,
            'komisariat_ta_data_prizovu': komisariat_ta_data_prizovu,
            'osvita': osvita,
            'fraza_zvannya_ta_pib_rodovyi': fraza_zvannya_ta_pib_rodovyi,
            'fraza_zvannya_ta_pib_davalyi': fraza_zvannya_ta_pib_davalyi,
            'pidpysant_1_zvannya': pidpysant_1_zvannya,
            'pidpysant_1_pib': pidpysant_1_pib,
            'pidpysant_2_zvannya': pidpysant_2_zvannya,
            'pidpysant_2_pib': pidpysant_2_pib,
            'imya_ta_prizvyshche_pidpys': f"{imya} {prizvyshche.upper()}",
        }

        try:
            save_service_signatories({
                'pidpysant_1_zvannya': pidpysant_1_zvannya,
                'pidpysant_1_pib': pidpysant_1_pib,
                'pidpysant_2_zvannya': pidpysant_2_zvannya,
                'pidpysant_2_pib': pidpysant_2_pib,
            })
            doc = DocxTemplate(SERVICE_CHARACTERISTIC_TEMPLATE)
            doc.render(context)
            file_stream = io.BytesIO()
            doc.save(file_stream)
            file_stream.seek(0)
        except Exception as e:
            logger.error("Помилка при рендері службової характеристики: %s", e)
            flash(f"Помилка генерації DOCX: {e}", "error")
            return render_template('service_characteristic.html', signatory_defaults=signatory_defaults)

        response = make_response(send_file(
            file_stream, as_attachment=True,
            download_name=f'Службова_характеристика_{imya}_{prizvyshche.upper()}.docx',
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        ))
        response.set_cookie('fileDownload', 'true', max_age=20)
        return response

    return render_template('service_characteristic.html', signatory_defaults=signatory_defaults)


@app.route('/vlk-report', methods=['GET', 'POST'])
def vlk_report():
    """Генерація рапорту на ВЛК через Jinja-плейсхолдери у vlk_report.docx."""
    signatory_defaults = load_service_signatories()
    vlk_signatory_defaults = load_vlk_signatories()
    defaults = {
        "oglyad_nuber": "",
        "likar": "",
        "hospital": "",
        "pib_nazivnyi": "",
        "tvo_kombrig_zvannya": vlk_signatory_defaults.get("tvo_kombrig_zvannya", ""),
        "tvo_kombrig_pib": vlk_signatory_defaults.get("tvo_kombrig_pib", ""),
        "tvo_kombat_zvannya": vlk_signatory_defaults.get("tvo_kombat_zvannya", "") or signatory_defaults.get("pidpysant_2_zvannya", ""),
        "tvo_kombat_pib": vlk_signatory_defaults.get("tvo_kombat_pib", "") or signatory_defaults.get("pidpysant_2_pib", ""),
    }

    if request.method == 'POST':
        if not os.path.isfile(VLK_REPORT_TEMPLATE):
            flash("Не знайдено шаблон templates/vlk_report.docx", "error")
            return render_template('vlk_report.html', defaults=defaults)

        payload = {
            "oglyad_nuber": _normalize_spaces(request.form.get("oglyad_nuber", "")),
            "likar": _normalize_spaces(request.form.get("likar", "")),
            "hospital": _normalize_spaces(request.form.get("hospital", "")),
            "pib_nazivnyi": _normalize_spaces(request.form.get("pib_nazivnyi", "")),
            "tvo_kombrig_zvannya": _normalize_spaces(request.form.get("tvo_kombrig_zvannya", "")),
            "tvo_kombrig_pib": _normalize_spaces(request.form.get("tvo_kombrig_pib", "")),
            "tvo_kombat_zvannya": _normalize_spaces(request.form.get("tvo_kombat_zvannya", "")),
            "tvo_kombat_pib": _normalize_spaces(request.form.get("tvo_kombat_pib", "")),
        }
        defaults.update(payload)
        if not all(payload.values()):
            flash("Заповніть усі обов'язкові поля рапорту.", "error")
            return render_template('vlk_report.html', defaults=defaults)

        person = _lookup_person_in_base_excel(payload["pib_nazivnyi"])
        if not person.get("zvanie") or not person.get("zaimana_posada"):
            try:
                treatments_df = load_treatments_data()
                pib_clean = re.sub(r'\s+', ' ', payload["pib_nazivnyi"]).strip().lower()
                soldier_records = treatments_df[treatments_df['ПІБ_чисте'] == pib_clean]
                if not soldier_records.empty:
                    row = soldier_records.iloc[0]
                    person["zvanie"] = person.get("zvanie") or _excel_cell_str(row.get('Військове звання'))
                    person["zaimana_posada"] = person.get("zaimana_posada") or _extract_position_from_row(row)
                    person["prizvyshche"] = person.get("prizvyshche") or _excel_cell_str(row.get('Прізвище'))
                    person["imya"] = person.get("imya") or _excel_cell_str(row.get("Ім'я"))
                    person["po_batkovi"] = person.get("po_batkovi") or _excel_cell_str(row.get('По батькові'))
            except Exception:
                pass

        zvanie = person.get("zvanie", "")
        zaimana_posada = person.get("zaimana_posada", "")
        sur = person.get("prizvyshche", "")
        first = person.get("imya", "")
        pat = person.get("po_batkovi", "")

        if not zvanie or not zaimana_posada:
            flash("Не вдалося автоматично підтягнути звання/посаду/ПІБ з base.xlsx. Уточніть ПІБ.", "error")
            return render_template('vlk_report.html', defaults=defaults)

        if not (sur and first):
            p_sur, p_first, p_pat = _split_pib(payload["pib_nazivnyi"])
            sur = sur or p_sur
            first = first or p_first
            pat = pat or p_pat

        pib_gen_line = build_pib_rodovyi_for_document(f"{sur} {first} {pat}", "")
        pib_gen_parts = pib_gen_line.split(' ')
        gen_surname = _surname_first_capital(pib_gen_parts[0] if pib_gen_parts else sur)
        fraza_zvannya_ta_pib_rodovyi = _normalize_spaces(
            f"{format_rank_genitive(zvanie)} {_format_initials_name(gen_surname, first, pat)}"
        )
        tvo_kombrig_zvannya_dav = format_rank_dative(payload["tvo_kombrig_zvannya"])
        tvo_kombrig_pib_dav = _uppercase_last_word(_pib_to_dative(payload["tvo_kombrig_pib"]))
        tvo_kombat_zvannya_dav = format_rank_dative(payload["tvo_kombat_zvannya"])
        tvo_kombat_pib_dav = _uppercase_last_word(_pib_to_dative(payload["tvo_kombat_pib"]))
        tvo_kombrig_zvannya_short = _rank_short(payload["tvo_kombrig_zvannya"])
        tvo_kombrig_pib_short = _pib_short_with_initials(payload["tvo_kombrig_pib"])
        context = {
            "oglyad_nuber": payload["oglyad_nuber"],
            "likar": payload["likar"],
            "hospital": payload["hospital"],
            "zaimana_posada_z_velykoi": _title_first_letter(_ensure_final_period(zaimana_posada).rstrip(".")),
            "zvanie": zvanie,
            "imya_ta_prizvyshche_pidpys": _normalize_spaces(f"{first} {sur.upper()}"),
            "tvo_kombrig_zvannya": payload["tvo_kombrig_zvannya"],
            "tvo_kombrig_pib": payload["tvo_kombrig_pib"],
            "tvo_kombrig_zvannya_dav": tvo_kombrig_zvannya_dav,
            "tvo_kombrig_pib_dav": tvo_kombrig_pib_dav,
            "fraza_zvannya_ta_pib_rodovyi": fraza_zvannya_ta_pib_rodovyi,
            "tvo_kombat_zvannya": payload["tvo_kombat_zvannya"],
            "tvo_kombat_pib": payload["tvo_kombat_pib"],
            "tvo_kombat_zvannya_dav": tvo_kombat_zvannya_dav,
            "tvo_kombat_pib_dav": tvo_kombat_pib_dav,
            "tvo_kombrig_zvannya_short": tvo_kombrig_zvannya_short,
            "tvo_kombrig_pib_short": tvo_kombrig_pib_short,
            # Сумісність із шаблонами, де частина плейсхолдерів введена ВЕЛИКИМИ.
            "TVO_KOMBRIG_PIB_DAV": tvo_kombrig_pib_dav,
            "TVO_KOMBAT_PIB_DAV": tvo_kombat_pib_dav,
        }

        try:
            save_vlk_signatories({
                "tvo_kombrig_zvannya": payload["tvo_kombrig_zvannya"],
                "tvo_kombrig_pib": payload["tvo_kombrig_pib"],
                "tvo_kombat_zvannya": payload["tvo_kombat_zvannya"],
                "tvo_kombat_pib": payload["tvo_kombat_pib"],
            })
            # Підтримка шаблону з потенційними описками у назвах плейсхолдерів.
            context["tvo_kombat_ zvannya"] = payload["tvo_kombat_zvannya"]
            context["tvo_kombat_ zvannya_dav"] = tvo_kombat_zvannya_dav
            doc = DocxTemplate(VLK_REPORT_TEMPLATE)
            doc.render(context)
            file_stream = io.BytesIO()
            doc.save(file_stream)
            file_stream.seek(0)
        except Exception as e:
            logger.error("Помилка при генерації рапорту ВЛК: %s", e)
            flash(f"Помилка генерації DOCX: {e}", "error")
            return render_template('vlk_report.html', defaults=defaults)

        safe_name = secure_filename(sur or "VLK")
        response = make_response(send_file(
            file_stream, as_attachment=True,
            download_name=f'Рапорт_на_ВЛК_{safe_name}.docx',
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        ))
        response.set_cookie('fileDownload', 'true', max_age=20)
        return response

    return render_template('vlk_report.html', defaults=defaults)

def _warmup_treatments_cache():
    """Фоновий прогрів кешу після старту — перший пошук не чекає читання великих Excel."""
    try:
        with app.app_context():
            load_treatments_data()
        logger.info("Прогрів кешу Excel завершено — пошук готовий.")
    except Exception as e:
        logger.warning(
            "Прогрів кешу Excel не вдався (дані завантажаться при першому запиті): %s",
            e,
        )


if __name__ == '__main__':
    threading.Thread(target=_warmup_treatments_cache, daemon=True).start()
    app.run(debug=DEBUG)