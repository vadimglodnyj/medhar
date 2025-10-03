#!/usr/bin/env python3
"""
Головний Flask додаток для генерації медичних документів
"""

import os
import sys
import pandas as pd
import numpy as np
from flask import Flask, render_template, request, send_file, make_response, flash, redirect, url_for, jsonify

def convert_to_json_serializable(obj):
    """Конвертує об'єкти pandas/numpy в JSON-сумісні типи"""
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif pd.isna(obj):
        return None
    else:
        return obj

def parse_treatment_days(days_value):
    """Парсить кількість днів лікування з очищенням від зайвих символів"""
    if days_value is None:
        return 0
    try:
        # Очищаємо від переносів рядків та зайвих символів
        days_str = str(days_value).strip()
        
        # Якщо є переноси рядків, це означає детальну інформацію про лікування
        if '\n' in days_str or '\r' in days_str:
            # Розбиваємо по рядках
            lines = days_str.replace('\r', '\n').split('\n')
            
            # Шукаємо рядок з загальною кількістю днів
            # Зазвичай це перший рядок або рядок з найбільшим числом
            total_days = 0
            for line in lines:
                line = line.strip()
                if line:
                    # Шукаємо числа в рядку
                    import re
                    numbers = re.findall(r'\d+(?:\.\d+)?', line)
                    if numbers:
                        # Беремо перше число як потенційну загальну кількість днів
                        try:
                            days = float(numbers[0])
                            if days > total_days:  # Беремо найбільше число як загальну кількість
                                total_days = days
                        except (ValueError, TypeError):
                            continue
            
            return total_days
        else:
            # Якщо немає переносів рядків, просто парсимо число
            days_str = days_str.replace('\n', ' ').replace('\r', ' ')
            days_str = days_str.split()[0] if days_str.split() else '0'
            return float(days_str)
    except (ValueError, TypeError, IndexError):
        return 0
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

# Імпортуємо генератори
from generators.service_characteristic import ServiceCharacteristicGenerator
from generators.vlk_report import VLKReportGenerator
from generators.payment_analyzer import PaymentAnalyzer, PaymentReportGenerator
from generators.medical_payment_analyzer import MedicalPaymentAnalyzer
from utils.database_reader import PersonnelDatabase
from utils.circumstances_parser import parse_circumstances
from utils.pdf_parser import extract_data_from_pdf
from utils.new_medical_database import NewMedicalDatabase

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Ініціалізуємо нову базу даних (використовуємо файл у папці database)
new_medical_db = NewMedicalDatabase("database/medical_new.db")

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Глобальна змінна для кешування даних
treatments_cache = None
cache_timestamp = None

# Пам'ять для результатів з PDF (ключ: ПІБ_чисте)
pdf_overrides = {}

