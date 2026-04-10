@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Легкий запуск: venv + лише requirements.txt при першому разі, потім одразу старт.

python --version >nul 2>&1
if errorlevel 1 (
    echo Не знайдено Python. Встановіть Python 3.8+ з https://www.python.org/downloads/
    echo У вікні інсталятора увімкніть "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo [MedChar] Створення віртуального середовища venv...
    python -m venv venv
    if errorlevel 1 (
        echo Не вдалося створити venv.
        pause
        exit /b 1
    )
)

REM Перевірка ядра: якщо чогось не вистачає — один раз ставимо requirements.txt
venv\Scripts\python.exe -c "import flask, docxtpl, pymorphy3" 2>nul
if errorlevel 1 (
    echo [MedChar] Перше встановлення залежностей ^(requirements.txt; 1–3 хв^)...
    venv\Scripts\python.exe -m pip install -q --upgrade pip
    venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Помилка pip. Спробуйте вручну: install.bat
        pause
        exit /b 1
    )
)

echo.
echo [MedChar] Відкрийте в браузері: http://127.0.0.1:5000/
echo [MedChar] Зупинка: Ctrl+C у цьому вікні
echo.
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5000/"
venv\Scripts\python.exe app.py
if errorlevel 1 pause
