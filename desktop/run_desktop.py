# -*- coding: utf-8 -*-
"""
Desktop-запуск Medhar: Flask у фоні + вікно pywebview (Edge WebView2).

Розробка (без перевстановлення програми):
  .\\desktop\\dev.ps1              # браузер + автоперезапуск на порті 17655
  .\\desktop\\dev.ps1 -Window      # те саме, але у вікні програми

Зібраний exe: Medhar.exe (entry = цей модуль).
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import socket
import sys
import threading
import time
import traceback

# Кореневе підключення репозиторію / бандла до sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from desktop.paths import (  # noqa: E402
    DESKTOP_HOST,
    desktop_mode,
    desktop_port,
    dev_mode,
    ensure_user_dirs,
    is_frozen,
    resource_root,
    user_data_root,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("medhar.desktop")


def _setup_file_logging() -> None:
    """У вікні немає консолі — пишемо лог у %LOCALAPPDATA%\\Medhar\\logs."""
    log_dir = os.path.join(user_data_root(), "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        return
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "medhar.log"),
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

# Seed JSON / example env з бандла → AppData (не перезаписуємо існуючі)
_SEED_DATA_FILES = (
    "combat_diagnosis_patterns.json",
    "lpz_list.json",
    "likar_specializations.json",
    "service_signatories.json",
    "vlk_signatories.json",
    "all_tcc_ukraine.json",
)


def _bootstrap_user_data() -> None:
    dirs = ensure_user_dirs()
    bundle_data = os.path.join(resource_root(), "data")
    user_data = dirs["data"]
    if os.path.isdir(bundle_data):
        for name in _SEED_DATA_FILES:
            src = os.path.join(bundle_data, name)
            dst = os.path.join(user_data, name)
            if os.path.isfile(src) and (name == "all_tcc_ukraine.json" or not os.path.isfile(dst)):
                try:
                    shutil.copy2(src, dst)
                    logger.info("Seed: %s", name)
                except OSError as e:
                    logger.warning("Не скопійовано %s: %s", name, e)

    # .env: з AppData; якщо немає — з бандла .env.example або порожній шаблон
    user_env = os.path.join(dirs["root"], ".env")
    if not os.path.isfile(user_env):
        example = os.path.join(resource_root(), ".env.example")
        try:
            if os.path.isfile(example):
                shutil.copy2(example, user_env)
            else:
                with open(user_env, "w", encoding="utf-8") as fh:
                    fh.write(
                        "# Medhar desktop — заповніть ключі Gemini / Dropbox\n"
                        "GEMINI_API_KEY=\n"
                        "GEMINI_MODEL=gemini-3.5-flash\n"
                        "MEDHAR_EXCEL_DIR=\n"
                        "TURSO_DATABASE_URL=\n"
                        "TURSO_AUTH_TOKEN=\n"
                        "DROPBOX_ACCESS_TOKEN=\n"
                        "DROPBOX_APP_KEY=\n"
                        "DROPBOX_APP_SECRET=\n"
                        "DROPBOX_REFRESH_TOKEN=\n"
                        "DROPBOX_ROOT_FOLDER=\n"
                    )
            logger.info("Створено .env у %s", user_env)
        except OSError as e:
            logger.warning("Не створено .env: %s", e)


def _wait_for_server(host: str, port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _run_flask() -> None:
    # Імпорт після bootstrap / env
    from app import app, _warmup_treatments_cache

    if dev_mode():
        # У вікні reloader неможливий, але шаблони й static мають підхоплюватись.
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
        app.jinja_env.auto_reload = True

    threading.Thread(target=_warmup_treatments_cache, daemon=True).start()
    # Без reloader і debug — інакше другий процес і блокування порту
    app.run(
        host=DESKTOP_HOST,
        port=desktop_port(),
        debug=False,
        use_reloader=False,
        threaded=True,
    )


def _run_dev_server(url: str) -> int:
    """
    Дев-режим: сервер із автоперезапуском у головному потоці плюс браузер.

    Зміни в .py перезапускають сервер, зміни в шаблонах/static видно після
    оновлення сторінки — перезбирати exe не треба.
    """
    from app import app, _warmup_treatments_cache

    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.jinja_env.auto_reload = True

    # Werkzeug перезапускає процес: браузер відкриваємо лише в батьківському,
    # а прогрів кешу робимо лише в робочому.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=_warmup_treatments_cache, daemon=True).start()
    elif not _use_window():
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    app.run(
        host=DESKTOP_HOST,
        port=desktop_port(),
        debug=True,
        use_reloader=True,
        threaded=True,
    )
    return 0


def _use_window() -> bool:
    """У дев-режимі вікно програми відкривається лише за MEDHAR_DEV_WINDOW=1."""
    if not dev_mode():
        return True
    flag = (os.environ.get("MEDHAR_DEV_WINDOW") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def main() -> int:
    os.environ["MEDHAR_DESKTOP"] = "1"
    _setup_file_logging()
    try:
        _bootstrap_user_data()
    except Exception:
        logger.exception("Bootstrap user data")

    port = desktop_port()
    url = f"http://{DESKTOP_HOST}:{port}/"
    logger.info(
        "Medhar desktop | frozen=%s | dev=%s | data=%s | url=%s",
        is_frozen(),
        dev_mode(),
        user_data_root(),
        url,
    )

    if dev_mode() and not _use_window():
        return _run_dev_server(url)

    flask_thread = threading.Thread(target=_run_flask, name="flask", daemon=True)
    flask_thread.start()

    if not _wait_for_server(DESKTOP_HOST, port):
        msg = (
            f"Не вдалося запустити локальний сервер на порті {port}.\n"
            "Можливо, порт зайнятий іншою програмою."
        )
        logger.error(msg)
        _show_error(msg)
        return 1

    try:
        from utils.journal_sync import start_daemon

        start_daemon()
    except Exception:
        logger.debug("Journal sync daemon not started", exc_info=True)

    try:
        import webview
    except ImportError:
        msg = (
            "Не встановлено pywebview.\n"
            "Виконайте: pip install pywebview\n"
            "На Windows потрібен Microsoft Edge WebView2 Runtime."
        )
        logger.error(msg)
        _show_error(msg)
        return 1

    # Без цього WebView2 мовчки блокує збереження DOCX/XLSX з програми.
    webview.settings["ALLOW_DOWNLOADS"] = True

    window = webview.create_window(
        "Медпункт батальйону — Medhar" + (" [DEV]" if dev_mode() else ""),
        url,
        width=1280,
        height=860,
        min_size=(900, 600),
    )
    webview.start()
    # Після закриття вікна daemon Flask-потік завершиться разом із процесом
    _ = window
    return 0


def _show_error(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "Medhar", 0x10)
    except Exception:
        print(message, file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        logger.error(tb)
        _show_error("Помилка запуску Medhar:\n\n" + tb[-1500:])
        raise SystemExit(1)
