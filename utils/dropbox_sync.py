# -*- coding: utf-8 -*-
"""Завантаження медіа пацієнтів у Dropbox."""

from __future__ import annotations

import logging
import re
import threading
from typing import Optional

from config import (
    DROPBOX_ACCESS_TOKEN,
    DROPBOX_APP_KEY,
    DROPBOX_APP_SECRET,
    DROPBOX_REFRESH_TOKEN,
    DROPBOX_ROOT_FOLDER,
)

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()


class DropboxAuthError(RuntimeError):
    """Токен недійсний або протермінований."""


def dropbox_configured() -> bool:
    if DROPBOX_REFRESH_TOKEN and DROPBOX_APP_KEY:
        return True
    return bool(DROPBOX_ACCESS_TOKEN)


def uses_refresh_token() -> bool:
    """True якщо налаштовано постійний доступ (refresh token)."""
    return bool(DROPBOX_REFRESH_TOKEN and DROPBOX_APP_KEY)


def _safe_path_segment(value: str, fallback: str = "item") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r'[<>:"|?*\x00-\x1f]+', "", text)
    text = text.replace("/", "-").replace("\\", "-").strip(" .")
    return text[:120] or fallback


def patient_media_dropbox_path(
    *,
    pib: str,
    treatment_title: str,
    treatment_id: int,
    filename: str,
) -> str:
    """
    App folder (root порожній):
      API: /patients/{ПІБ}/{лікування}/файл
      Windows: D:\\Dropbox\\Програми\\patientss\\patients\\{ПІБ}\\...
    Full Dropbox (root=м-с): /м-с/patients/{ПІБ}/{лікування}/файл
    """
    root = (DROPBOX_ROOT_FOLDER or "").strip().strip("/")
    patient = _safe_path_segment(pib, "patient")
    title = _safe_path_segment(treatment_title, "treatment")
    treatment_folder = f"{title} (#{int(treatment_id)})"
    file_name = _safe_path_segment(filename, "file")
    if "." not in file_name and "." in (filename or ""):
        ext = filename.rsplit(".", 1)[-1]
        file_name = f"{file_name}.{ext}"
    parts = []
    if root:
        parts.append(root)
    parts.extend(["patients", patient, treatment_folder, file_name])
    return "/" + "/".join(parts)


def _build_client():
    """Клієнт Dropbox: refresh token (постійний) або access token (тимчасовий)."""
    try:
        import dropbox
    except ImportError as e:
        raise RuntimeError(
            "Не встановлено пакет dropbox. Виконайте: pip install dropbox"
        ) from e

    if uses_refresh_token():
        return dropbox.Dropbox(
            oauth2_refresh_token=DROPBOX_REFRESH_TOKEN,
            app_key=DROPBOX_APP_KEY,
            app_secret=DROPBOX_APP_SECRET or None,
        )
    return dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)


def get_client(*, force_new: bool = False):
    global _client
    if _client is not None and not force_new:
        return _client
    with _client_lock:
        if _client is None or force_new:
            _client = _build_client()
        return _client


def _auth_hint() -> str:
    if uses_refresh_token():
        return (
            "Перевірте DROPBOX_APP_KEY / DROPBOX_APP_SECRET / DROPBOX_REFRESH_TOKEN у .env"
        )
    return (
        "Токен DROPBOX_ACCESS_TOKEN протермінований (sl.u.… живе ~4 години). "
        "Налаштуйте постійний доступ: DROPBOX_APP_KEY + DROPBOX_APP_SECRET + DROPBOX_REFRESH_TOKEN"
    )


def check_connection() -> dict:
    """Діагностика: {ok, account, error, permanent}."""
    result = {
        "configured": dropbox_configured(),
        "permanent": uses_refresh_token(),
        "ok": False,
        "account": "",
        "error": "",
    }
    if not result["configured"]:
        result["error"] = "Не задано DROPBOX_ACCESS_TOKEN (або refresh token) у .env"
        return result
    try:
        account = get_client().users_get_current_account()
        result["ok"] = True
        result["account"] = getattr(account, "email", "") or getattr(
            getattr(account, "name", None), "display_name", ""
        )
    except Exception as e:
        if _is_auth_error(e):
            result["error"] = f"Помилка авторизації Dropbox. {_auth_hint()}"
        else:
            result["error"] = str(e)
    return result


def _is_auth_error(exc: Exception) -> bool:
    try:
        from dropbox.exceptions import AuthError
    except ImportError:
        AuthError = ()  # noqa: N806
    if AuthError and isinstance(exc, AuthError):
        return True
    text = str(exc).casefold()
    return any(
        token in text
        for token in (
            "expired_access_token",
            "invalid_access_token",
            "missing_scope",
            "refresh token",
            "unauthorized",
        )
    )


def upload_bytes_to_dropbox(
    data: bytes,
    dropbox_path: str,
    *,
    mute: bool = True,
) -> Optional[str]:
    """
    Завантажує файл у Dropbox (overwrite).
    Повертає шлях у Dropbox або None, якщо Dropbox не налаштовано.
    Кидає DropboxAuthError, якщо токен протермінований.
    """
    if not dropbox_configured():
        return None
    if not data:
        raise ValueError("Порожній файл для Dropbox")
    path = "/" + str(dropbox_path or "").lstrip("/")

    from dropbox.files import WriteMode

    try:
        # створити батьківські папки не обов'язково — upload створює шлях
        get_client().files_upload(data, path, mode=WriteMode.overwrite, mute=mute)
    except Exception as e:
        if _is_auth_error(e):
            raise DropboxAuthError(
                f"Dropbox відхилив запит. {_auth_hint()}"
            ) from e
        raise
    logger.info("Dropbox upload: %s (%s bytes)", path, len(data))
    return path