def load_treatments_data():
    """Завантажує дані з Excel файлів з кешуванням (гібридна система: архів + нові дані)"""
    global treatments_cache, cache_timestamp
    
    # Перевіряємо чи потрібно оновити кеш (кожні 5 хвилин)
    if treatments_cache is not None and cache_timestamp is not None:
        if (datetime.now() - cache_timestamp).seconds < 300:  # 5 хвилин
            return treatments_cache
    
    try:
        logger.info("Завантаження даних з Excel файлів (гібридна система)...")
        
        # Перевіряємо існування файлів
        if not os.path.exists(TREATMENTS_2025_FILE):
            raise FileNotFoundError(f"Файл {TREATMENTS_2025_FILE} не знайдено")
        
        logger.info("Файли Excel знайдено, починаємо завантаження...")
        
        # Завантажуємо нові дані з 2025
        df_new_data = pd.read_excel(TREATMENTS_2025_FILE)
        df_new_data.columns = df_new_data.columns.str.strip()
        logger.info(f"Завантажено нових записів з 2025: {len(df_new_data)}")
        
        # Завантажуємо архівні дані
        if os.path.exists(TREATMENTS_FINAL_FILE):
            logger.info("Завантажуємо архівні дані з treatments_final.xlsx...")
            df_archive = pd.read_excel(TREATMENTS_FINAL_FILE)
            df_archive.columns = df_archive.columns.str.strip()
            logger.info(f"Завантажено архівних записів: {len(df_archive)}")
            
            # Об'єднуємо архів + нові дані
            logger.info("Об'єднуємо архівні та нові дані...")
            treatments_df = pd.concat([df_archive, df_new_data], ignore_index=True)
            
            # Видаляємо дублікати
            logger.info("Видаляємо дублікати...")
            initial_count = len(treatments_df)
            
            # Створюємо унікальний ключ для виявлення дублікатів
            treatments_df['ПІБ_чисте'] = (
                treatments_df['Прізвище'].fillna('').astype(str) + ' ' +
                treatments_df['Ім\'я'].fillna('').astype(str) + ' ' +
                treatments_df['По батькові'].fillna('').astype(str)
            ).str.replace(r'\s+', ' ', regex=True).str.strip().str.lower()
            
            # Видаляємо дублікати за ПІБ та датою надходження
            # Але зберігаємо ВСІ унікальні записи (не тільки останні)
            if 'Дата надходження в поточний Л/З' in treatments_df.columns:
                treatments_df['Дата надходження в поточний Л/З'] = pd.to_datetime(treatments_df['Дата надходження в поточний Л/З'], errors='coerce')
                # Видаляємо тільки точні дублікати (ПІБ + дата), але зберігаємо різні дати лікування
                treatments_df = treatments_df.drop_duplicates(
                    subset=['ПІБ_чисте', 'Дата надходження в поточний Л/З'], 
                    keep='first'  # Залишаємо перший запис (з архіву)
                )
            else:
                # Якщо немає дати, видаляємо дублікати за ПІБ, але зберігаємо перший запис
                treatments_df = treatments_df.drop_duplicates(subset=['ПІБ_чисте'], keep='first')
            
            final_count = len(treatments_df)
            duplicates_removed = initial_count - final_count
            logger.info(f"Видалено дублікатів: {duplicates_removed}")
            logger.info(f"Фінальна кількість записів: {final_count}")
            
        else:
            # Fallback: якщо архіву немає, використовуємо тільки нові дані
            logger.warning("Архівний файл treatments_final.xlsx не знайдено, використовуємо тільки нові дані")
            treatments_df = df_new_data
        
        # Обробка дат
        date_columns = ['Дата надходження в поточний Л/З', 'Дата виписки', 'Дата народження', 'Дата первинної госпіталізації', 'Дата виписки з поточного Л/З']
        for col in date_columns:
            if col in treatments_df.columns:
                treatments_df[col] = pd.to_datetime(treatments_df[col], errors='coerce')
        
        # Обробка ПІБ (якщо ще не створено)
        for col in ['Прізвище', 'Ім\'я', 'По батькові']:
            if col in treatments_df.columns:
                treatments_df[col] = treatments_df[col].fillna('').astype(str)
        
        # Створюємо ПІБ та ПІБ_чисте (якщо ще не створено)
        if 'ПІБ' not in treatments_df.columns:
            treatments_df['ПІБ'] = treatments_df['Прізвище'] + ' ' + treatments_df['Ім\'я'] + ' ' + treatments_df['По батькові']
        
        if 'ПІБ_чисте' not in treatments_df.columns:
            treatments_df['ПІБ_чисте'] = treatments_df['ПІБ'].str.replace(r'\s+', ' ', regex=True).str.strip().str.lower()
        
        # Оновлюємо кеш
        treatments_cache = treatments_df
        cache_timestamp = datetime.now()
        
        logger.info(f"Дані успішно завантажено. Записів: {len(treatments_df)}")
        return treatments_df
        
    except Exception as e:
        logger.error(f"Помилка при завантаженні даних: {e}")
        raise

def format_pib_rodovyi(pib_rodovyi_input):
    """Форматує ПІБ у родовому відмінку"""
    if not pib_rodovyi_input or not pib_rodovyi_input.strip():
        return ""
    
    parts = pib_rodovyi_input.strip().split()
    if not parts: 
        return ""
    
    parts[0] = parts[0].upper()
    for i in range(1, len(parts)): 
        parts[i] = parts[i].title()
    return " ".join(parts)

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

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """API endpoint для отримання статистики бази даних"""
    try:
        # Використовуємо SQLite БД замість Excel
        stats = new_medical_db.get_database_stats()
        
        return jsonify(convert_to_json_serializable(stats))
        
    except Exception as e:
        logger.error(f"Помилка при отриманні статистики: {e}")
        return jsonify({'error': str(e)})

@app.route('/api/search_pib', methods=['GET'])
def search_pib():
    """API endpoint для пошуку ПІБ"""
    try:
        query = request.args.get('q', '').strip()
        if len(query) < 2:
            return jsonify({'results': []})
        
        # Пошук у SQLite БД
        results = new_medical_db.search_patients(query, limit=10)
        return jsonify({'results': results})
        
    except Exception as e:
        logger.error(f"Помилка при пошуку ПІБ: {e}")
        return jsonify({'results': [], 'error': str(e)})

