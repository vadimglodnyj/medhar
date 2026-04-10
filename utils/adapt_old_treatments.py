#!/usr/bin/env python3
"""
Скрипт адаптації файлу old_treatments.xlsx під структуру treatments
Перетворює дані в формат, сумісний з медичною характеристикою
"""

import pandas as pd
import os
import sys
from datetime import datetime
import re

def clean_pib(pib_text):
    """Очищає та форматує ПІБ"""
    if pd.isna(pib_text) or not pib_text:
        return ""
    
    # Видаляємо номери телефонів та зайві символи
    pib_clean = re.sub(r'\d{10,}', '', str(pib_text))  # Видаляємо номери телефонів
    pib_clean = re.sub(r'\n+', ' ', pib_clean)  # Замінюємо переноси рядків на пробіли
    pib_clean = re.sub(r'\s+', ' ', pib_clean).strip()  # Нормалізуємо пробіли
    
    return pib_clean

def determine_treatment_type(row):
    """Визначає тип лікування на основі даних"""
    bd_combat = str(row.get('Бойова / небойова', '')).lower()
    place = str(row.get('Місце госпіталізації', '')).lower()
    
    # Якщо є дата поранення - це стабілізаційний пункт
    if pd.notna(row.get('Дата та час отримання поранення / травмування')):
        return 'стабілізаційний пункт'
    
    # Визначаємо тип на основі місця госпіталізації
    if 'медичний пункт' in place:
        return 'стабілізаційний пункт'
    elif 'шпиталь' in place or 'лікарня' in place:
        return 'стаціонар'
    elif 'санаторій' in place:
        return 'реабілітація'
    elif 'влк' in place:
        return 'влк'
    elif 'відпустка' in place:
        return 'відпустка'
    else:
        return 'стаціонар'  # За замовчуванням

def parse_dates(date_text):
    """Парсить дати з тексту"""
    if pd.isna(date_text) or not date_text:
        return None, None
    
    date_str = str(date_text)
    
    # Шукаємо дати в форматі дд.мм.рр або дд.мм.рррр
    date_pattern = r'(\d{1,2}\.\d{1,2}\.\d{2,4})'
    dates = re.findall(date_pattern, date_str)
    
    if dates:
        try:
            # Беремо першу дату як дату надходження
            start_date = dates[0]
            # Якщо є друга дата - це дата виписки
            end_date = dates[1] if len(dates) > 1 else None
            
            return start_date, end_date
        except:
            return None, None
    
    return None, None

def extract_circumstances(circumstances_text):
    """Витягує обставини поранення"""
    if pd.isna(circumstances_text) or not circumstances_text:
        return ""
    
    # Очищаємо текст
    circumstances = str(circumstances_text).strip()
    
    # Якщо текст занадто довгий, обрізаємо
    if len(circumstances) > 500:
        circumstances = circumstances[:500] + "..."
    
    return circumstances

def _extract_dates(text):
    """Повертає список дат у тексті (у порядку появи)."""
    if pd.isna(text) or not text:
        return []
    pattern = r"(\d{1,2}\.\d{1,2}\.\d{2,4})"
    return re.findall(pattern, str(text))

def determine_treatment_type_from_text(place_text):
    """Груба евристика визначення типу лікування за назвою закладу."""
    txt = (place_text or "").lower()
    if 'влк' in txt:
        return 'влк'
    if 'відпустк' in txt:
        return 'відпустка'
    if 'медичний пункт' in txt or 'медпункт' in txt or 'стабілізаці' in txt or 'лсб' in txt:
        return 'стабілізаційний пункт'
    if 'санатор' in txt or 'реабілітац' in txt:
        return 'реабілітація'
    if 'шпиталь' in txt or 'лікарн' in txt or 'вмкц' in txt or 'клінік' in txt:
        return 'стаціонар'
    return 'стаціонар'

