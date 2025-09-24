#!/usr/bin/env python3
"""
Генератор рапорту на ВЛК (Військово-лікарську комісію)
"""

import os
import sys
import logging
from datetime import datetime

# Додаємо батьківську папку до шляху для імпорту
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.base_generator import BaseDocumentGenerator
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.database_reader import PersonnelDatabase

logger = logging.getLogger(__name__)

class VLKReportGenerator(BaseDocumentGenerator):
    """Генератор рапорту на ВЛК"""
    
    def __init__(self):
        """Ініціалізація генератора рапорту ВЛК"""
        template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates', 'vlk_report_template.docx')
        super().__init__(template_path)
        
        # Ініціалізуємо базу даних
        self.db = PersonnelDatabase()
        if not self.db.load_data():
            logger.warning("Не вдалося завантажити базу даних персоналу")
        
        if not self.db.load_commanders_data():
            logger.warning("Не вдалося завантажити базу даних командирів")
    
    def prepare_data(self, form_data):
        """
        Підготовка даних для рапорту ВЛК
        
        Args:
            form_data (dict): Дані з форми
            
        Returns:
            dict: Підготовлені дані для шаблону
        """
        context = {}
        
        # Основні дані солдата
        pib_nazivnyi = form_data.get('pib_nazivnyi', '').strip()
        
        # Пошук даних солдата в базі Personnel
        soldier_data = None
        if pib_nazivnyi:
            pib_parts = pib_nazivnyi.split()
            if len(pib_parts) >= 1:
                surname = pib_parts[0]
                db_records = self.db.search_by_surname(surname)
                
                if db_records:
                    soldier_data = db_records[0]
                    logger.info(f"Знайдено дані солдата: {soldier_data}")
        
        # Дані командирів з бази Commanders
        main_commander = self.db.get_main_commander()
        direct_commander = self.db.get_direct_commander()
        
        # Заповнюємо контекст для шаблону
        if main_commander:
            context['main_commander_rank'] = main_commander.get('Zvannia', '')
            # Форматуємо ім'я для лівого верхнього кута: Є.МОСПАН
            full_name = main_commander.get('Prizvyshche_Imya', '')
            context['main_commander_name'] = self._format_name_for_header(full_name)
            context['main_commander_rank_dav'] = main_commander.get('Zvannia dav', '')
            context['main_commander_name_dav'] = main_commander.get('Prizvyshche_Imya_davalniy', '')
        
        if direct_commander:
            context['direct_commander_rank'] = direct_commander.get('Zvannia', '')
            context['direct_commander_name'] = direct_commander.get('Prizvyshche_Imya', '')
            context['direct_commander_rank_dav'] = direct_commander.get('Zvannia dav', '')
            context['direct_commander_name_dav'] = direct_commander.get('Prizvyshche_Imya_davalniy', '')
        
        # Дані солдата
        if soldier_data:
            context['soldier_position'] = self.db.get_soldier_position(soldier_data)
            context['soldier_rank'] = soldier_data.get('Військове звання (фактичне)', '')
            context['soldier_name'] = self.db.get_soldier_name_only(soldier_data)
            context['soldier_full_name'] = self.db.get_soldier_full_name(soldier_data)
            
            # Для родового відмінку поки що використовуємо те, що ввів користувач
            context['soldier_rank_rodovuy'] = form_data.get('soldier_rank_rodovuy', context['soldier_rank'])
            context['soldier_full_name_rodovuy'] = form_data.get('soldier_full_name_rodovuy', context['soldier_full_name'])
        else:
            # Якщо не знайшли в базі, використовуємо дані з форми
            context['soldier_position'] = form_data.get('soldier_position', '')
            context['soldier_rank'] = form_data.get('soldier_rank', '')
            context['soldier_name'] = form_data.get('soldier_name', '')
            context['soldier_full_name'] = pib_nazivnyi
            context['soldier_rank_rodovuy'] = form_data.get('soldier_rank_rodovuy', '')
            context['soldier_full_name_rodovuy'] = form_data.get('soldier_full_name_rodovuy', '')
        
        # Тип документа та його деталі
        document_type = form_data.get('document_type', 'examination_result')
        
        if document_type == 'examination_result':
            context['document_description'] = "Копію результату огляду"
            context['document_number'] = form_data.get('examination_number', '').strip()
            # Додаємо інформацію про лікаря
            doctor_specialty = form_data.get('doctor_specialty', '').strip()
            doctor_name = form_data.get('doctor_name', '').strip()
            if doctor_specialty and doctor_name:
                context['document_description'] += f" {doctor_specialty} {doctor_name}"
        elif document_type == 'discharge_epicrisis':
            context['document_description'] = "Копію виписного епікризу"
            context['document_number'] = form_data.get('epicrisis_number', '').strip()
        elif document_type == 'vlk_decision':
            context['document_description'] = "Копію рішення ВЛК"
            context['document_number'] = form_data.get('vlk_decision_number', '').strip()
        
        # Дата та місце отримання документа
        context['document_date'] = form_data.get('document_date', '').strip()
        context['document_issuer'] = form_data.get('document_issuer', 'ДУ "ТМО МВС України по Запорізькій області"').strip()
        
        # Поточна дата (для дат, які будуть писатися вручну)
        context['current_date'] = datetime.now().strftime('%d.%m.%Y')
        
        logger.info(f"Підготовлено контекст для рапорту ВЛК: {context}")
        return context
    
    def _format_name_for_header(self, full_name):
        """
        Форматує повне ім'я для заголовка у форматі Є.МОСПАН
        
        Args:
            full_name (str): Повне ім'я (наприклад, "Євстахій МОСПАН")
            
        Returns:
            str: Відформатоване ім'я (наприклад, "Є.МОСПАН")
        """
        try:
            if not full_name or not full_name.strip():
                return ""
            
            # Розбиваємо на частини
            parts = full_name.strip().split()
            
            if len(parts) >= 2:
                # Беремо першу літеру імені та повне прізвище
                first_name = parts[0]
                surname = parts[1]
                
                # Беремо першу літеру імені
                first_letter = first_name[0] if first_name else ""
                
                # Формуємо Є.МОСПАН
                return f"{first_letter}.{surname}"
            else:
                # Якщо тільки одне слово, повертаємо як є
                return full_name
                
        except Exception as e:
            logger.error(f"Помилка при форматуванні імені '{full_name}': {e}")
            return full_name
    
    def _calculate_service_period(self, enlistment_date):
        """
        Розраховує період служби
        
        Args:
            enlistment_date (str): Дата призову
            
        Returns:
            str: Період служби
        """
        if not enlistment_date or enlistment_date == "з моменту призову":
            return "з моменту призову"
        
        try:
            # Парсимо дату
            if '.' in enlistment_date:
                day, month, year = map(int, enlistment_date.split('.'))
                enlistment = datetime(year, month, day)
                current = datetime.now()
                
                # Розраховуємо різницю
                delta = current - enlistment
                days = delta.days
                
                if days < 30:
                    return f"{days} днів"
                elif days < 365:
                    months = days // 30
                    return f"{months} місяців"
                else:
                    years = days // 365
                    months = (days % 365) // 30
                    if months > 0:
                        return f"{years} років {months} місяців"
                    else:
                        return f"{years} років"
            else:
                return f"з {enlistment_date} року"
                
        except Exception as e:
            logger.warning(f"Помилка розрахунку періоду служби: {e}")
            return f"з {enlistment_date} року"
    
    def validate_data(self, form_data):
        """
        Валідація даних для рапорту ВЛК
        
        Args:
            form_data (dict): Дані для валідації
            
        Returns:
            tuple: (is_valid, error_message)
        """
        # Базова валідація (тільки ПІБ в називному відмінку)
        if not form_data.get('pib_nazivnyi', '').strip():
            return False, "ПІБ в називному відмінку є обов'язковим"
        
        # Перевіряємо наявність даних командирів в базі
        main_commander = self.db.get_main_commander()
        direct_commander = self.db.get_direct_commander()
        
        if not main_commander:
            return False, "Дані головного командира не знайдено в базі даних"
        
        if not direct_commander:
            return False, "Дані безпосереднього командира не знайдено в базі даних"
        
        # Валідація типу документа та його деталей
        document_type = form_data.get('document_type', '')
        if not document_type:
            return False, "Оберіть тип документа"
        
        if document_type == 'examination_result':
            if not form_data.get('examination_number', '').strip():
                return False, "Номер огляду є обов'язковим"
            if not form_data.get('doctor_specialty', '').strip():
                return False, "Спеціальність лікаря є обов'язковою"
        elif document_type == 'discharge_epicrisis':
            if not form_data.get('epicrisis_number', '').strip():
                return False, "Номер виписного епікризу є обов'язковим"
        elif document_type == 'vlk_decision':
            if not form_data.get('vlk_decision_number', '').strip():
                return False, "Номер рішення ВЛК є обов'язковим"
        
        if not form_data.get('document_date', '').strip():
            return False, "Дата документа є обов'язковою"
        
        return True, None
