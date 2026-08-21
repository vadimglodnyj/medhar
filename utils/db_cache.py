"""
Короткочасний кеш важких списків із БД.

Потрібен передусім для Turso: кожен запит там іде по мережі. Будь-який запис
одразу скидає кеш, тому власні зміни користувач бачить без затримки; зміни
колег підтягуються не пізніше ніж через TTL.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

JOURNAL_ROWS = "journal_rows"
PATIENTS = "patients"
PIB_MAP = "pib_map"
REMINDERS = "reminders"
INJURY_CERTS = "injury_certs"


def _ttl_from_env() -> float:
    raw = (os.environ.get("MEDHAR_CACHE_TTL_SEC") or "").strip()
    if not raw:
        return 30.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 30.0


TTL_SEC = _ttl_from_env()

_lock = threading.Lock()
_entries: dict[str, tuple[float, Any]] = {}


def get_or_load(key: str, loader: Callable[[], Any]) -> Any:
    """Значення з кешу або результат loader() (кладеться в кеш)."""
    if TTL_SEC <= 0:
        return loader()
    now = time.monotonic()
    with _lock:
        hit = _entries.get(key)
        if hit is not None and now < hit[0]:
            return hit[1]
    value = loader()
    with _lock:
        _entries[key] = (time.monotonic() + TTL_SEC, value)
    return value


def invalidate_all() -> None:
    """Скидає кеш після будь-якого запису в БД."""
    with _lock:
        _entries.clear()
