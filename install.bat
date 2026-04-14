@echo off
chcp 65001 >nul
echo 🏥 Легке встановлення — лише ядро ^(для app.py^)
echo ==================================================
echo.

python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Python не знайдено!
    echo Встановіть Python 3.8+ з https://www.python.org ^(Add to PATH^)
    pause
    exit /b 1
)

echo ✅ Python знайдено
python --version

echo.
python -m pip install --upgrade pip
if %ERRORLEVEL% NEQ 0 (
    echo ⚠️ Не вдалося оновити pip, продовжуємо...
)

python -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Помилка встановлення requirements.txt!
    pause
    exit /b 1
)

echo.
echo ✅ Готово. Запуск: start_medchar.bat або python app.py
echo Відкрийте http://127.0.0.1:5000/ — інструкція та форма
echo ==================================================
pause
