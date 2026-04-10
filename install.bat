@echo off
echo 🏥 ВСТАНОВЛЕННЯ МЕДИЧНОГО ДОДАТКУ
echo ==================================================
echo.

REM Перевіряємо чи є Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Python не знайдено!
    echo Встановіть Python 3.8+ з https://python.org
    pause
    exit /b 1
)

echo ✅ Python знайдено
python --version

echo.
echo 📦 Встановлення залежностей...
echo.

REM Оновлюємо pip
echo 🔄 Оновлення pip...
python -m pip install --upgrade pip

REM Встановлюємо залежності
echo 📥 Встановлення пакетів...
python -m pip install -r requirements.txt

if %ERRORLEVEL% NEQ 0 (
    echo ❌ Помилка встановлення залежностей!
    pause
    exit /b 1
)

echo.
echo 🎉 ВСТАНОВЛЕННЯ ЗАВЕРШЕНО!
echo ==================================================
echo 📋 Наступні кроки:
echo 1. Додайте папку 'data/' з Excel файлами
echo 2. Запустіть додаток: python app.py
echo 3. Відкрийте браузер: http://127.0.0.1:5000
echo ==================================================
echo.

pause

