#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Встановлення залежностей: лише requirements.txt (мінімум для python app.py).
На Windows зручніше: start_medchar.bat у корені проєкту.
"""

import sys
import os
import subprocess
import platform

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _req_path(filename: str) -> str:
    return os.path.join(BASE_DIR, filename)


def check_python_version():
    """Перевіряє версію Python"""
    if sys.version_info < (3, 8):
        print("❌ Потрібен Python 3.8 або новіший!")
        print(f"Поточна версія: {sys.version}")
        return False
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    return True


def install_from_requirements():
    path = _req_path("requirements.txt")
    if not os.path.isfile(path):
        print(f"❌ Не знайдено файл: {path}")
        return False
    print("📦 Встановлення з requirements.txt")
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                path,
                "--upgrade",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        print("✅ requirements.txt встановлено успішно")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Помилка pip: {e}")
        if e.stderr:
            print(e.stderr)
        return False


def check_system_requirements():
    print("🔍 Перевірка системних вимог...")
    print(f"🖥️  ОС: {platform.system()}")
    print(f"🏗️  Архітектура: {platform.machine()}")
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        print("✅ Git встановлено")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("⚠️  Git не знайдено (не обов'язково)")
    return True


def create_virtual_environment():
    print("🐍 Створення віртуального середовища...")
    if os.path.exists("venv"):
        print("✅ Віртуальне середовище вже існує")
        return True
    try:
        subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
        print("✅ Віртуальне середовище створено")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Помилка створення venv: {e}")
        return False


def main():
    print("🏥 ВСТАНОВЛЕННЯ МЕДХАР (лише мінімальні залежності)")
    print("=" * 50)

    if not check_python_version():
        return 1
    if not check_system_requirements():
        return 1
    if not create_virtual_environment():
        return 1

    try:
        print("🔄 Оновлення pip...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
            check=True,
            capture_output=True,
        )
        print("✅ pip оновлено")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Не вдалося оновити pip: {e}")

    if not install_from_requirements():
        return 1

    print("\n🎉 ГОТОВО")
    print("=" * 50)
    print("1. Покладіть Excel у папку data/ (поруч із app.py)")
    print("2. Запуск: start_medchar.bat  або  venv\\Scripts\\activate  →  python app.py")
    print("3. Браузер: http://127.0.0.1:5000/ — інструкція та форма")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
