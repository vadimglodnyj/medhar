# -*- coding: utf-8 -*-
"""Перевірка та встановлення оновлення desktop Medhar через Install.exe."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Маніфест і інсталятор у App Folder Dropbox: …/Програми/patientss/releases/
DEFAULT_DROPBOX_MANIFEST = "/releases/update_manifest.json"
DEFAULT_DROPBOX_INSTALLER = "/releases/Install.exe"
DEFAULT_HTTP_MANIFEST_URL = (
    "https://raw.githubusercontent.com/vadimglodnyj/medhar/main/desktop/update_manifest.json"
)
_USER_AGENT = "Medhar-Update/1.0"
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
_CACHE_TTL_SEC = 300.0


def parse_version(value: str) -> tuple[int, ...]:
    text = (value or "").strip().lstrip("vV")
    parts = re.findall(r"\d+", text)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def version_newer(remote: str, local: str) -> bool:
    a = parse_version(remote)
    b = parse_version(local)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


def current_version() -> str:
    import config as cfg

    return (getattr(cfg, "APP_VERSION", None) or "0").strip()


def _configured_manifest_source() -> str:
    """dropbox:/… або https://… — звідки читати маніфест."""
    import config as cfg

    raw = (
        (os.environ.get("MEDHAR_UPDATE_URL") or "").strip()
        or getattr(cfg, "APP_UPDATE_MANIFEST_URL", "")
        or ""
    ).strip()
    if raw:
        return raw
    return f"dropbox:{DEFAULT_DROPBOX_MANIFEST}"


def _is_dropbox_ref(value: str) -> bool:
    text = (value or "").strip().casefold()
    return text.startswith("dropbox:") or text.startswith("dropbox:/")


def _dropbox_path_from_ref(value: str) -> str:
    text = (value or "").strip()
    if text.casefold().startswith("dropbox:"):
        text = text[8:]
    return "/" + text.lstrip("/")


