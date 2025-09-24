#!/usr/bin/env python3
"""
Базовий клас для генераторів документів
"""

import os
import logging
from abc import ABC, abstractmethod
from docxtpl import DocxTemplate
import io
from flask import make_response, send_file

logger = logging.getLogger(__name__)

class BaseDocumentGenerator(ABC):
    """Базовий клас для всіх генераторів документів"""
    
    def __init__(self, template_path):
        """
        Ініціалізація генератора
        
        Args:
            template_path (str): Шлях до шаблону документа
        """
        self.template_path = template_path
        self.template = None
        
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"Шаблон не знайдено: {template_path}")
    
    def load_template(self):
        """Завантажує шаблон документа"""
        try:
            self.template = DocxTemplate(self.template_path)
            logger.info(f"✅ Шаблон завантажено: {self.template_path}")
            return True
        except Exception as e:
            logger.error(f"❌ Помилка завантаження шаблону: {e}")
            return False
    
    @abstractmethod
    def prepare_data(self, form_data):
        """
        Підготовка даних для шаблону
        
        Args:
            form_data (dict): Дані з форми
            
        Returns:
            dict: Підготовлені дані для шаблону
        """
        pass
    
    def generate_document(self, form_data, filename_prefix="Document"):
        """
        Генерація документа
        
        Args:
            form_data (dict): Дані з форми
            filename_prefix (str): Префікс для імені файлу
            
        Returns:
            Flask response: Відповідь з файлом
        """
        try:
            if not self.template:
                if not self.load_template():
                    raise Exception("Не вдалося завантажити шаблон")
            
            # Підготовка даних
            context = self.prepare_data(form_data)
            
            # Рендеринг документа
            self.template.render(context)
            
            # Створення файлу в пам'яті
            file_stream = io.BytesIO()
            self.template.save(file_stream)
            file_stream.seek(0)
            
            # Створення імені файлу
            filename = f"{filename_prefix}_{context.get('pib_nazivnyi', 'Unknown')}.docx"
            
            # Створення відповіді
            response = make_response(send_file(
                file_stream, 
                as_attachment=True,
                download_name=filename,
                mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            ))
            response.set_cookie('fileDownload', 'true', max_age=20)
            
            logger.info(f"✅ Документ згенеровано: {filename}")
            return response
            
        except Exception as e:
            logger.error(f"❌ Помилка генерації документа: {e}")
            raise
    
    def validate_data(self, form_data):
        """
        Валідація даних
        
        Args:
            form_data (dict): Дані для валідації
            
        Returns:
            tuple: (is_valid, error_message)
        """
        # Базова валідація
        required_fields = ['pib_nazivnyi', 'pib_rodovyi']
        
        for field in required_fields:
            if not form_data.get(field, '').strip():
                return False, f"Поле '{field}' є обов'язковим"
        
        return True, None

