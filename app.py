#!/usr/bin/env python3
"""
Головний Flask додаток для генерації медичних документів
"""

import os
import sys
import pandas as pd
from flask import Flask, render_template, request, send_file, make_response, flash, redirect, url_for, jsonify
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
from utils.database_reader import PersonnelDatabase
from utils.circumstances_parser import parse_circumstances

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Глобальна змінна для кешування даних
treatments_cache = None
cache_timestamp = None

def load_treatments_data():
    """Завантажує дані з Excel файлів з кешуванням"""
    global treatments_cache, cache_timestamp
    
    # Перевіряємо чи потрібно оновити кеш (кожні 5 хвилин)
    if treatments_cache is not None and cache_timestamp is not None:
        if (datetime.now() - cache_timestamp).seconds < 300:  # 5 хвилин
            return treatments_cache
    
    try:
        logger.info("Завантаження даних з Excel файлів...")
        
        # Перевіряємо існування файлів
        if not os.path.exists(TREATMENTS_2025_FILE):
            raise FileNotFoundError(f"Файл {TREATMENTS_2025_FILE} не знайдено")
        if not os.path.exists(TREATMENTS_2024_FILE):
            raise FileNotFoundError(f"Файл {TREATMENTS_2024_FILE} не знайдено")
        
        logger.info("Файли Excel знайдено, починаємо завантаження...")
        
        df_current_year = pd.read_excel(TREATMENTS_2025_FILE)
        df_last_year = pd.read_excel(TREATMENTS_2024_FILE)
        
        # Використовуємо фінальний файл якщо він існує
        if os.path.exists(TREATMENTS_FINAL_FILE):
            logger.info("Використовуємо фінальний очищений файл...")
            treatments_df = pd.read_excel(TREATMENTS_FINAL_FILE)
            treatments_df.columns = treatments_df.columns.str.strip()
            logger.info(f"Завантажено фінальних записів: {len(treatments_df)}")
        else:
            # Fallback до старої логіки
            logger.info("Фінальний файл не знайдено, використовуємо окремі файли...")
            
            # Додаємо очищений файл якщо він існує, інакше адаптований
            df_additional = None
            if os.path.exists(TREATMENTS_CLEANED_FILE):
                df_additional = pd.read_excel(TREATMENTS_CLEANED_FILE)
                logger.info(f"Завантажено очищених записів: {len(df_additional)}")
            elif os.path.exists(TREATMENTS_ADAPTED_FILE):
                df_additional = pd.read_excel(TREATMENTS_ADAPTED_FILE)
                logger.info(f"Завантажено адаптованих записів: {len(df_additional)}")
            
            logger.info(f"Завантажено записів з 2024: {len(df_last_year)}")
            logger.info(f"Завантажено записів з 2025: {len(df_current_year)}")
            
            # Об'єднуємо всі файли
            dataframes = [df_last_year, df_current_year]
            if df_additional is not None:
                dataframes.append(df_additional)
            
            treatments_df = pd.concat(dataframes, ignore_index=True)
            treatments_df.columns = treatments_df.columns.str.strip()
        
        # Обробка дат
        date_columns = ['Дата надходження в поточний Л/З', 'Дата виписки', 'Дата народження']
        for col in date_columns:
            if col in treatments_df.columns:
                treatments_df[col] = pd.to_datetime(treatments_df[col], errors='coerce')
        
        # Обробка ПІБ
        for col in ['Прізвище', 'Ім\'я', 'По батькові']:
            if col in treatments_df.columns:
                treatments_df[col] = treatments_df[col].fillna('').astype(str)
        
        treatments_df['ПІБ'] = treatments_df['Прізвище'] + ' ' + treatments_df['Ім\'я'] + ' ' + treatments_df['По батькові']
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
        return "За час проходження військової служби не знаходився на стаціонарному або амбулаторному лікуванні у закладах Міністерства охорони здоров'я України та медичних територіальних об'єднань Міністерства внутрішніх справ України."

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
        return "За час проходження військової служби не знаходився на стаціонарному або амбулаторному лікуванні у закладах Міністерства охорони здоров'я України та медичних територіальних об'єднань Міністерства внутрішніх справ України."
    
    # Повертаємо список рядків; таб додаємо на початку для візуального відступу
    return ["\t" + line for line in history_lines]

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """API endpoint для отримання статистики бази даних"""
    try:
        treatments_df = load_treatments_data()
        
        stats = {
            'total_records': len(treatments_df),
            'unique_patients': treatments_df['ПІБ'].nunique() if 'ПІБ' in treatments_df.columns else 0,
            'columns': list(treatments_df.columns),
            'date_range': {
                'earliest': None,
                'latest': None
            }
        }
        
        # Знаходимо діапазон дат
        if 'Дата надходження в поточний Л/З' in treatments_df.columns:
            date_col = treatments_df['Дата надходження в поточний Л/З']
            valid_dates = date_col.dropna()
            if len(valid_dates) > 0:
                try:
                    stats['date_range']['earliest'] = valid_dates.min().strftime('%d.%m.%Y')
                    stats['date_range']['latest'] = valid_dates.max().strftime('%d.%m.%Y')
                except Exception as e:
                    logger.warning(f"Помилка при форматуванні дат: {e}")
                    stats['date_range']['earliest'] = "Невідомо"
                    stats['date_range']['latest'] = "Невідомо"
        
        return jsonify(stats)
        
    except Exception as e:
        logger.error(f"Помилка при отриманні статистики: {e}")
        return jsonify({'error': str(e)})

