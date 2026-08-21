# -*- coding: utf-8 -*-
"""Завантаження медіа пацієнтів у Dropbox."""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Optional

import config as _cfg

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()


class DropboxAuthError(RuntimeError):
    """Токен недійсний або протермінований."""


def dropbox_configured() -> bool:
    if _cfg.DROPBOX_REFRESH_TOKEN and _cfg.DROPBOX_APP_KEY:
        return True
    return bool(_cfg.DROPBOX_ACCESS_TOKEN)


def uses_refresh_token() -> bool:
    """True якщо налаштовано постійний доступ (refresh token)."""
    return bool(_cfg.DROPBOX_REFRESH_TOKEN and _cfg.DROPBOX_APP_KEY)


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
    treatment_sync_id: str = "",
) -> str:
    """
    App folder (root порожній):
      API: /patients/{ПІБ}/{лікування}/файл
      Windows: D:\\Dropbox\\Програми\\patientss\\patients\\{ПІБ}\\...
    Full Dropbox (root=м-с): /м-с/patients/{ПІБ}/{лікування}/файл

    Папка лікування іменується за sync_id (стабільно між ПК), а не за локальним id.
    Старі файли лишаються в «назва (#123)» — їх шукає resolve_media_local_path / scan.
    """
    root = (_cfg.DROPBOX_ROOT_FOLDER or "").strip().strip("/")
    patient = _safe_path_segment(pib, "patient")
    title = _safe_path_segment(treatment_title, "treatment")
    sid = re.sub(r"[^0-9a-fA-F\-]", "", str(treatment_sync_id or "").strip())
    if sid:
        treatment_folder = f"{title} ({sid})"
    else:
        treatment_folder = f"{title} (#{int(treatment_id or 0)})"
    file_name = _safe_path_segment(filename, "file")
    if "." not in file_name and "." in (filename or ""):
        ext = filename.rsplit(".", 1)[-1]
        file_name = f"{file_name}.{ext}"
    parts = []
    if root:
        parts.append(root)
    parts.extend(["patients", patient, treatment_folder, file_name])
    return "/" + "/".join(parts)


def api_media_path_to_local(api_path: str) -> Optional[str]:
    """Перетворює API-шлях Dropbox (/patients/…) на локальний файл у synced-папці."""
    root = patients_local_root()
    if not root:
        return None
    parts = [
        p for p in str(api_path or "").replace("\\", "/").strip("/").split("/") if p
    ]
    cfg_root = (_cfg.DROPBOX_ROOT_FOLDER or "").strip().strip("/")
    if cfg_root and parts and parts[0] == cfg_root:
        parts = parts[1:]
    if parts and parts[0].casefold() == "patients":
        parts = parts[1:]
    if not parts:
        return None
    return os.path.join(root, *parts)


def find_media_under_patient(
    *,
    pib: str,
    original_name: str = "",
    filename: str = "",
) -> Optional[str]:
    """
    Шукає файл під patients/{ПІБ}/ у будь-якій підпапці лікування.
    Потрібно, коли на іншому ПК локальний treatment_id ≠ id у назві папки Dropbox.
    """
    root = patients_local_root()
    if not root:
        return None
    patient = _safe_path_segment(pib, "patient")
    patient_dir = os.path.join(root, patient)
    if not os.path.isdir(patient_dir):
        return None
    want = []
    for name in (original_name, filename):
        text = (name or "").strip()
        if not text:
            continue
        want.append(os.path.basename(text).casefold())
        safe = _safe_path_segment(text, "")
        if safe:
            want.append(safe.casefold())
    want = [w for w in want if w]
    if not want:
        return None
    try:
        for dirpath, _dirs, files in os.walk(patient_dir):
            listing = {f.casefold(): f for f in files}
            for w in want:
                real = listing.get(w)
                if real:
                    path = os.path.join(dirpath, real)
                    if os.path.isfile(path):
                        return path
    except OSError:
        return None
    return None


def download_bytes_from_dropbox(dropbox_path: str) -> Optional[bytes]:
    """Завантажує файл з Dropbox API (коли локальна копія ще online-only / відсутня)."""
    if not dropbox_configured():
        return None
    path = "/" + str(dropbox_path or "").lstrip("/")
    if path == "/":
        return None
    try:
        _meta, resp = get_client().files_download(path)
        return resp.content
    except Exception as e:
        if _is_auth_error(e):
            raise DropboxAuthError(
                f"Dropbox відхилив запит. {_auth_hint()}"
            ) from e
        logger.info("Dropbox download miss %s: %s", path, e)
        return None


