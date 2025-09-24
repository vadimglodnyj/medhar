#!/usr/bin/env python3
"""
Модуль для читання та роботи з базою даних Personnel
"""

import pandas as pd
import os
import logging

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PersonnelDatabase:
    """Клас для роботи з базою даних персоналу"""
    
    def __init__(self, database_path=None):
        """
        Ініціалізація бази даних
        
        Args:
            database_path (str): Шлях до файлу database.xlsx
        """
        if database_path is None:
            # Визначаємо шлях до бази даних відносно поточного файлу
            current_dir = os.path.dirname(os.path.abspath(__file__))
            parent_dir = os.path.dirname(current_dir)
            database_path = os.path.join(parent_dir, 'data', 'database.xlsx')
        
        self.database_path = database_path
        self.data = None
        self.commanders_data = None
        self.sheet_name = 'Personnel'
        self.commanders_sheet = 'Commanders'
        
    def load_data(self):
        """Завантажує дані з аркуша Personnel"""
        try:
            if not os.path.exists(self.database_path):
                raise FileNotFoundError(f"Файл {self.database_path} не знайдено")
            
            logger.info(f"Завантаження даних з {self.database_path}, аркуш '{self.sheet_name}'...")
            
            # Читаємо дані з аркуша Personnel
            self.data = pd.read_excel(self.database_path, sheet_name=self.sheet_name)
            
            # Очищаємо назви колонок від зайвих пробілів
            self.data.columns = self.data.columns.str.strip()
            
            logger.info(f"✅ Дані успішно завантажено. Записів: {len(self.data)}")
            logger.info(f"📋 Колонки: {list(self.data.columns)}")
            
            # Показуємо перші кілька записів для перевірки
            if len(self.data) > 0:
                logger.info("📄 Перші 3 записи:")
                for i, row in self.data.head(3).iterrows():
                    logger.info(f"  Запис {i+1}: {dict(row)}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Помилка при завантаженні даних: {e}")
            return False
    
    def load_commanders_data(self):
        """Завантажує дані з аркуша Commanders"""
        try:
            if not os.path.exists(self.database_path):
                raise FileNotFoundError(f"Файл {self.database_path} не знайдено")
            
            logger.info(f"Завантаження даних командирів з {self.database_path}, аркуш '{self.commanders_sheet}'...")
            
            # Читаємо дані з аркуша Commanders
            self.commanders_data = pd.read_excel(self.database_path, sheet_name=self.commanders_sheet)
            
            # Очищаємо назви колонок від зайвих пробілів
            self.commanders_data.columns = self.commanders_data.columns.str.strip()
            
            logger.info(f"✅ Дані командирів успішно завантажено. Записів: {len(self.commanders_data)}")
            logger.info(f"📋 Колонки командирів: {list(self.commanders_data.columns)}")
            
            # Показуємо перші кілька записів для перевірки
            if len(self.commanders_data) > 0:
                logger.info("📄 Перші 3 записи командирів:")
                for i, row in self.commanders_data.head(3).iterrows():
                    logger.info(f"  Запис {i+1}: {dict(row)}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Помилка при завантаженні даних командирів: {e}")
            return False
    
    def get_data_info(self):
        """Повертає інформацію про завантажені дані"""
        if self.data is None:
            return {"error": "Дані не завантажені"}
        
        info = {
            "total_records": len(self.data),
            "columns": list(self.data.columns),
            "data_types": dict(self.data.dtypes),
            "sample_data": self.data.head(2).to_dict('records') if len(self.data) > 0 else []
        }
        
        return info
    
    def search_by_surname(self, surname):
        """
        Пошук людей за прізвищем
        
        Args:
            surname (str): Прізвище для пошуку
            
        Returns:
            list: Список знайдених записів
        """
        if self.data is None:
            logger.error("Дані не завантажені. Спочатку викличте load_data()")
            return []
        
        if not surname or not surname.strip():
            logger.warning("Прізвище не вказано")
            return []
        
        surname = surname.strip().lower()
        logger.info(f"🔍 Пошук за прізвищем: '{surname}'")
        
        # Шукаємо колонки, які можуть містити прізвище
        surname_columns = []
        for col in self.data.columns:
            col_lower = col.lower()
            if any(keyword in col_lower for keyword in ['прізвище', 'surname', 'lastname', 'фамилия']):
                surname_columns.append(col)
        
        if not surname_columns:
            logger.warning("Не знайдено колонок з прізвищами")
            return []
        
        logger.info(f"📋 Колонки для пошуку прізвищ: {surname_columns}")
        
        # Виконуємо пошук
        found_records = []
        
        for col in surname_columns:
            # Створюємо маску для пошуку (нечутливий до регістру)
            mask = self.data[col].astype(str).str.lower().str.contains(surname, na=False)
            matches = self.data[mask]
            
            if len(matches) > 0:
                logger.info(f"✅ Знайдено {len(matches)} записів у колонці '{col}'")
                found_records.extend(matches.to_dict('records'))
        
        # Видаляємо дублікати
        unique_records = []
        seen = set()
        for record in found_records:
            # Створюємо унікальний ключ для запису
            record_key = str(record)
            if record_key not in seen:
                seen.add(record_key)
                unique_records.append(record)
        
        logger.info(f"🎯 Всього знайдено унікальних записів: {len(unique_records)}")
        
        return unique_records
    
    def get_all_surnames(self):
        """Повертає всі унікальні прізвища з бази даних"""
        if self.data is None:
            logger.error("Дані не завантажені")
            return []
        
        surnames = set()
        
        # Шукаємо колонки з прізвищами
        for col in self.data.columns:
            col_lower = col.lower()
            if any(keyword in col_lower for keyword in ['прізвище', 'surname', 'lastname', 'фамилия']):
                # Додаємо всі непусті значення
                col_surnames = self.data[col].dropna().astype(str).str.strip()
                col_surnames = col_surnames[col_surnames != '']
                surnames.update(col_surnames)
        
        return sorted(list(surnames))
    
    def get_commanders_data(self):
        """Повертає дані командирів"""
        if self.commanders_data is None:
            logger.warning("Дані командирів не завантажені. Викличте load_commanders_data() спочатку")
            return None
        return self.commanders_data
    
    def get_main_commander(self):
        """Повертає дані головного командира"""
        if self.commanders_data is None:
            logger.warning("Дані командирів не завантажені")
            return None
        
        # Шукаємо запис з main\direct = 'main'
        main_commander = self.commanders_data[
            self.commanders_data['main\\direct'].str.lower() == 'main'
        ]
        
        if len(main_commander) > 0:
            return main_commander.iloc[0].to_dict()
        else:
            logger.warning("Головний командир не знайдено")
            return None
    
    def get_direct_commander(self):
        """Повертає дані безпосереднього командира"""
        if self.commanders_data is None:
            logger.warning("Дані командирів не завантажені")
            return None
        
        # Шукаємо запис з main\direct = 'direct'
        direct_commander = self.commanders_data[
            self.commanders_data['main\\direct'].str.lower() == 'direct'
        ]
        
        if len(direct_commander) > 0:
            return direct_commander.iloc[0].to_dict()
        else:
            logger.warning("Безпосередній командир не знайдено")
            return None
    
    def get_soldier_position(self, soldier_data):
        """
        Формує посаду солдата з комбінації полів
        
        Args:
            soldier_data (dict): Дані солдата з бази Personnel
            
        Returns:
            str: Сформована посада
        """
        try:
            position = soldier_data.get('Посада', '').strip()
            podrozdil_4 = soldier_data.get('Підрозділ 4', '').strip()
            podrozdil_3 = soldier_data.get('Підрозділ 3', '').strip()
            
            # Формуємо посаду
            parts = [position, podrozdil_4, podrozdil_3, "2-го батальйону оперативного призначення"]
            parts = [part for part in parts if part]  # Видаляємо пусті частини
            
            return " ".join(parts)
            
        except Exception as e:
            logger.error(f"Помилка при формуванні посади: {e}")
            return soldier_data.get('Посада', '')
    
    def get_soldier_name_only(self, soldier_data):
        """
        Повертає тільки Прізвище Ім'я (без по батькові)
        
        Args:
            soldier_data (dict): Дані солдата з бази Personnel
            
        Returns:
            str: Прізвище Ім'я
        """
        try:
            surname = soldier_data.get('Прізвище', '').strip()
            name = soldier_data.get('Ім\'я', '').strip()
            
            return f"{surname} {name}".strip()
            
        except Exception as e:
            logger.error(f"Помилка при формуванні імені: {e}")
            return ""
    
    def get_soldier_full_name(self, soldier_data):
        """
        Повертає повний ПІБ в називному відмінку
        
        Args:
            soldier_data (dict): Дані солдата з бази Personnel
            
        Returns:
            str: Повний ПІБ
        """
        try:
            surname = soldier_data.get('Прізвище', '').strip()
            name = soldier_data.get('Ім\'я', '').strip()
            patronymic = soldier_data.get('Ім\'я по батькові', '').strip()
            
            return f"{surname} {name} {patronymic}".strip()
            
        except Exception as e:
            logger.error(f"Помилка при формуванні повного ПІБ: {e}")
            return ""

