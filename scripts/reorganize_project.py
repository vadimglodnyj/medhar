#!/usr/bin/env python3
"""
Скрипт для реорганізації структури проекту
"""

import os
import shutil
import logging

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def create_directories():
    """Створює необхідні папки"""
    directories = [
        'data',
        'templates',
        'static',
        'static/css',
        'static/js',
        'static/images',
        'utils',
        'tests',
        'docs',
        'backup',
        'temp'
    ]
    
    for directory in directories:
        if not os.path.exists(directory):
            os.makedirs(directory)
            logger.info(f"✅ Створено папку: {directory}")
        else:
            logger.info(f"📁 Папка вже існує: {directory}")

def move_files():
    """Переміщує файли в правильні папки"""
    
    # Список файлів для переміщення
    file_moves = [
        # База даних
        ('database.xlsx', 'data/database.xlsx'),
        ('treatments_2024.xlsx', 'data/treatments_2024.xlsx'),
        ('treatments_2025.xlsx', 'data/treatments_2025.xlsx'),
        
        # Шаблони документів
        ('template.docx', 'templates/medical_characteristic_template.docx'),
        ('sluzhbova_kharakterystyka_template.docx.docx', 'templates/service_characteristic_template.docx'),
        ('raport_vlk_template.docx.docx', 'templates/vlk_report_template.docx'),
        
        # Утиліти
        ('database_reader.py', 'utils/database_reader.py'),
    ]
    
    logger.info("📦 Переміщення файлів...")
    
    for source, destination in file_moves:
        if os.path.exists(source):
            # Створюємо папку призначення якщо не існує
            dest_dir = os.path.dirname(destination)
            if dest_dir and not os.path.exists(dest_dir):
                os.makedirs(dest_dir)
            
            # Переміщуємо файл
            shutil.move(source, destination)
            logger.info(f"✅ Переміщено: {source} → {destination}")
        else:
            logger.warning(f"⚠️ Файл не знайдено: {source}")

def clean_old_files():
    """Видаляє старі та невикористані файли"""
    
    files_to_remove = [
        'app.py',  # Старий додаток
        'setup_project_structure.py',
        'run_setup.py',
        'reorganize_project.py'
    ]
    
    directories_to_remove = [
        'uploads',  # Стара функціональність PDF
        'tessdata',  # OCR дані
        '__pycache__'
    ]
    
    logger.info("🧹 Очищення старих файлів...")
    
    # Видаляємо файли
    for file_path in files_to_remove:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"🗑️ Видалено файл: {file_path}")
            except Exception as e:
                logger.error(f"❌ Помилка видалення файлу {file_path}: {e}")
    
    # Видаляємо папки
    for dir_path in directories_to_remove:
        if os.path.exists(dir_path):
            try:
                shutil.rmtree(dir_path)
                logger.info(f"🗑️ Видалено папку: {dir_path}")
            except Exception as e:
                logger.error(f"❌ Помилка видалення папки {dir_path}: {e}")

def rename_main_app():
    """Перейменовує app_new.py в app.py"""
    if os.path.exists('app_new.py'):
        if os.path.exists('app.py'):
            os.remove('app.py')  # Видаляємо старий app.py
        os.rename('app_new.py', 'app.py')
        logger.info("✅ Перейменовано app_new.py → app.py")

def create_config_file():
    """Створює файл конфігурації"""
    config_content = '''# config.py
"""
Конфігурація проекту
"""

import os

# Шляхи до папок
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
TEMP_DIR = os.path.join(BASE_DIR, 'temp')

# Налаштування бази даних
DATABASE_FILE = os.path.join(DATA_DIR, 'database.xlsx')
TREATMENTS_2024_FILE = os.path.join(DATA_DIR, 'treatments_2024.xlsx')
TREATMENTS_2025_FILE = os.path.join(DATA_DIR, 'treatments_2025.xlsx')

# Шаблони документів
MEDICAL_CHARACTERISTIC_TEMPLATE = os.path.join(TEMPLATES_DIR, 'medical_characteristic_template.docx')
SERVICE_CHARACTERISTIC_TEMPLATE = os.path.join(TEMPLATES_DIR, 'service_characteristic_template.docx')
VLK_REPORT_TEMPLATE = os.path.join(TEMPLATES_DIR, 'vlk_report_template.docx')

# Налаштування Flask
SECRET_KEY = 'your-secret-key-here'
DEBUG = True
'''
    
    with open('config.py', 'w', encoding='utf-8') as f:
        f.write(config_content)
    logger.info("✅ Створено: config.py")

def create_gitignore():
    """Створює .gitignore файл"""
    gitignore_content = '''# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual Environment
venv/
env/
ENV/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Project specific
temp/
backup/
*.log
'''
    
    with open('.gitignore', 'w', encoding='utf-8') as f:
        f.write(gitignore_content)
    logger.info("✅ Створено: .gitignore")

def print_final_structure():
    """Виводить фінальну структуру проекту"""
    print("\n" + "="*60)
    print("🎉 РЕОРГАНІЗАЦІЯ ЗАВЕРШЕНА!")
    print("="*60)
    print("\n📁 Нова структура проекту:")
    print("""
medchar/
├── app.py                          # Головний Flask додаток
├── config.py                       # Конфігурація
├── requirements.txt                # Залежності
├── README.md                       # Документація
├── .gitignore                      # Git ignore
│
├── data/                           # База даних та Excel файли
│   ├── database.xlsx              # База даних персоналу
│   ├── treatments_2024.xlsx       # Лікування 2024
│   └── treatments_2025.xlsx       # Лікування 2025
│
├── templates/                      # Шаблони документів
│   ├── medical_characteristic_template.docx
│   ├── service_characteristic_template.docx
│   ├── vlk_report_template.docx
│   └── index.html                 # HTML шаблони
│
├── generators/                     # Генератори документів
│   ├── __init__.py
│   ├── base_generator.py
│   ├── service_characteristic.py
│   └── vlk_report.py
│
├── utils/                          # Допоміжні утиліти
│   └── database_reader.py
│
├── static/                         # Статичні файли
│   ├── css/
│   ├── js/
│   └── images/
│
├── tests/                          # Тести
├── docs/                           # Документація
├── backup/                         # Резервні копії
└── temp/                           # Тимчасові файли
""")

def main():
    """Основна функція реорганізації"""
    print("🏗️ Початок реорганізації структури проекту...")
    print("="*60)
    
    try:
        # 1. Створюємо папки
        create_directories()
        
        # 2. Переміщуємо файли
        move_files()
        
        # 3. Перейменовуємо головний додаток
        rename_main_app()
        
        # 4. Створюємо конфігураційні файли
        create_config_file()
        create_gitignore()
        
        # 5. Очищаємо старі файли
        clean_old_files()
        
        # 6. Виводимо результат
        print_final_structure()
        
        print("\n✅ Реорганізація завершена успішно!")
        print("📝 Наступні кроки:")
        print("   1. Оновити імпорти в app.py")
        print("   2. Створити HTML шаблони для форм")
        print("   3. Протестувати роботу додатку")
        
    except Exception as e:
        logger.error(f"❌ Помилка під час реорганізації: {e}")
        raise

if __name__ == "__main__":
    main()