def split_episodes(place_block, discharge_block):
    """
    Розбиває один запис з кількома зверненнями на епізоди.
    - place_block: рядок з послідовністю закладів та дат надходження
    - discharge_block: рядок з датами виписки

    Повертає список словників: [{ 'place': str, 'admit_date': str|None, 'discharge_date': str|None }]
    """
    if pd.isna(place_block) and pd.isna(discharge_block):
        return []

    lines = str(place_block or "").splitlines()
    episodes = []
    current_place_lines = []
    date_regex = re.compile(r"^\s*(\d{1,2}\.\d{1,2}\.\d{2,4})\s*$")

    def flush_episode(with_date):
        place_text = re.sub(r"\s+", " ", " ".join(current_place_lines).strip())
        # Пропускаємо сегменти, що стосуються ВЛК/відпустки
        if place_text and not any(k in place_text.lower() for k in ['влк', 'відпустк']):
            episodes.append({
                'place': place_text,
                'admit_date': with_date,
                'discharge_date': None,
            })

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        m = date_regex.match(line)
        if m:
            admit_date = m.group(1)
            flush_episode(admit_date)
            current_place_lines = []
        else:
            current_place_lines.append(line)

    # Якщо закінчили без дати, створюємо епізод без дати надходження
    if current_place_lines:
        flush_episode(None)

    # Призначаємо дати виписки по порядку
    discharges = _extract_dates(discharge_block)
    for i in range(min(len(episodes), len(discharges))):
        episodes[i]['discharge_date'] = discharges[i]

    return episodes

def adapt_old_treatments(input_file, output_file):
    """Адаптує старий файл під нову структуру"""
    
    print("🔄 АДАПТАЦІЯ ФАЙЛУ old_treatments.xlsx")
    print("=" * 60)
    
    # Читаємо вхідний файл
    print("📊 Завантаження файлу...")
    try:
        df = pd.read_excel(input_file)
        print(f"✅ Завантажено {len(df)} записів")
    except Exception as e:
        print(f"❌ Помилка завантаження: {e}")
        return False
    
    # Створюємо новий DataFrame з потрібною структурою
    print("🔧 Створення нової структури...")
    
    adapted_data = []
    
    for index, row in df.iterrows():
        if index % 1000 == 0:
            print(f"   Оброблено {index}/{len(df)} записів...")
        
        # Очищаємо ПІБ
        pib_clean = clean_pib(row.get('П.І.Б.', ''))
        if not pib_clean:
            continue  # Пропускаємо записи без ПІБ
        
        # Розбиваємо ПІБ на частини
        pib_parts = pib_clean.split()
        if len(pib_parts) < 2:
            continue  # Пропускаємо якщо ПІБ не повний
        
        surname = pib_parts[0] if len(pib_parts) > 0 else ""
        name = pib_parts[1] if len(pib_parts) > 1 else ""
        patronymic = pib_parts[2] if len(pib_parts) > 2 else ""
        
        # Розбиваємо на епізоди
        episodes = split_episodes(row.get('Місце госпіталізації', ''), row.get('Дата виписки', ''))

        # Якщо не вдалося розбити, падаємо назад на стару логіку однорядкового запису
        if not episodes:
            start_date, end_date = parse_dates(row.get('Місце госпіталізації', ''))
            if not start_date:
                start_date, _ = parse_dates(row.get('Дата та час отримання поранення / травмування', ''))
            if not end_date:
                end_date, _ = parse_dates(row.get('Дата виписки', ''))

            circumstances = extract_circumstances(row.get('Обставини та місце отримання поранення / травмування', ''))
            treatment_type = determine_treatment_type(row)

            record = {
                'Прізвище': surname,
                'Ім\'я': name,
                'По батькові': patronymic,
                'Дата народження': None,
                'Військове звання': row.get('Військове звання', ''),
                'Категорія': row.get('Категорія', ''),
                'Дата надходження в поточний Л/З': start_date,
                'Дата виписки': end_date,
                'Вид лікування': treatment_type,
                'Місце госпіталізації': row.get('Місце госпіталізації', ''),
                'Попередній діагноз': row.get('Діагноз', ''),
                'Обставини отримання поранення/ травмування': circumstances,
                'Заключення ВЛК': None,
                'Джерело': 'old_treatments'
            }
            adapted_data.append(record)
        else:
            circumstances = extract_circumstances(row.get('Обставини та місце отримання поранення / травмування', ''))
            for ep in episodes:
                place_text = ep['place']
                start_date = ep['admit_date']
                end_date = ep['discharge_date']
                treatment_type = determine_treatment_type_from_text(place_text)
                record = {
                    'Прізвище': surname,
                    'Ім\'я': name,
                    'По батькові': patronymic,
                    'Дата народження': None,
                    'Військове звання': row.get('Військове звання', ''),
                    'Категорія': row.get('Категорія', ''),
                    'Дата надходження в поточний Л/З': start_date,
                    'Дата виписки': end_date,
                    'Вид лікування': treatment_type,
                    'Місце госпіталізації': place_text,
                    'Попередній діагноз': row.get('Діагноз', ''),
                    'Обставини отримання поранення/ травмування': circumstances,
                    'Заключення ВЛК': None,
                    'Джерело': 'old_treatments'
                }
                adapted_data.append(record)
    
    print(f"✅ Створено {len(adapted_data)} адаптованих записів")
    
    # Створюємо новий DataFrame
    adapted_df = pd.DataFrame(adapted_data)
    
    # Видаляємо дублікати
    print("🔍 Видалення дублікатів...")
    initial_count = len(adapted_df)
    adapted_df = adapted_df.drop_duplicates(subset=['Прізвище', 'Ім\'я', 'По батькові', 'Дата надходження в поточний Л/З'])
    final_count = len(adapted_df)
    print(f"   Видалено {initial_count - final_count} дублікатів")
    
    # Зберігаємо результат
    print("💾 Збереження адаптованого файлу...")
    try:
        adapted_df.to_excel(output_file, index=False)
        print(f"✅ Файл збережено: {output_file}")
    except Exception as e:
        print(f"❌ Помилка збереження: {e}")
        return False
    
    # Статистика
    print("\n📊 СТАТИСТИКА АДАПТАЦІЇ:")
    print("-" * 40)
    print(f"Всього записів: {final_count}")
    print(f"Записів з датою надходження: {adapted_df['Дата надходження в поточний Л/З'].notna().sum()}")
    print(f"Записів з датою виписки: {adapted_df['Дата виписки'].notna().sum()}")
    print(f"Записів з діагнозом: {adapted_df['Попередній діагноз'].notna().sum()}")
    print(f"Записів з обставинами: {adapted_df['Обставини отримання поранення/ травмування'].notna().sum()}")
    
    # Статистика по типах лікування
    print("\n🏥 ТИПИ ЛІКУВАННЯ:")
    treatment_stats = adapted_df['Вид лікування'].value_counts()
    for treatment, count in treatment_stats.items():
        percentage = (count / final_count) * 100
        print(f"   • {treatment}: {count} ({percentage:.1f}%)")
    
    return True