def main():
    """Основна функція для тестування"""
    print("🚀 Тестування читання бази даних Personnel")
    print("=" * 50)
    
    # Створюємо екземпляр бази даних
    db = PersonnelDatabase()
    
    # Завантажуємо дані
    if not db.load_data():
        print("❌ Не вдалося завантажити дані")
        return
    
    # Показуємо інформацію про дані
    print("\n📊 Інформація про дані:")
    info = db.get_data_info()
    print(f"  Загальна кількість записів: {info['total_records']}")
    print(f"  Колонки: {info['columns']}")
    
    # Тестуємо пошук за прізвищем
    print("\n🔍 Тестування пошуку за прізвищем:")
    
    # Отримуємо всі прізвища для тестування
    all_surnames = db.get_all_surnames()
    print(f"  Знайдено {len(all_surnames)} унікальних прізвищ")
    
    if all_surnames:
        # Тестуємо пошук для перших 3 прізвищ
        test_surnames = all_surnames[:3]
        for surname in test_surnames:
            print(f"\n  Пошук: '{surname}'")
            results = db.search_by_surname(surname)
            print(f"    Знайдено записів: {len(results)}")
            
            if results:
                print(f"    Перший результат: {results[0]}")
    
    print("\n✅ Тестування завершено!")

if __name__ == "__main__":
    main()
