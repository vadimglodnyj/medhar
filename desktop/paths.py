# -*- coding: utf-8 -*-
"""Шляхи для desktop / PyInstaller (frozen) vs розробка."""

from __future__ import annotations

import os
import sys


APP_NAME = "Medhar"
DESKTOP_PORT = 17654
# Окремий порт, щоб дев-запуск не конфліктував зі встановленою програмою.
DESKTOP_DEV_PORT = 17655
DESKTOP_HOST = "127.0.0.1"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def dev_mode() -> bool:
    """True для запуску з вихідного коду з автоперезавантаженням (MEDHAR_DEV=1)."""
    if is_frozen():
        return False
    flag = (os.environ.get("MEDHAR_DEV") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def desktop_port() -> int:
    return DESKTOP_DEV_PORT if dev_mode() else DESKTOP_PORT


def desktop_mode() -> bool:
    """True якщо запущено як desktop (env) або як зібраний exe."""
    if is_frozen():
        return True
    flag = (os.environ.get("MEDHAR_DESKTOP") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def resource_root() -> str:
    """
    Корінь read-only ресурсів (шаблони, static, seed data у бандлі).
    PyInstaller onedir: sys._MEIPASS (часто _internal).
    """
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    # desktop/paths.py → repo root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_data_root() -> str:
    """
    Записуваний корінь: %LOCALAPPDATA%\\Medhar у desktop/frozen,
    інакше корінь репозиторію.
    """
    if desktop_mode():
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(local, APP_NAME)
    return resource_root()


def ensure_user_dirs() -> dict:
    """Створює data/, temp/ тощо. Повертає шляхи."""
    root = user_data_root()
    data = os.path.join(root, "data")
    temp = os.path.join(root, "temp")
    payments = os.path.join(data, "payments")
    media = os.path.join(data, "treatment_media")
    # Кастомні DOCX-шаблони користувача (мають пріоритет над бандлом).
    templates = os.path.join(root, "templates")
    for path in (root, data, temp, payments, media, templates):
        os.makedirs(path, exist_ok=True)
    return {
        "root": root,
        "data": data,
        "temp": temp,
        "payments": payments,
        "media": media,
        "templates": templates,
    }
