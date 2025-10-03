# Структура проєкту МедХар

## Корінь проєкту

- `app.py` - головний Flask додаток
- `config.py` - конфігурація
- `README.md` - основна документація
- `requirements.txt` - залежності Python

## Організовані директорії

### 📁 docs/

**Документація та звіти**

- `EXCEL_UPLOAD_README.md` - документація завантаження Excel
- `VLK_FINAL_REPORT.md` - фінальний звіт ВЛК
- `VLK_REPORT_UPDATE.md` - оновлення звіту ВЛК
- `database/` - логи імпорту БД
  - `import_log.txt` - лог імпорту
  - `medical_import_log.txt` - лог медичного імпорту
- `migration/` - плани міграції
  - `DB_MIGRATION_PLAN.md` - план міграції з Excel на БД
- `scripts/` - документація скриптів

### 📁 database/

**База даних**

- `medical_new.db` - SQLite база даних

### 📁 import_scripts/

**Скрипти імпорту даних**

- `fix_bautin_payments.py` - виправлення платежів Баутіна
- `import_to_database.py` - імпорт в БД
- `import_to_medical_db.py` - імпорт в медичну БД

### 📁 startup_scripts/

**Скрипти запуску**

- `run_medchar.bat` - Windows batch запуск
- `run_medchar.ps1` - PowerShell запуск
- `start_medchar.bat` - альтернативний запуск
- `start_medchar.py` - Python запуск

### 📁 scripts/

**Допоміжні скрипти**

- `check_bautin_final.py` - перевірка Баутіна
- `check_tables.py` - перевірка таблиць БД
- `reorganize_project.py` - реорганізація проєкту
- `setup_onedrive_auth.py` - налаштування OneDrive
- `verify_db.py` - верифікація БД

### 📁 utils/

**Утиліти**

- `adapt_old_treatments.py` - адаптація старих лікувань
- `circumstances_parser.py` - парсер обставин
- `database_manager.py` - менеджер БД
- `database_reader.py` - читач БД
- `inspect_docx_format.py` - інспектор DOCX
- `medical_database.py` - медична БД
- `new_medical_database.py` - нова медична БД
- `pdf_parser.py` - парсер PDF
- `ukr.traineddata` - дані для OCR

### 📁 generators/

**Генератори документів**

- `base_generator.py` - базовий генератор
- `medical_payment_analyzer.py` - аналізатор медичних платежів
- `payment_analyzer.py` - аналізатор платежів
- `service_characteristic.py` - службова характеристика
- `vlk_report.py` - рапорт ВЛК

### 📁 templates/

**HTML шаблони**

- `base.html` - базовий шаблон
- `index.html` - головна сторінка
- `medical_characteristic.html` - медична характеристика
- `service_characteristic.html` - службова характеристика
- `vlk_report.html` - рапорт ВЛК
- `payment_reports.html` - звіти платежів
- `unpaid_stationary.html` - неоплачені стаціонари
- `excel_upload.html` - завантаження Excel
- `*_template.docx` - Word шаблони

### 📁 data/

**Вхідні дані Excel**

- `allsoldiers.xlsx` - всі солдати
- `treatments_*.xlsx` - лікування по місяцях
- `payment.xlsx` - платежі
- `old_treatments.xlsx` - старі лікування

### 📁 tests/

**Тестові дані**

- `data/` - тестові Excel файли

### 📁 uploads/

**Завантажені файли**

- Завантажені Excel файли користувачів

### 📁 output/

**Згенеровані документи**

- PDF виписки та звіти

### 📁 static/

**Статичні ресурси**

- `css/` - стилі
- `js/` - JavaScript
- `images/` - зображення
- `favicon.ico` - іконка

## Переваги організації

✅ **Чистота кореня** - тільки основні файли  
✅ **Логічне групування** - файли згруповані за призначенням  
✅ **Легка навігація** - зрозуміла структура  
✅ **Масштабованість** - легко додавати нові файли  
✅ **Документація** - всі MD файли в docs/

## Рекомендації

1. **Нові скрипти** → `scripts/` або `import_scripts/`
2. **Нова документація** → `docs/`
3. **Нові тести** → `tests/`
4. **Нові утиліти** → `utils/`
5. **Нові шаблони** → `templates/`