@app.route('/', methods=['GET', 'POST'])
def index():
    """Головна сторінка з формою для вибору типу документа"""
    if request.method == 'POST':
        document_type = request.form.get('document_type', 'medical_characteristic')
        
        # Перенаправляємо на відповідний маршрут
        if document_type == 'service_characteristic':
            return redirect(url_for('service_characteristic'))
        elif document_type == 'vlk_report':
            return redirect(url_for('vlk_report'))
        elif document_type == 'payment_reports':
            return redirect(url_for('payment_reports'))
        else:
            return redirect(url_for('medical_characteristic'))
    
    return render_template('index.html')

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
        if not pib_rodovyi_input:
            flash("ПІБ (в родовому відмінку) є обов'язковим полем", "error")
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

        # Інтегруємо можливі оновлення з PDF (за пріоритетом над Excel)
        pdf_episodes = pdf_overrides.get(pib_nazivnyi_clean)
        context = {}
        
        if not soldier_records.empty:
            first_record = soldier_records.iloc[0]
            kategoriia = first_record['Категорія']
            birth_date_obj = first_record['Дата народження']
            try:
                birth_date_str = birth_date_obj.strftime('%d.%m.%Y') if pd.notna(birth_date_obj) else "[дата не вказана]"
            except:
                birth_date_str = "[дата не вказана]"
            context = {
                'zvanie': first_record['Військове звання'],
                'sluzhba_type': 'за контрактом' if 'контр' in str(kategoriia).lower() else 'під час мобілізації',
                'birth_date': birth_date_str,
            }
            # Якщо є епізоди з PDF, оновлюємо діагнози/епізоди виводу
            if pdf_episodes:
                # Готуємо копію для виводу
                df_copy = soldier_records.copy()
                try:
                    # Оновлюємо або додаємо епізоди за датами
                    for ep in pdf_episodes:
                        ds = ep.get('date_start')
                        de = ep.get('date_end')
                        dx = ep.get('diagnosis') or ''
                        # Підбір запису з такими ж датами
                        if ds and de and 'Дата надходження в поточний Л/З' in df_copy.columns and 'Дата виписки' in df_copy.columns:
                            # Порівнюємо як рядок dd.mm.yyyy
                            mask = (
                                df_copy['Дата надходження в поточний Л/З'].dt.strftime('%d.%m.%Y') == ds
                            ) & (
                                df_copy['Дата виписки'].dt.strftime('%d.%m.%Y') == de
                            )
                            if mask.any():
                                df_copy.loc[mask, 'Попередній діагноз'] = dx
                            else:
                                # Додаємо новий епізод мінімально необхідними полями
                                new_row = first_record.copy()
                                try:
                                    new_row['Дата надходження в поточний Л/З'] = pd.to_datetime(ds, format='%d.%m.%Y', errors='coerce')
                                    new_row['Дата виписки'] = pd.to_datetime(de, format='%d.%m.%Y', errors='coerce')
                                except Exception:
                                    pass
                                new_row['Попередній діагноз'] = dx
                                df_copy = pd.concat([df_copy, pd.DataFrame([new_row])], ignore_index=True)
                        elif dx:
                            # Якщо немає дат — принаймні замінимо діагноз першого запису
                            df_copy.loc[df_copy.index[:1], 'Попередній діагноз'] = dx
                except Exception as _e:
                    logger.warning(f"Не вдалося інтегрувати епізоди PDF: {_e}")
                context['treatment_history'] = format_treatment_history(df_copy, hide_diagnosis_flag)
            else:
                context['treatment_history'] = format_treatment_history(soldier_records, hide_diagnosis_flag)
        else:
            context = {
                'zvanie': request.form.get('zvanie'),
                'sluzhba_type': request.form.get('sluzhba_type'),
                'birth_date': request.form.get('birth_date'),
            }
            # Якщо завантажили PDF для невідомої особи — виводимо епізоди з PDF
            if pdf_episodes:
                # Побудуємо прості рядки історії за епізодами
                lines = []
                for ep in pdf_episodes:
                    ds = ep.get('date_start') or '[дата не вказана]'
                    de = ep.get('date_end') or 'по теперішній час'
                    dx = ep.get('diagnosis') or ''
                    base = f"З {ds} по {de} отримував(ла) медичну допомогу"
                    if hide_diagnosis_flag:
                        lines.append(base + ".")
                    else:
                        if dx and not dx.endswith('.'):
                            dx = dx + '.'
                        lines.append(base + f" Діагноз: {dx}")
                context['treatment_history'] = ["\t" + l for l in lines] if lines else ["\t" + "За час проходження військової служби не знаходився на стаціонарному або амбулаторному лікуванні у закладах Міністерства охорони здоров'я України та медичних територіальних об'єднань Міністерства внутрішніх справ України."]
            else:
                context['treatment_history'] = ["\t" + "За час проходження військової служби не знаходився на стаціонарному або амбулаторному лікуванні у закладах Міністерства охорони здоров'я України та медичних територіальних об'єднань Міністерства внутрішніх справ України."]
        
        context['pib_nazivnyi'] = pib_nazivnyi
        context['pib_rodovyi'] = format_pib_rodovyi(pib_rodovyi_input)
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
            download_name=f'Медична_характеристика_{pib_nazivnyi}.docx',
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        ))
        response.set_cookie('fileDownload', 'true', max_age=20)
        
        return response

    return render_template('medical_characteristic.html')