def find_dropbox_api_path_under_patient(
    *,
    pib: str,
    original_name: str = "",
    filename: str = "",
) -> Optional[str]:
    """Шукає файл у Dropbox API під /patients/{ПІБ}/ (рекурсивно)."""
    if not dropbox_configured():
        return None
    want = set()
    for name in (original_name, filename):
        text = (name or "").strip()
        if not text:
            continue
        want.add(os.path.basename(text).casefold())
        safe = _safe_path_segment(text, "")
        if safe:
            want.add(safe.casefold())
    if not want:
        return None
    root = (_cfg.DROPBOX_ROOT_FOLDER or "").strip().strip("/")
    patient = _safe_path_segment(pib, "patient")
    parts = []
    if root:
        parts.append(root)
    parts.extend(["patients", patient])
    folder = "/" + "/".join(parts)
    try:
        from dropbox.files import FileMetadata

        dbx = get_client()
        result = dbx.files_list_folder(folder, recursive=True)
        entries = list(result.entries or [])
        while result.has_more:
            result = dbx.files_list_folder_continue(result.cursor)
            entries.extend(result.entries or [])
        for entry in entries:
            if not isinstance(entry, FileMetadata):
                continue
            name = os.path.basename(entry.path_display or entry.name or "").casefold()
            if name in want:
                return entry.path_display or entry.path_lower
    except Exception as e:
        if _is_auth_error(e):
            raise DropboxAuthError(
                f"Dropbox відхилив запит. {_auth_hint()}"
            ) from e
        logger.info("Dropbox list under %s: %s", folder, e)
        return None
    return None


def patients_local_root() -> Optional[str]:
    """
    Локальна папка Dropbox з медіа пацієнтів:
      …/Програми/patientss/patients
    (поруч із Excel-папкою …/patientss/data).
    """
    from utils.team_vault import find_dropbox_root, resolve_excel_dir

    excel = (_cfg.EXCEL_DATA_DIR or "").rstrip("\\/")
    if excel:
        parent = os.path.dirname(excel)
        candidate = os.path.join(parent, "patients")
        if os.path.isdir(candidate):
            return candidate
        # якщо Excel вказаний як .../patientss (без data)
        sibling = os.path.join(excel, "patients")
        if os.path.isdir(sibling):
            return sibling

    sub = (os.environ.get("MEDHAR_EXCEL_SUBPATH") or "").strip().replace("\\", "/")
    if sub:
        parts = [p for p in sub.split("/") if p]
        if parts and parts[-1].lower() == "data":
            parts = parts[:-1] + ["patients"]
        else:
            parts = parts + ["patients"]
        resolved = resolve_excel_dir("/".join(parts[:-1])) if len(parts) > 1 else None
        # resolve_excel_dir очікує шлях під Dropbox; для patients будуємо вручну
        dropbox = find_dropbox_root()
        if dropbox:
            full = os.path.normpath(os.path.join(dropbox, *parts))
            if os.path.isdir(full):
                return full

    dropbox = find_dropbox_root()
    if not dropbox:
        return None
    for rel in (
        ("Програми", "patientss", "patients"),
        ("Apps", "patientss", "patients"),
        ("patients",),
    ):
        full = os.path.join(dropbox, *rel)
        if os.path.isdir(full):
            return full
    return None


def releases_local_dir() -> Optional[str]:
    """
    Локальна папка збірки оновлень:
      …/Програми/patientss/releases
    (поруч із data/ і patients/).
    """
    from utils.team_vault import find_dropbox_root

    excel = (_cfg.EXCEL_DATA_DIR or "").rstrip("\\/")
    if excel:
        parent = os.path.dirname(excel)
        candidate = os.path.join(parent, "releases")
        if os.path.isdir(candidate) or os.path.isdir(parent):
            return candidate

    patients = patients_local_root()
    if patients:
        parent = os.path.dirname(patients)
        return os.path.join(parent, "releases")

    dropbox = find_dropbox_root()
    if not dropbox:
        return None
    for rel in (
        ("Програми", "patientss", "releases"),
        ("Apps", "patientss", "releases"),
        ("releases",),
    ):
        full = os.path.join(dropbox, *rel)
        if os.path.isdir(full) or os.path.isdir(os.path.dirname(full)):
            return full
    return None


