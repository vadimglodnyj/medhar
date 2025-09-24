#!/usr/bin/env python3
"""
Генератор службової характеристики
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

class ServiceCharacteristicGenerator(BaseDocumentGenerator):
    """Генератор службової характеристики"""
    
    def __init__(self):
        """Ініціалізація генератора службової характеристики"""
        template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates', 'service_characteristic_template.docx')
        super().__init__(template_path)
        
        # Ініціалізуємо базу даних
        self.db = PersonnelDatabase()
        if not self.db.load_data():
            logger.warning("Не вдалося завантажити базу даних персоналу")
    
    def prepare_data(self, form_data):
        """
        Підготовка даних для службової характеристики
        
        Args:
            form_data (dict): Дані з форми
            
        Returns:
            dict: Підготовлені дані для шаблону
        """
        context = {}
        
        # Основні дані
        context['pib_nazivnyi'] = form_data.get('pib_nazivnyi', '').strip()
        context['pib_rodovyi'] = form_data.get('pib_rodovyi', '').strip()
        context['birth_date'] = form_data.get('birth_date', '').strip()
        context['enlistment_date'] = form_data.get('enlistment_date', '').strip()
        
        # Військові дані
        context['zvanie'] = form_data.get('zvanie', '').strip()
        context['sluzhba_type'] = form_data.get('sluzhba_type', 'під час мобілізації').strip()
        
        # Пошук додаткової інформації в базі даних
        if context['pib_nazivnyi']:
            # Розбиваємо ПІБ для пошуку
            pib_parts = context['pib_nazivnyi'].split()
            if len(pib_parts) >= 1:
                surname = pib_parts[0]
                db_records = self.db.search_by_surname(surname)
                
                if db_records:
                    # Беремо перший знайдений запис
                    record = db_records[0]
                    logger.info(f"Знайдено запис в базі даних: {record}")
                    
                    # Додаємо дані з бази, якщо вони відсутні в формі
                    if not context['zvanie'] and 'Військове звання' in record:
                        context['zvanie'] = record['Військове звання']
                    
                    if not context['birth_date'] and 'Дата народження' in record:
                        birth_date = record['Дата народження']
                        if hasattr(birth_date, 'strftime'):
                            context['birth_date'] = birth_date.strftime('%d.%m.%Y')
                        else:
                            context['birth_date'] = str(birth_date)
        
        # Службова інформація
        context['current_date'] = datetime.now().strftime('%d.%m.%Y')
        context['current_year'] = datetime.now().year
        
        # Додаткові поля для службової характеристики
        context['service_period'] = self._calculate_service_period(context['enlistment_date'])
        context['performance_assessment'] = form_data.get('performance_assessment', 'добре')
        context['discipline_assessment'] = form_data.get('discipline_assessment', 'добре')
        context['additional_info'] = form_data.get('additional_info', '')
        
        logger.info(f"Підготовлено контекст для службової характеристики: {context}")
        return context
    
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
        Валідація даних для службової характеристики
        
        Args:
            form_data (dict): Дані для валідації
            
        Returns:
            tuple: (is_valid, error_message)
        """
        # Базова валідація
        is_valid, error = super().validate_data(form_data)
        if not is_valid:
            return is_valid, error
        
        # Додаткова валідація для службової характеристики
        if not form_data.get('zvanie', '').strip():
            return False, "Військове звання є обов'язковим для службової характеристики"
        
        return True, None
