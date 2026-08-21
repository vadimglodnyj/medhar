# -*- coding: utf-8 -*-
"""
Спільний зашифрований пакет команди (Turso / Gemini / Dropbox).

Файл data/team.vault пакується в Install.exe. На ПК колеги розшифровується
спільним PIN і зберігається в %LOCALAPPDATA%\\Medhar\\ (не треба правити .env).
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

VAULT_FILENAME = "team.vault"
SESSION_FILENAME = "session.json"
TEAM_ENV_FILENAME = "team.env"

# Ключі, які входять у спільний пакет команди.
TEAM_SECRET_KEYS = (
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "TURSO_DATABASE_URL",
    "TURSO_AUTH_TOKEN",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_RECIPIENT",
    "WHATSAPP_API_VERSION",
    "DROPBOX_ACCESS_TOKEN",
    "DROPBOX_APP_KEY",
    "DROPBOX_APP_SECRET",
    "DROPBOX_REFRESH_TOKEN",
    "DROPBOX_ROOT_FOLDER",
    # Відносний шлях під коренем Dropbox: Програми/patientss/data
    "MEDHAR_EXCEL_SUBPATH",
)

_KDF_ITERATIONS = 390_000
_SALT_LEN = 16
_NONCE_LEN = 12


def vault_path(resource_root: str) -> str:
    return os.path.join(resource_root, "data", VAULT_FILENAME)


def session_path(user_data_root: str) -> str:
    return os.path.join(user_data_root, SESSION_FILENAME)


def team_env_path(user_data_root: str) -> str:
    return os.path.join(user_data_root, TEAM_ENV_FILENAME)


def vault_exists(resource_root: str) -> bool:
    return os.path.isfile(vault_path(resource_root))


def _derive_key(pin: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_KDF_ITERATIONS,
    )
    return kdf.derive((pin or "").encode("utf-8"))


def encrypt_secrets(secrets: dict, pin: str) -> dict:
    """Повертає JSON-структуру vault (сіль + ciphertext)."""
    clean = {k: str(secrets.get(k) or "").strip() for k in TEAM_SECRET_KEYS}
    payload = json.dumps(clean, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    salt = os.urandom(_SALT_LEN)
    key = _derive_key(pin, salt)
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, payload, None)
    return {
        "v": 1,
        "kdf": "pbkdf2-sha256",
        "iterations": _KDF_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_vault(vault: dict, pin: str) -> dict:
    """Розшифровує vault. ValueError при невірному PIN або пошкодженому файлі."""
    if not isinstance(vault, dict) or int(vault.get("v") or 0) != 1:
        raise ValueError("Невідомий формат пакету команди")
    try:
        salt = base64.b64decode(vault["salt"])
        nonce = base64.b64decode(vault["nonce"])
        ciphertext = base64.b64decode(vault["ciphertext"])
        iterations = int(vault.get("iterations") or _KDF_ITERATIONS)
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError("Пошкоджений пакет команди") from e

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    key = kdf.derive((pin or "").encode("utf-8"))
    try:
        raw = AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as e:
        raise ValueError("Невірний PIN") from e
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Пошкоджений пакет команди")
    return {k: str(data.get(k) or "").strip() for k in TEAM_SECRET_KEYS}


def load_vault_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_vault_file(path: str, vault: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(vault, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def secrets_from_env_map(env: dict) -> dict:
    """Збирає секрети з словника .env; SUBPATH виводить з MEDHAR_EXCEL_DIR."""
    out = {k: str(env.get(k) or "").strip() for k in TEAM_SECRET_KEYS}
    excel = str(env.get("MEDHAR_EXCEL_DIR") or "").strip().strip('"')
    if excel and not out.get("MEDHAR_EXCEL_SUBPATH"):
        out["MEDHAR_EXCEL_SUBPATH"] = excel_subpath_from_absolute(excel)
    return out


def excel_subpath_from_absolute(path: str) -> str:
    """D:\\Dropbox\\Програми\\patientss\\data → Програми/patientss/data"""
    text = (path or "").replace("/", "\\").strip().strip('"')
    m = re.search(r"(?i)(?:^|\\)Dropbox\\(.+)$", text)
    if m:
        return m.group(1).replace("\\", "/").strip("/")
    # якщо вже відносний
    return text.replace("\\", "/").strip("/")


def find_dropbox_root() -> Optional[str]:
    """Шлях до локальної папки Dropbox (info.json або типові диски)."""
    info = os.path.join(
        os.environ.get("LOCALAPPDATA") or "",
        "Dropbox",
        "info.json",
    )
    if os.path.isfile(info):
        try:
            with open(info, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for key in ("personal", "business"):
                block = data.get(key) or {}
                path = block.get("path")
                if path and os.path.isdir(path):
                    return path
        except (OSError, ValueError, TypeError):
            pass
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        candidate = f"{letter}:\\Dropbox"
        if os.path.isdir(candidate):
            return candidate
    return None


def resolve_excel_dir(subpath: str) -> Optional[str]:
    """Повний шлях до спільних Excel за відносним шляхом під Dropbox."""
    sub = (subpath or "").replace("\\", "/").strip().strip("/")
    if not sub:
        return None
    root = find_dropbox_root()
    if root:
        full = os.path.normpath(os.path.join(root, *sub.split("/")))
        if os.path.isdir(full):
            return full
    # запасний перебір дисків
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        full = os.path.normpath(
            os.path.join(f"{letter}:\\Dropbox", *sub.split("/"))
        )
        if os.path.isdir(full):
            return full
    if root:
        return os.path.normpath(os.path.join(root, *sub.split("/")))
    return None


def write_team_env(path: str, secrets: dict, excel_dir: str = "") -> None:
    lines = [
        "# Згенеровано Medhar після входу в режим команди. Не редагуйте вручну.",
        "MEDHAR_MODE=team",
    ]
    for key in TEAM_SECRET_KEYS:
        if key == "MEDHAR_EXCEL_SUBPATH":
            continue
        lines.append(f"{key}={secrets.get(key) or ''}")
    if excel_dir:
        lines.append(f"MEDHAR_EXCEL_DIR={excel_dir}")
    elif secrets.get("MEDHAR_EXCEL_SUBPATH"):
        lines.append(f"MEDHAR_EXCEL_SUBPATH={secrets['MEDHAR_EXCEL_SUBPATH']}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_session(path: str, mode: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"mode": mode, "v": 1}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def read_session(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        mode = str((data or {}).get("mode") or "").strip().lower()
        if mode in ("local", "team"):
            return mode
    except (OSError, ValueError, TypeError):
        return None
    return None


def clear_team_files(user_data_root: str) -> None:
    for name in (TEAM_ENV_FILENAME,):
        path = os.path.join(user_data_root, name)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