def _http_get_json(url: str, timeout: float = 12.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Маніфест оновлення має бути JSON-об'єктом")
    return data


def _dropbox_download_bytes(path: str) -> bytes:
    from utils.dropbox_sync import dropbox_configured, get_client

    if not dropbox_configured():
        raise RuntimeError("Dropbox не налаштовано (немає токена команди).")
    api_path = "/" + str(path or "").lstrip("/")
    _meta, resp = get_client().files_download(api_path)
    return resp.content


def _dropbox_download_to_file(path: str, dest: str) -> None:
    from utils.dropbox_sync import dropbox_configured, get_client

    if not dropbox_configured():
        raise RuntimeError("Dropbox не налаштовано (немає токена команди).")
    api_path = "/" + str(path or "").lstrip("/")
    _meta, resp = get_client().files_download(api_path)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(1024 * 256):
            if chunk:
                fh.write(chunk)


def _load_manifest_from_source(source: str) -> dict:
    if _is_dropbox_ref(source) or source.startswith("/"):
        path = (
            _dropbox_path_from_ref(source)
            if _is_dropbox_ref(source)
            else "/" + source.lstrip("/")
        )
        raw = _dropbox_download_bytes(path).decode("utf-8", errors="replace")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Маніфест Dropbox має бути JSON-об'єктом")
        return data
    return _http_get_json(source)


def _local_releases_paths() -> tuple[Optional[str], Optional[str]]:
    """(manifest.json, Install.exe) у синхронізованій папці Dropbox."""
    from utils.dropbox_sync import releases_local_dir

    root = releases_local_dir()
    if not root:
        return None, None
    return (
        os.path.join(root, "update_manifest.json"),
        os.path.join(root, "Install.exe"),
    )


def _load_manifest_local() -> Optional[dict]:
    manifest_path, _installer = _local_releases_paths()
    if not manifest_path or not os.path.isfile(manifest_path):
        return None
    with open(manifest_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("Локальний маніфест має бути JSON-об'єктом")
    data = dict(data)
    data["_source"] = f"local:{manifest_path}"
    return data


def fetch_manifest(*, force: bool = False) -> Optional[dict]:
    """Читає manifest: локальний Dropbox sync → Dropbox API → HTTP."""
    import time

    from utils.dropbox_sync import dropbox_configured

    now = time.monotonic()
    with _CACHE_LOCK:
        if (
            not force
            and _CACHE["payload"] is not None
            and now - float(_CACHE["at"] or 0) < _CACHE_TTL_SEC
        ):
            return dict(_CACHE["payload"])

    # 1) Локальна синхронізована папка (не потребує files.content.read)
    try:
        local = _load_manifest_local()
        if local:
            with _CACHE_LOCK:
                _CACHE["at"] = now
                _CACHE["payload"] = dict(local)
            return local
    except Exception as exc:
        logger.info("Local update manifest failed: %s", exc)

    sources: list[str] = []
    if dropbox_configured():
        sources.append(f"dropbox:{DEFAULT_DROPBOX_MANIFEST}")
    primary = _configured_manifest_source()
    if primary:
        sources.append(primary)
    sources.append(DEFAULT_HTTP_MANIFEST_URL)

    seen: set[str] = set()
    ordered: list[str] = []
    for s in sources:
        key = (s or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)

    last_err = ""
    for source in ordered:
        try:
            data = _load_manifest_from_source(source)
            data = dict(data)
            data["_source"] = source
            with _CACHE_LOCK:
                _CACHE["at"] = now
                _CACHE["payload"] = dict(data)
            return data
        except Exception as exc:
            last_err = str(exc)
            logger.info("Update manifest unavailable (%s): %s", source, exc)

    logger.info("All update manifest sources failed: %s", last_err)
    return None


def _installer_ref_from_manifest(manifest: dict) -> str:
    """Повертає dropbox:/path або https://… для інсталятора."""
    dropbox_path = str(manifest.get("dropbox_path") or "").strip()
    if dropbox_path:
        return f"dropbox:{_dropbox_path_from_ref(dropbox_path)}"
    url = str(manifest.get("url") or "").strip()
    if url:
        return url
    return f"dropbox:{DEFAULT_DROPBOX_INSTALLER}"


def check_for_update(*, force: bool = False) -> dict[str, Any]:
    """
    Повертає стан оновлення для UI/API.
    available=True лише коли remote version новіша і є шлях/url інсталятора.
    """
    local = current_version()
    out: dict[str, Any] = {
        "ok": True,
        "current_version": local,
        "latest_version": local,
        "available": False,
        "url": "",
        "dropbox_path": "",
        "notes": "",
        "manifest_url": _configured_manifest_source(),
        "error": "",
    }
    manifest = fetch_manifest(force=force)
    if not manifest:
        out["error"] = (
            "Не вдалося перевірити оновлення "
            "(немає мережі, Dropbox або маніфесту в /releases/)."
        )
        return out

    latest = str(manifest.get("version") or "").strip()
    notes = str(manifest.get("notes") or "").strip()
    installer = _installer_ref_from_manifest(manifest)
    out["latest_version"] = latest or local
    out["notes"] = notes
    out["url"] = installer
    if _is_dropbox_ref(installer) or installer.startswith("/"):
        out["dropbox_path"] = _dropbox_path_from_ref(installer)
    if latest and installer and version_newer(latest, local):
        out["available"] = True
    return out


def _download_http_file(url: str, dest: str, timeout: float = 300.0) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as fh:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            fh.write(chunk)


def download_and_run_installer(*, force_check: bool = True) -> dict[str, Any]:
    """
    Завантажує Install.exe (локальний Dropbox sync / API / HTTP) у temp і запускає.
    Дані користувача (%LOCALAPPDATA%\\Medhar) інсталятор не чіпає.
    """
    import shutil

    status = check_for_update(force=force_check)
    if not status.get("available"):
        return {
            "ok": False,
            "error": status.get("error")
            or "Нова версія недоступна або вже встановлена.",
            **status,
        }
    ref = status.get("url") or ""
    latest = status["latest_version"]

    import config as cfg

    temp_dir = getattr(cfg, "TEMP_DIR", None) or os.environ.get("TEMP") or "."
    os.makedirs(temp_dir, exist_ok=True)
    dest = os.path.join(temp_dir, f"Medhar_Update_{latest}.exe")
    try:
        _manifest_path, local_installer = _local_releases_paths()
        if local_installer and os.path.isfile(local_installer):
            shutil.copy2(local_installer, dest)
        elif _is_dropbox_ref(ref) or (
            status.get("dropbox_path") and not str(ref).startswith("http")
        ):
            path = status.get("dropbox_path") or _dropbox_path_from_ref(ref)
            _dropbox_download_to_file(path, dest)
        else:
            _download_http_file(ref, dest)
    except Exception as exc:
        logger.exception("Update download failed")
        return {"ok": False, "error": f"Завантаження не вдалося: {exc}", **status}

    if not os.path.isfile(dest) or os.path.getsize(dest) < 1_000_000:
        return {
            "ok": False,
            "error": "Файл оновлення пошкоджений або занадто малий.",
            **status,
        }

    try:
        subprocess.Popen(
            [dest],
            cwd=os.path.dirname(dest),
            close_fds=True,
        )
    except Exception as exc:
        logger.exception("Update launch failed")
        return {"ok": False, "error": f"Не вдалося запустити інсталятор: {exc}", **status}

    return {
        "ok": True,
        "message": (
            f"Запущено інсталятор {latest}. Підтвердіть UAC і дочекайтесь завершення; "
            "журнал і дані в AppData збережуться."
        ),
        "installer_path": dest,
        **status,
    }


def publish_release_to_dropbox(
    installer_path: str,
    *,
    version: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """
    Викладає Install.exe + update_manifest.json у Dropbox /releases/
    (API upload + локальна папка синхронізації).
    """
    import shutil

    from utils.dropbox_sync import (
        dropbox_configured,
        releases_local_dir,
        upload_bytes_to_dropbox,
    )

    if not os.path.isfile(installer_path):
        return {"ok": False, "error": f"Немає файлу: {installer_path}"}

    ver = (version or current_version()).strip()
    with open(installer_path, "rb") as fh:
        data = fh.read()

    manifest = {
        "version": ver,
        "dropbox_path": DEFAULT_DROPBOX_INSTALLER,
        "url": f"dropbox:{DEFAULT_DROPBOX_INSTALLER}",
        "notes": notes
        or f"Medhar {ver}. Оновлення через Dropbox (Програми/patientss/releases).",
    }
    raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    # Локальна папка синхронізації — головне джерело для колег з клієнтом Dropbox
    local_dir = releases_local_dir()
    if local_dir:
        try:
            os.makedirs(local_dir, exist_ok=True)
            shutil.copy2(installer_path, os.path.join(local_dir, "Install.exe"))
            shutil.copy2(
                installer_path, os.path.join(local_dir, f"Install_{ver}.exe")
            )
            with open(
                os.path.join(local_dir, "update_manifest.json"), "w", encoding="utf-8"
            ) as fh:
                fh.write(raw.decode("utf-8"))
        except OSError as exc:
            logger.warning("Local releases publish failed: %s", exc)

    if dropbox_configured():
        try:
            upload_bytes_to_dropbox(data, DEFAULT_DROPBOX_INSTALLER, mute=False)
            upload_bytes_to_dropbox(data, f"/releases/Install_{ver}.exe", mute=False)
            upload_bytes_to_dropbox(raw, DEFAULT_DROPBOX_MANIFEST, mute=False)
        except Exception as exc:
            logger.warning("Dropbox API publish failed: %s", exc)
            if not local_dir or not os.path.isfile(
                os.path.join(local_dir, "Install.exe")
            ):
                return {"ok": False, "error": str(exc)}

    # локальна копія маніфесту в репо
    local_manifest = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "desktop",
        "update_manifest.json",
    )
    try:
        with open(local_manifest, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError as exc:
        logger.warning("Не оновлено локальний update_manifest.json: %s", exc)

    return {"ok": True, "version": ver, "manifest": manifest, "bytes": len(data)}