def main():
    """Головна функція"""
    input_file = r"D:\medchar\data\old_treatments.xlsx"
    output_file = r"D:\medchar\data\treatments_adapted.xlsx"
    
    print("🔧 АДАПТАТОР ФАЙЛІВ ДЛЯ МЕДИЧНОЇ ХАРАКТЕРИСТИКИ")
    print("=" * 60)
    print(f"Час адаптації: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print()
    
    if not os.path.exists(input_file):
        print(f"❌ ВХІДНИЙ ФАЙЛ НЕ ЗНАЙДЕНО: {input_file}")
        return
    
    success = adapt_old_treatments(input_file, output_file)
    
    print("\n" + "=" * 60)
    if success:
        print("✅ АДАПТАЦІЯ ЗАВЕРШЕНА УСПІШНО")
        print(f"📁 Результат збережено в: {output_file}")
        print("\n💡 НАСТУПНІ КРОКИ:")
        print("   1. Перевірте адаптований файл")
        print("   2. Покладіть файл у папку data/ як treatments_YYYY.xlsx (рік у назві)")
        print("   3. Протестуйте генерацію медичних характеристик")
    else:
        print("❌ АДАПТАЦІЯ ЗАВЕРШЕНА З ПОМИЛКАМИ")
    print("=" * 60)

if __name__ == "__main__":
    main()