def treatment_media_local_dir(
    *,
    pib: str,
    treatment_title: str,
    treatment_id: int,
    treatment_sync_id: str = "",
) -> Optional[str]:
    """Папка лікування у локальному Dropbox: …/patients/{ПІБ}/{title} (sync_id|#id)."""
    root = patients_local_root()
    if not root:
        return None
    patient = _safe_path_segment(pib, "patient")
    title = _safe_path_segment(treatment_title, "treatment")
    patient_dir = os.path.join(root, patient)
    candidates = []
    sid = re.sub(r"[^0-9a-fA-F\-]", "", str(treatment_sync_id or "").strip())
    if sid:
        candidates.append(os.path.join(patient_dir, f"{title} ({sid})"))
    if treatment_id:
        candidates.append(os.path.join(patient_dir, f"{title} (#{int(treatment_id)})"))
    for folder in candidates:
        if os.path.isdir(folder):
            return folder
    if not os.path.isdir(patient_dir):
        return None
    needles = []
    if sid:
        needles.append(f"({sid})")
    if treatment_id:
        needles.append(f"(#{int(treatment_id)})")
    try:
        for name in os.listdir(patient_dir):
            for needle in needles:
                if name.endswith(needle):
                    path = os.path.join(patient_dir, name)
                    if os.path.isdir(path):
                        return path
        # Інший ПК: папка «ВТ … (#15)», а локальний treatment_id інший —
        # беремо будь-яку підпапку з тим самим title.
        title_cf = title.casefold()
        title_hits = []
        for name in os.listdir(patient_dir):
            path = os.path.join(patient_dir, name)
            if not os.path.isdir(path):
                continue
            name_cf = name.casefold()
            if name_cf == title_cf or name_cf.startswith(title_cf + " ("):
                title_hits.append(path)
        if len(title_hits) == 1:
            return title_hits[0]
        if len(title_hits) > 1:
            # надаємо перевагу папці, де вже є PDF-довідка 21_*.pdf
            for path in title_hits:
                try:
                    for fname in os.listdir(path):
                        low = fname.casefold()
                        if low.startswith("21") and low.endswith(".pdf"):
                            return path
                except OSError:
                    continue
            return title_hits[0]
    except OSError:
        return None
    return None


def find_injury_cert_pdfs_under_patient(
    *,
    pib: str,
    treatment_title: str = "",
) -> list[str]:
    """
    Локальні PDF-довідки (21_*.pdf) під patients/{ПІБ}/.
    Якщо задано title — спочатку папки цього лікування (навіть з чужим #id).
    """
    root = patients_local_root()
    if not root:
        return []
    patient = _safe_path_segment(pib, "patient")
    patient_dir = os.path.join(root, patient)
    if not os.path.isdir(patient_dir):
        return []
    title = _safe_path_segment(treatment_title or "", "")
    title_cf = title.casefold() if title else ""
    found: list[str] = []

    def _collect(folder: str) -> None:
        try:
            for fname in os.listdir(folder):
                low = fname.casefold()
                if not (low.startswith("21") and low.endswith(".pdf")):
                    continue
                path = os.path.join(folder, fname)
                if os.path.isfile(path):
                    found.append(path)
        except OSError:
            return

    try:
        preferred: list[str] = []
        other: list[str] = []
        for name in os.listdir(patient_dir):
            path = os.path.join(patient_dir, name)
            if not os.path.isdir(path):
                continue
            if title_cf and (
                name.casefold() == title_cf
                or name.casefold().startswith(title_cf + " (")
            ):
                preferred.append(path)
            else:
                other.append(path)
        for folder in preferred or other:
            _collect(folder)
            if preferred and found:
                break
        # PDF прямо в корені пацієнта
        if not found:
            _collect(patient_dir)
    except OSError:
        return []
    return found


def resolve_media_local_path(
    *,
    pib: str,
    treatment_title: str,
    treatment_id: int,
    original_name: str = "",
    filename: str = "",
    treatment_sync_id: str = "",
) -> Optional[str]:
    """Шлях до файлу в локальному Dropbox (за original_name або filename)."""
    folder = treatment_media_local_dir(
        pib=pib,
        treatment_title=treatment_title,
        treatment_id=treatment_id,
        treatment_sync_id=treatment_sync_id,
    )
    if not folder:
        return None
    candidates = []
    for name in (original_name, filename):
        text = _safe_path_segment(name or "", "")
        if not text:
            continue
        if "." not in text and name and "." in name:
            text = f"{text}.{name.rsplit('.', 1)[-1]}"
        candidates.append(text)
        candidates.append(os.path.basename(name))
    seen = set()
    for name in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    # case-insensitive match
    try:
        listing = {f.casefold(): f for f in os.listdir(folder)}
    except OSError:
        return None
    for name in candidates:
        real = listing.get((name or "").casefold())
        if real:
            path = os.path.join(folder, real)
            if os.path.isfile(path):
                return path
    return None


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
            oauth2_refresh_token=_cfg.DROPBOX_REFRESH_TOKEN,
            app_key=_cfg.DROPBOX_APP_KEY,
            app_secret=_cfg.DROPBOX_APP_SECRET or None,
        )
    return dropbox.Dropbox(_cfg.DROPBOX_ACCESS_TOKEN)


def get_client(*, force_new: bool = False):
    global _client
    if _client is not None and not force_new:
        return _client
    with _client_lock:
        if _client is None or force_new:
            _client = _build_client()
        return _client


def reset_client() -> None:
    """Скидає кеш клієнта після зміни режиму / секретів."""
    global _client
    with _client_lock:
        _client = None


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