@app.route('/service-characteristic', methods=['GET', 'POST'])
def service_characteristic():
    """Генерація службової характеристики"""
    if request.method == 'POST':
        try:
            # Валідація даних
            generator = ServiceCharacteristicGenerator()
            is_valid, error = generator.validate_data(request.form.to_dict())
            
            if not is_valid:
                flash(error, "error")
                return render_template('service_characteristic.html')
            
            # Генерація документа
            response = generator.generate_document(
                request.form.to_dict(), 
                "Службова_характеристика"
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Помилка генерації службової характеристики: {e}")
            flash(f"Помилка генерації документа: {e}", "error")
            return render_template('service_characteristic.html')
    
    return render_template('service_characteristic.html')

@app.route('/vlk-report', methods=['GET', 'POST'])
def vlk_report():
    """Генерація рапорту ВЛК"""
    if request.method == 'POST':
        try:
            # Валідація даних
            generator = VLKReportGenerator()
            is_valid, error = generator.validate_data(request.form.to_dict())
            
            if not is_valid:
                flash(error, "error")
                return render_template('vlk_report.html')
            
            # Генерація документа
            response = generator.generate_document(
                request.form.to_dict(), 
                "Рапорт_ВЛК"
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Помилка генерації рапорту ВЛК: {e}")
            flash(f"Помилка генерації документа: {e}", "error")
            return render_template('vlk_report.html')
    
    return render_template('vlk_report.html')

@app.route('/api/upload_pdf', methods=['POST'])
def upload_pdf():
    """Завантаження PDF, парсинг та збереження епізодів в пам'яті."""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Файл не надіслано'}), 400
        file = request.files['file']
        person_pib = (request.form.get('pib') or '').strip().lower()
        if not person_pib:
            return jsonify({'error': 'Не вказано ПІБ для привʼязки'}), 400

        # Збережемо тимчасово
        temp_dir = os.path.join(TEMP_DIR)
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.pdf")
        file.save(temp_path)

        episodes = extract_data_from_pdf(temp_path) or []
        pdf_overrides[person_pib] = episodes

        return jsonify({'episodes': episodes, 'count': len(episodes)})
    except Exception as e:
        logger.error(f"Помилка при обробці PDF: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/apply_pdf_overrides', methods=['POST'])
def apply_pdf_overrides():
    """Застосувати епізоди, завантажені з PDF (pdf_overrides), до Excel та зберегти зміни.

    Логіка:
      - Завантажуємо актуальні дані (із кешем)
      - Для кожного ПІБ_чисте у pdf_overrides намагаємось знайти відповідні записи
      - Якщо знайдено записи з такими ж датами — оновлюємо 'Попередній діагноз'
      - Якщо не знайдено — додаємо нові рядки, копіюючи базовий запис для людини
      - Робимо бекап файлу призначення та зберігаємо оновлений датафрейм
      - Оновлюємо кеш
    """
    try:
        # Перевірка, що є дані для застосування
        if not pdf_overrides:
            return jsonify({'error': 'Немає завантажених епізодів для застосування'}), 400

        # Завантажити дані
        treatments_df = load_treatments_data().copy()

        # Підготовка колонок та форматів
        if 'ПІБ_чисте' not in treatments_df.columns:
            treatments_df['ПІБ'] = (
                treatments_df['Прізвище'].fillna('').astype(str) + ' ' +
                treatments_df["Ім'я"].fillna('').astype(str) + ' ' +
                treatments_df['По батькові'].fillna('').astype(str)
            )
            treatments_df['ПІБ_чисте'] = (
                treatments_df['ПІБ'].str.replace(r'\s+', ' ', regex=True).str.strip().str.lower()
            )

        # Допоміжна функція: знайти базовий запис для ПІБ
        def pick_base_row_for_person(df, pib_clean):
            subset = df[df['ПІБ_чисте'] == pib_clean]
            if subset.empty:
                return None
            return subset.iloc[0]

        updates = 0
        inserts = 0

        for pib_clean, episodes in pdf_overrides.items():
            if not episodes:
                continue
            base_row = pick_base_row_for_person(treatments_df, pib_clean)
            for ep in episodes:
                ds = ep.get('date_start')
                de = ep.get('date_end')
                dx = (ep.get('diagnosis') or '').strip()

                if ds and de and 'Дата надходження в поточний Л/З' in treatments_df.columns and 'Дата виписки' in treatments_df.columns:
                    try:
                        mask = (
                            treatments_df['ПІБ_чисте'].astype(str) == pib_clean
                        ) & (
                            treatments_df['Дата надходження в поточний Л/З'].dt.strftime('%d.%m.%Y') == ds
                        ) & (
                            treatments_df['Дата виписки'].dt.strftime('%d.%m.%Y') == de
                        )
                    except Exception:
                        mask = False
                    if isinstance(mask, pd.Series) and mask.any():
                        treatments_df.loc[mask, 'Попередній діагноз'] = dx
                        updates += int(mask.sum())
                        continue

                # Якщо не оновили — додаємо новий рядок, якщо є базовий
                if base_row is not None:
                    new_row = base_row.copy()
                    # Спробуємо заповнити дати
                    try:
                        if ds:
                            new_row['Дата надходження в поточний Л/З'] = pd.to_datetime(ds, format='%d.%m.%Y', errors='coerce')
                        if de:
                            new_row['Дата виписки'] = pd.to_datetime(de, format='%d.%m.%Y', errors='coerce')
                    except Exception:
                        pass
                    new_row['Попередній діагноз'] = dx
                    treatments_df = pd.concat([treatments_df, pd.DataFrame([new_row])], ignore_index=True)
                    inserts += 1

        # Збереження з бекапом
        os.makedirs(os.path.join(BASE_DIR, 'backup'), exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = os.path.join(BASE_DIR, 'backup', f'final_backup_{ts}.xlsx')

        # Визначаємо цільовий файл для збереження
        target_path = TREATMENTS_FINAL_FILE

        try:
            if os.path.exists(target_path):
                # Бекап існуючого файлу
                try:
                    import shutil
                    shutil.copy2(target_path, backup_path)
                except Exception as e:
                    logger.warning(f"Не вдалося створити бекап: {e}")

            # Запис у файл
            treatments_df.to_excel(target_path, index=False)

            # Оновити кеш
            global treatments_cache, cache_timestamp
            treatments_cache = treatments_df
            cache_timestamp = datetime.now()
        except Exception as e:
            logger.error(f"Помилка збереження Excel: {e}")
            return jsonify({'error': f'Помилка збереження Excel: {e}'}), 500

        return jsonify({'updated': updates, 'inserted': inserts, 'saved_to': target_path})
    except Exception as e:
        logger.error(f"Помилка застосування епізодів: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/payment-reports', methods=['GET'])
def payment_reports():
    """Сторінка звітів по виплатах хворим з пораненнями"""
    return render_template('payment_reports.html')

@app.route('/api/generate_payment_report', methods=['POST'])
def generate_payment_report():
    """API для генерації звіту по виплатах"""
    try:
        data = request.get_json()
        target_units = data.get('target_units', ['2 БОП', '6 БОП'])
        report_type = data.get('report_type', 'excel')
        
        analyzer = PaymentAnalyzer()
        
        if report_type == 'excel':
            # Генеруємо Excel звіт
            output_file = analyzer.generate_payment_report(target_units, 'excel')
            
            # Відправляємо файл
            return send_file(
                output_file,
                as_attachment=True,
                download_name=f'payment_analysis_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx',
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        else:
            # Повертаємо JSON з даними
            results = analyzer.generate_payment_report(target_units, 'json')
            # Перетворюємо DataFrame в словники для JSON серіалізації
            if isinstance(results, dict):
                for unit, data in results.items():
                    if isinstance(data, dict) and 'combined_data' in data:
                        # Перетворюємо DataFrame в список словників
                        if hasattr(data['combined_data'], 'to_dict'):
                            data['combined_data'] = data['combined_data'].to_dict('records')
            return jsonify(results)
            
    except Exception as e:
        logger.error(f"Помилка генерації звіту по виплатах: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/search_patient_payments', methods=['GET'])
def search_patient_payments():
    """API для пошуку історії виплат пацієнта"""
    try:
        patient_name = request.args.get('name', '').strip()
        if not patient_name:
            return jsonify({'error': 'Не вказано ім\'я пацієнта'}), 400
        
        # Використовуємо нову базу даних
        patient_info = new_medical_db.get_patient_info(patient_name)
        
        if not patient_info:
            return jsonify({'error': 'Пацієнт не знайдено'}), 404
        
        # Формуємо відповідь у зручному форматі
        response = {
            'patient': {
                'full_name': patient_info['full_name'],
                'rank': patient_info['rank'],
                'unit_name': patient_info['unit_name']
            },
            'treatments': patient_info['treatments'],
            'payments': patient_info['payments']
        }
        
        return jsonify(convert_to_json_serializable(response))
        
    except Exception as e:
        logger.error(f"Помилка пошуку історії виплат пацієнта: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/monthly_stats/<month>', methods=['GET'])
def monthly_stats(month):
    """API для отримання статистики по місяцях"""
    try:
        # Використовуємо нову базу даних
        stats = new_medical_db.get_monthly_payment_stats(month)
        return jsonify(convert_to_json_serializable(stats))
        
    except Exception as e:
        logger.error(f"Помилка отримання статистики за місяць {month}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/compare_treatments_payments', methods=['GET'])
def compare_treatments_payments():
    """API для порівняння лікування з оплатами"""
    try:
        # Використовуємо нову базу даних
        unit_filter = request.args.get('unit', '').strip()
        comparison_results = new_medical_db.compare_treatments_with_payments(unit_filter)
        return jsonify(convert_to_json_serializable(comparison_results))
    except Exception as e:
        logger.error(f"Помилка порівняння: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/advanced_search_treatments', methods=['POST'])
def advanced_search_treatments():
    """API для розширеного пошуку в даних лікування"""
    try:
        search_criteria = request.get_json()
        if not search_criteria:
            return jsonify({'error': 'Критерії пошуку не надані'}), 400
        
        # Використовуємо нову базу даних
        results = new_medical_db.advanced_search_patients(search_criteria)
        
        # Конвертуємо результати для JSON
        return jsonify(convert_to_json_serializable(results))
    except Exception as e:
        logger.error(f"Помилка розширеного пошуку: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/unpaid-stationary')
def unpaid_stationary():
    """Сторінка для генерації звіту по неоплаченим стаціонарам"""
    return render_template('unpaid_stationary.html')

@app.route('/api/unpaid_stationary_treatments', methods=['GET'])
def get_unpaid_stationary_treatments():
    """API для отримання неоплачених стаціонарних лікувань у форматі august_2025.xlsx"""
    try:
        month = request.args.get('month', type=int)
        year = request.args.get('year', type=int)
        start_month = request.args.get('start_month', type=int)
        end_month = request.args.get('end_month', type=int)
        unit_filter = request.args.get('unit', '2 БОП')
        include_hardcoded = request.args.get('include_hardcoded', 'true').lower() == 'true'
        
        # Отримуємо дані у форматі august_2025.xlsx
        august_data = new_medical_db.get_unpaid_stationary_august_format(
            month=month, 
            year=year,
            start_month=start_month,
            end_month=end_month,
            unit_filter=unit_filter,
            include_hardcoded=include_hardcoded
        )
        
        # Підраховуємо загальні показники
        total_patients = len(august_data)
        total_days = sum(item['Сумарна кількість днів лікування'] for item in august_data)
        
        results = {
            'total_patients': total_patients,
            'total_days': total_days,
            'patients': august_data,
            'month': month,
            'year': year,
            'unit_filter': unit_filter
        }
        
        return jsonify(convert_to_json_serializable(results))
    except Exception as e:
        logger.error(f"Помилка отримання неоплачених стаціонарів: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/test_excel', methods=['GET'])
def test_excel():
    """Тестовий endpoint для перевірки Excel генерації"""
    try:
        import pandas as pd
        from io import BytesIO
        from flask import send_file
        
        # Створюємо тестові дані
        test_data = [
            {'ПІБ': 'Тест', 'Звання': 'солдат', 'Підрозділ': '2 БОП', 'Дата початку': '2025-01-01', 'Дні': 5, 'Сума': '1000 грн'}
        ]
        
        df = pd.DataFrame(test_data)
        output = BytesIO()
        df.to_excel(output, sheet_name='Тест', index=False, engine='openpyxl')
        output.seek(0)
        
        return send_file(
            output,
            as_attachment=True,
            download_name='test.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logger.error(f"Помилка тестового Excel: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/generate_unpaid_excel', methods=['POST'])
def generate_unpaid_excel():
    """API для генерації Excel звіту по неоплаченим стаціонарам у форматі august_2025.xlsx"""
    try:
        data = request.get_json()
        month = data.get('month')
        year = data.get('year')
        start_month = data.get('start_month')
        end_month = data.get('end_month')
        unit_filter = data.get('unit', '2 БОП')
        
        # Отримуємо дані у форматі august_2025.xlsx
        logger.info(f"Параметри запиту: month={month}, year={year}, start_month={start_month}, end_month={end_month}, unit={unit_filter}")
        
        august_data = new_medical_db.get_unpaid_stationary_august_format(
            month=month, 
            year=year,
            start_month=start_month,
            end_month=end_month,
            unit_filter=unit_filter
        )
        
        logger.info(f"Отримано результатів у форматі august: {len(august_data)}")
        
        # Створюємо Excel файл
        from io import BytesIO
        import pandas as pd
        from datetime import datetime
        
        # Створюємо DataFrame
        logger.info(f"Створюємо DataFrame з {len(august_data)} записів")
        df = pd.DataFrame(august_data)
        logger.info(f"DataFrame створено успішно. Колонки: {list(df.columns)}")
        
        # Створюємо Excel файл в пам'яті
        output = BytesIO()
        try:
            logger.info("Починаємо створення Excel файлу...")
            
            # Створюємо Excel файл з автопідбором ширини колонок
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Неоплачені стаціонари', index=False)
                
                # Автопідбір ширини колонок
                worksheet = writer.sheets['Неоплачені стаціонари']
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    adjusted_width = min(max_length + 2, 50)
                    worksheet.column_dimensions[column_letter].width = adjusted_width
            
            logger.info("Excel файл створено успішно!")
            
        except Exception as e:
            logger.error(f"Помилка створення Excel файлу: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
        
        output.seek(0)
        
        # Формуємо назву файлу
        month_names = ['січень', 'лютий', 'березень', 'квітень', 'травень', 'червень', 
                      'липень', 'серпень', 'вересень', 'жовтень', 'листопад', 'грудень']
        
        if start_month and end_month:
            period_name = f"{month_names[start_month-1]}-{month_names[end_month-1]}"
        elif month:
            period_name = month_names[month-1]
        else:
            period_name = 'всі_місяці'
        
        # Використовуємо поточну дату без форматування
        current_time = datetime.now()
        timestamp = f"{current_time.year}{current_time.month:02d}{current_time.day:02d}_{current_time.hour:02d}{current_time.minute:02d}{current_time.second:02d}"
        filename = f'неоплачені_стаціонари_{period_name}_{year}_{timestamp}.xlsx'
        
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
    except Exception as e:
        logger.error(f"Помилка генерації Excel: {e}")
        return jsonify({'error': str(e)}), 500



@app.route('/excel_upload', methods=['GET'])
def excel_upload_page():
    """Сторінка завантаження Excel файлів"""
    return render_template('excel_upload.html')

@app.route('/api/upload_excel', methods=['POST'])
def upload_excel():
    """API endpoint для завантаження Excel файлу"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'Файл не знайдено'})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'Файл не вибрано'})
        
        if not (file.filename.lower().endswith('.xlsx') or file.filename.lower().endswith('.xls')):
            return jsonify({'success': False, 'error': 'Підтримуються тільки Excel файли (.xlsx, .xls)'})
        
        # Прапорець імпорту в БД
        import_to_db = (request.form.get('import_to_db') or '').lower() in ['true', '1', 'yes', 'on']

        # Зберігаємо файл
        import os
        import uuid
        from datetime import datetime
        
        upload_dir = 'uploads'
        if not os.path.exists(upload_dir):
            os.makedirs(upload_dir)
        
        # Генеруємо унікальне ім'я файлу
        file_id = str(uuid.uuid4())
        file_extension = os.path.splitext(file.filename)[1]
        filename = f"{file_id}{file_extension}"
        file_path = os.path.join(upload_dir, filename)
        
        file.save(file_path)
        
        # Обробляємо файл
        result = process_excel_file(file_path, file.filename)

        # Якщо потрібно, імпортуємо в БД
        if result.get('success') and import_to_db:
            try:
                from utils.new_medical_database import NewMedicalDatabase
                db = NewMedicalDatabase()
                import_summary = {'inserted': 0, 'updated': 0, 'skipped': 0}

                import pandas as pd
                df = pd.read_excel(file_path)
                df.columns = df.columns.astype(str).str.strip()

                # Простий імпорт як лікувань: створюємо пацієнта + діагноз + лікування, якщо вдається прочитати ключові поля
                for _, row in df.iterrows():
                    try:
                        surname = str(row.get('Прізвище', '')).strip()
                        name = str(row.get("Ім'я", '')).strip()
                        patronymic = str(row.get('По батькові', '')).strip()
                        full_name = ' '.join([p for p in [surname, name, patronymic] if p])
                        if not full_name:
                            import_summary['skipped'] += 1
                            continue

                        unit_name = str(row.get('Підрозділ', '')).strip() or None
                        rank = str(row.get('Військове звання', '')).strip() or None
                        patient_id = db._get_or_create_patient(full_name, rank, unit_name)

                        preliminary = str(row.get('Попередній діагноз', '')).strip() or None
                        final = str(row.get('Заключний діагноз', '')).strip() or None
                        is_combat = str(row.get('Бойова/ небойова', '')).strip().lower() == 'бойова'
                        diagnosis_id = db._get_or_create_diagnosis(preliminary, final, is_combat)

                        treatment_type = str(row.get('Вид лікування', '')).strip() or 'Стаціонар'
                        hospital_place = str(row.get('Місце госпіталізації', '')).strip() or None
                        primary_date = str(row.get('Дата надходження в поточний Л/З', '')).strip() or None
                        discharge_date = str(row.get('Дата виписки', '')).strip() or None
                        injury_date = None
                        treatment_days = None
                        treatment_result = str(row.get('Результат лікування', '')).strip() or None

                        cursor = db._get_connection().cursor()
                        cursor.execute(
                            """
                            INSERT INTO treatments (
                                patient_id, diagnosis_id, treatment_type, hospital_place,
                                primary_hospitalization_date, discharge_date, injury_date,
                                treatment_days, treatment_result, is_combat
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                patient_id, diagnosis_id, treatment_type, hospital_place,
                                primary_date, discharge_date, injury_date,
                                treatment_days, treatment_result, 1 if is_combat else 0
                            ),
                        )
                        db._get_connection().commit()
                        import_summary['inserted'] += 1
                    except Exception:
                        import_summary['skipped'] += 1
                        continue

                # Додаємо підсумок до details
                result.setdefault('details', {})
                result['details']['importSummary'] = import_summary
            except Exception as ie:
                result.setdefault('details', {})
                result['details']['importSummary'] = {'inserted': 0, 'updated': 0, 'skipped': 0}
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': 'Файл успішно оброблено',
                'details': result['details']
            })
        else:
            return jsonify({
                'success': False,
                'error': result['error']
            })
            
    except Exception as e:
        logger.error(f"Помилка завантаження Excel файлу: {e}")
        return jsonify({'success': False, 'error': str(e)})

def process_excel_file(file_path, original_filename):
    """
    Обробляє Excel файл і додає дані до бази даних
    
    Args:
        file_path (str): Шлях до файлу
        original_filename (str): Оригінальна назва файлу
        
    Returns:
        dict: Результат обробки
    """
    try:
        import pandas as pd
        from datetime import datetime
        import time
        
        start_time = time.time()
        
        # Читаємо Excel файл
        df = pd.read_excel(file_path)
        
        # Очищаємо назви колонок
        df.columns = df.columns.astype(str).str.strip()
        
        # Логуємо інформацію про файл
        logger.info(f"Обробка файлу: {original_filename}")
        logger.info(f"Розмір: {len(df)} записів, {len(df.columns)} колонок")
        logger.info(f"Колонки: {list(df.columns)}")
        
        # Підготовка огляду даних (без змін у БД)
        # Нормалізуємо дати в рядки, щоб уникнути NaT/JSON проблем
        import numpy as np
        from pandas.api.types import is_datetime64_any_dtype as is_dt
        sample_df = df.head(5).copy()
        for col in sample_df.columns:
            try:
                if is_dt(sample_df[col]):
                    sample_df[col] = sample_df[col].dt.strftime('%Y-%m-%d')
                # Якщо колонки схожі на дати за назвою, спробуємо сконвертувати для превʼю
                elif any(k in str(col).lower() for k in ['дата', 'date']):
                    coerced = pd.to_datetime(sample_df[col], errors='coerce')
                    sample_df[col] = coerced.dt.strftime('%Y-%m-%d')
            except Exception:
                # Якщо конвертація не вдалася, залишаємо як є
                pass
        # Замінюємо NaN/NaT на порожній рядок
        sample_rows = sample_df.replace({np.nan: ''}).to_dict(orient='records')
        
        # Евристичне визначення мапінгу колонок до полів БД
        column_names_lower = {c.lower(): c for c in df.columns}
        def pick(*options):
            for opt in options:
                if opt in column_names_lower:
                    return column_names_lower[opt]
            return None
        inferred_mapping = {
            'last_name': pick('прізвище', 'фамилия', 'прізвище/імʼя', 'surname', 'last name'),
            'first_name': pick('імʼя', 'имя', 'імя', 'name', 'first name'),
            'middle_name': pick('по батькові', 'по-батькові', 'отчество', 'middle name'),
            'unit': pick('підрозділ', 'подразделение', 'unit', 'рота', 'батальйон'),
            'diagnosis': pick('діагноз', 'диагноз', 'diagnosis'),
            'start_date': pick('дата початку', 'дата початок', 'date start', 'start date', 'from', 'з'),
            'end_date': pick('дата закінчення', 'дата кінець', 'date end', 'end date', 'to', 'по'),
            'hospital': pick('заклад', 'лікарня', 'больница', 'hospital'),
            'payment_status': pick('статус оплати', 'оплата', 'payment status')
        }
        
        # Пошук можливих колонок дат і базова валідація
        possible_date_cols = [c for c in df.columns if any(k in c.lower() for k in ['дата', 'date'])]
        date_parse_issues = {}
        for c in possible_date_cols:
            try:
                pd.to_datetime(df[c], errors='raise')
            except Exception as err:
                date_parse_issues[c] = str(err)[:200]
        
        # Прості числові колонки (для швидкої статистики)
        numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        def _safe_float(value):
            try:
                if value is None:
                    return None
                f = float(value)
                if np.isnan(f) or np.isinf(f):
                    return None
                return f
            except Exception:
                return None

        numeric_stats = {}
        for c in numeric_cols[:8]:  # обмежимо до 8 колонок для відповіді
            col = df[c]
            numeric_stats[c] = {
                'min': _safe_float(col.min() if len(col) else None),
                'max': _safe_float(col.max() if len(col) else None),
                'mean': _safe_float(col.mean() if len(col) else None),
                'nonNull': int(col.notna().sum()),
            }
        
        processing_time = time.time() - start_time
        
        return {
            'success': True,
            'details': {
                'records': len(df),
                'columns': len(df.columns),
                'processingTime': f"{processing_time:.2f}s",
                'filename': original_filename,
                'sampleRows': sample_rows,
                'inferredMapping': inferred_mapping,
                'possibleDateColumns': possible_date_cols,
                'dateParseIssues': date_parse_issues,
                'numericStats': numeric_stats
            }
        }
        
    except Exception as e:
        logger.error(f"Помилка обробки Excel файлу {original_filename}: {e}")
        return {
            'success': False,
            'error': str(e)
        }

@app.route('/api/import_treatments_excel', methods=['POST'])
def import_treatments_excel():
    """Імпорт лікувань з Excel з мапінгом колонок.
    Очікує multipart/form-data: file (excel), mapping (JSON string)
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Файл не надіслано'}), 400
        import json
        mapping_raw = request.form.get('mapping', '{}')
        mapping = json.loads(mapping_raw) if mapping_raw else {}
        f = request.files['file']
        import pandas as pd
        df = pd.read_excel(f)
        result = new_medical_db.import_treatments_generic(df, mapping)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Помилка імпорту лікувань: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/import_payments_excel', methods=['POST'])
def import_payments_excel():
    """Імпорт оплат з Excel з мапінгом колонок.
    Очікує multipart/form-data: file (excel), mapping (JSON string)
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Файл не надіслано'}), 400
        import json
        mapping_raw = request.form.get('mapping', '{}')
        mapping = json.loads(mapping_raw) if mapping_raw else {}
        f = request.files['file']
        import pandas as pd
        df = pd.read_excel(f)
        source_file = request.form.get('source_file') or f.filename
        result = new_medical_db.import_payments_generic(df, mapping, source_file)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Помилка імпорту оплат: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    try:
        app.run(debug=DEBUG)
    finally:
        # Закриваємо з'єднання з базою даних
        new_medical_db.close()