@app.route('/api/search_pib', methods=['GET'])
def search_pib():
    """API endpoint для пошуку ПІБ"""
    try:
        query = request.args.get('q', '').strip().lower()
        if len(query) < 2:
            return jsonify({'results': []})
        
        # Використовуємо PersonnelDatabase для пошуку
        db = PersonnelDatabase()
        if not db.load_data():
            return jsonify({'error': 'Не вдалося завантажити базу даних'})
        
        # Пошук за прізвищем
        matching_records = db.search_by_surname(query)
        
        # Обмежуємо до 10 результатів
        matching_records = matching_records[:10]
        
        results = []
        for record in matching_records:
            # Формуємо повний ПІБ
            surname = record.get('Прізвище', '')
            name = record.get('Ім\'я', '')
            patronymic = record.get('Ім\'я по батькові', '')
            full_pib = f"{surname} {name} {patronymic}".strip()
            
            # Формуємо підпис з додатковою інформацією
            rank = record.get('Військове звання (фактичне)', '')
            label = full_pib
            if rank:
                label += f" ({rank})"
            
            # Безпечно обробляємо дату народження
            birth_date = record.get('Дата народження', '')
            if pd.notna(birth_date) and birth_date != '':
                try:
                    # Перевіряємо чи це datetime об'єкт
                    if hasattr(birth_date, 'strftime'):
                        birth_date = birth_date.strftime('%Y-%m-%d')
                    else:
                        birth_date = str(birth_date)
                except:
                    birth_date = ''
            else:
                birth_date = ''
            
            results.append({
                'value': full_pib, 
                'label': label,
                'rank': rank,
                'birth_date': birth_date
            })
        
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
            context['treatment_history'] = format_treatment_history(soldier_records, hide_diagnosis_flag)
        else:
            context = {
                'zvanie': request.form.get('zvanie'),
                'sluzhba_type': request.form.get('sluzhba_type'),
                'birth_date': request.form.get('birth_date'),
            }
            context['treatment_history'] = "За час проходження військової служби не знаходився на стаціонарному або амбулаторному лікуванні у закладах Міністерства охорони здоров'я України та медичних територіальних об'єднань Міністерства внутрішніх справ України."
        
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

if __name__ == '__main__':
    app.run(debug=DEBUG)