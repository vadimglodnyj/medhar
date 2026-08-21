"""Єдиний SQLite-сумісний конектор: локальний файл (primary) або Turso (sync)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from typing import Any, Optional

import config as _cfg

# Синхронізація може чекати cold-start Turso; перевірка онлайн — коротша.
TURSO_HTTP_TIMEOUT = 60.0
TURSO_PROBE_TIMEOUT = 4.0
_REACH_TTL_SEC = 20.0
_reach_lock = threading.Lock()
_reach_ok = False
_reach_at = 0.0


class CloudRow(Mapping):
    """Рядок Turso з поведінкою sqlite3.Row (ключі та числові індекси)."""

    def __init__(self, columns, values):
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._by_name = dict(zip(self._columns, self._values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._by_name[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)

    def keys(self):
        return self._columns


class CloudCursor:
    def __init__(self, result):
        columns = tuple(result.columns or ())
        self._rows = [
            CloudRow(columns, [row[i] for i in range(len(columns))])
            for row in (result.rows or ())
        ]
        self._index = 0
        self.rowcount = int(result.rows_affected or 0)
        self.lastrowid = result.last_insert_rowid

    def fetchone(self) -> Optional[CloudRow]:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[CloudRow]:
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows


class _PipelineResult:
    def __init__(self, columns, rows, rows_affected=0, last_insert_rowid=None):
        self.columns = columns
        self.rows = rows
        self.rows_affected = rows_affected
        self.last_insert_rowid = last_insert_rowid


def _turso_http_url(url: str) -> str:
    """
    libsql-client на Windows стабільно працює через HTTPS/HTTP API.
    libsql:// → wss:// часто дає 400 Invalid response status.
    """
    text = (url or "").strip()
    if text.startswith("libsql://"):
        return "https://" + text[len("libsql://") :]
    return text


def _cell_value(cell: Any) -> Any:
    if not isinstance(cell, dict):
        return cell
    if cell.get("type") == "null" or "value" not in cell:
        return None
    raw = cell.get("value")
    kind = cell.get("type")
    if kind == "integer":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return raw
    if kind == "float":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return raw
    return raw


def _arg_value(value: Any) -> dict:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": str(value)}
    return {"type": "text", "value": str(value)}


class CloudConnection:
    """HTTP-адаптер Turso /v2/pipeline (без KeyError 'result' у libsql-client)."""

    def __init__(self):
        base = _turso_http_url(getattr(_cfg, "TURSO_DATABASE_URL", "")).rstrip("/")
        token = (getattr(_cfg, "TURSO_AUTH_TOKEN", "") or "").strip()
        if not base or not token:
            raise RuntimeError("Turso не налаштовано")
        self._url = base + "/v2/pipeline"
        self._token = token

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, _value):
        pass

    def execute(self, sql: str, parameters=(), *, timeout: Optional[float] = None) -> CloudCursor:
        stmt: dict[str, Any] = {"sql": sql}
        args = list(parameters or ())
        if args:
            stmt["args"] = [_arg_value(v) for v in args]
        payload = json.dumps(
            {"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=payload,
            method="POST",
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
                "User-Agent": "MedharBot/1.0",
            },
        )
        try:
            with urllib.request.urlopen(
                req, timeout=TURSO_HTTP_TIMEOUT if timeout is None else timeout
            ) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            err = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(err.strip() or f"Turso HTTP {exc.code}") from exc
        except Exception as exc:
            raise RuntimeError(f"Turso недоступна: {exc}") from exc
        try:
            data = json.loads(raw) if raw else {}
        except ValueError as exc:
            raise RuntimeError("Turso повернула не JSON") from exc
        results = data.get("results") or []
        if not results:
            raise RuntimeError("Turso: порожня відповідь")
        first = results[0] if isinstance(results[0], dict) else {}
        if first.get("type") != "ok":
            err = first.get("error") or first
            if isinstance(err, dict):
                message = str(err.get("message") or err.get("code") or err)
            else:
                message = str(err)
            raise RuntimeError(message)
        response = first.get("response") or {}
        result = response.get("result") or {}
        cols_raw = result.get("cols") or []
        columns = []
        for col in cols_raw:
            if isinstance(col, dict):
                columns.append(str(col.get("name") or ""))
            else:
                columns.append(str(col))
        rows = []
        for row in result.get("rows") or []:
            if isinstance(row, (list, tuple)):
                rows.append([_cell_value(cell) for cell in row])
            else:
                rows.append(row)
        affected = result.get("affected_row_count") or result.get("rows_affected") or 0
        last_id = result.get("last_insert_rowid")
        if isinstance(last_id, dict):
            last_id = last_id.get("value")
        try:
            last_id = int(last_id) if last_id not in (None, "") else None
        except (TypeError, ValueError):
            last_id = None
        return CloudCursor(_PipelineResult(columns, rows, affected, last_id))

    def executescript(self, script: str) -> None:
        statement = ""
        for line in script.splitlines():
            statement += line + "\n"
            if sqlite3.complete_statement(statement):
                sql = statement.strip()
                statement = ""
                if sql:
                    self.execute(sql)
        if statement.strip():
            self.execute(statement.strip())

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def connect_local() -> sqlite3.Connection:
    """Локальна SQLite — первинне сховище для UI та офлайн."""
    # timeout + WAL: UI і фоновий sync-потік пишуть в одну БД,
    # без цього конкурентний запис дає "database is locked".
    conn = sqlite3.connect(_cfg.OUTPATIENT_JOURNAL_DB, timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
    except Exception:
        pass
    return conn


def is_locked_error(exc: BaseException) -> bool:
    text = str(exc or "").casefold()
    return "database is locked" in text or "database table is locked" in text


def retry_if_locked(fn, *, attempts: int = 8, delay_sec: float = 0.15):
    """Повторює запис у SQLite, якщо БД тимчасово зайнятa sync-потоком."""
    import time

    last: BaseException | None = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            last = exc
            if not is_locked_error(exc) or i >= attempts - 1:
                raise
            time.sleep(delay_sec * (1.0 + 0.5 * i))
    if last:
        raise last
    return None


def connect():
    """Завжди локальна SQLite (team mode = local-first + фоновий Turso sync)."""
    return connect_local()


def connect_turso() -> CloudConnection:
    """Віддалена Turso — лише для sync worker."""
    if not _cfg.USE_TURSO:
        raise RuntimeError("Turso не налаштовано")
    return CloudConnection()


def turso_reachable(timeout: float = TURSO_PROBE_TIMEOUT, *, force: bool = False) -> bool:
    """Чи доступна хмарна БД (для індикатора онлайн/офлайн). Результат кешується."""
    if not _cfg.USE_TURSO:
        return False
    now = time.monotonic()
    global _reach_ok, _reach_at
    with _reach_lock:
        if not force and (now - _reach_at) < _REACH_TTL_SEC:
            return _reach_ok
    ok = False
    try:
        import socket
        from urllib.parse import urlparse

        url = _turso_http_url(_cfg.TURSO_DATABASE_URL)
        host = urlparse(url).hostname
        if not host:
            ok = False
        else:
            port = urlparse(url).port or (443 if url.startswith("https") else 80)
            with socket.create_connection((host, port), timeout=timeout):
                pass
            conn = connect_turso()
            try:
                conn.execute("SELECT 1", timeout=timeout)
                ok = True
            finally:
                conn.close()
    except Exception:
        ok = False
    with _reach_lock:
        _reach_ok = ok
        _reach_at = time.monotonic()
    return ok


def set_turso_reach_cache(ok: bool) -> None:
    """Фоновий sync фіксує онлайн/офлайн, щоб індикатор не робив зайвий HTTP."""
    global _reach_ok, _reach_at
    with _reach_lock:
        _reach_ok = bool(ok)
        _reach_at = time.monotonic()


def backend_name() -> str:
    if getattr(_cfg, "USE_TURSO_SYNC", False):
        return "local+sync"
    if _cfg.USE_TURSO:
        return "turso"
    return "local"
