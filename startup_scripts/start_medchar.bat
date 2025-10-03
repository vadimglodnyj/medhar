@echo off
echo 🏥 Запуск медичного додатку...
echo ==================================================
echo.

REM Перевіряємо чи є віртуальне середовище
if not exist "venv" (
    echo ❌ Віртуальне середовище не знайдено!
    echo Створіть віртуальне середовище: python -m venv venv
    pause
    exit /b 1
)

REM Перевіряємо чи є app.py
if not exist "app.py" (
    echo ❌ Файл app.py не знайдено!
    pause
    exit /b 1
)

echo 🚀 Запуск Flask додатку...
echo 📱 Відкрийте браузер: http://127.0.0.1:5000
echo ⏹️  Для зупинки натисніть Ctrl+C
echo ==================================================
echo.

REM Активуємо віртуальне середовище та запускаємо
call venv\Scripts\activate.bat
python app.py

pause

