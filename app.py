#!/usr/bin/env python3
"""
Головний Flask додаток для генерації медичних документів
"""

import os
import sys
import shutil
import tempfile
import threading
import pandas as pd
from flask import Flask, render_template, request, send_file, make_response, flash, jsonify
from werkzeug.utils import secure_filename

from docxtpl import DocxTemplate
from docx import Document as DocxDocument
from docx.text.paragraph import Paragraph as DocxParagraph
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
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
    
    rank_lower = rank.lower().strip()
    
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
        return genitive_ranks[rank_lower]
    
    # Якщо не знайдено точне співпадіння, повертаємо оригінал
    return rank

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


@app.route('/api/search_pib', methods=['GET'])
def search_pib():
    """API endpoint для пошуку ПІБ"""
    try:
        query = request.args.get('q', '').strip()
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
            birth_date = row.get("Дата народження")

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
            signatory_rank = "капітан м\\с"
            signatory_name = "Ірина ГОНЧАРОВА"
        elif signatory == "chief":
            signatory_position = "Начальник медичної служби"
            signatory_department = "секції тилу"
            signatory_rank = "капітан м\\с"
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
            try:
                birth_date_str = birth_date_obj.strftime('%d.%m.%Y') if pd.notna(birth_date_obj) else "[дата не вказана]"
            except Exception:
                birth_date_str = "[дата не вказана]"
            context = {
                'zvanie': first_record['Військове звання'],
                'sluzhba_type': 'за контрактом' if 'контр' in str(kategoriia).lower() else 'під час мобілізації',
                'birth_date': birth_date_str,
                'treatment_history': format_treatment_history(soldier_records, hide_diagnosis_flag),
            }
        else:
            context = {
                'zvanie': request.form.get('zvanie'),
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