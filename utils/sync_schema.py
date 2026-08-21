# -*- coding: utf-8 -*-
"""Схема офлайн-синхронізації журналу: sync_id, outbox, meta."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import config as _cfg

SYNC_TABLES = (
    "patients",
    "injury_cases",
    "treatments",
    "outpatient_entries",
    "treatment_restrictions",
    "treatment_media",
    "team_settings",
    "team_members",
    "team_tasks",
    "vlk_queue",
)

# FK column -> (parent_table, parent_pk_column)
FK_SYNC_MAP: dict[str, dict[str, tuple[str, str]]] = {
    "injury_cases": {"patient_id": ("patients", "id")},
    "treatments": {
        "patient_id": ("patients", "id"),
        "injury_case_id": ("injury_cases", "id"),
    },
    "outpatient_entries": {"treatment_id": ("treatments", "id")},
    "treatment_restrictions": {
        "treatment_id": ("treatments", "id"),
        "visit_id": ("outpatient_entries", "id"),
    },
    "treatment_media": {
        "treatment_id": ("treatments", "id"),
        "visit_id": ("outpatient_entries", "id"),
        "injury_case_id": ("injury_cases", "id"),
    },
}

_sync_schema_ready = False


def sync_enabled() -> bool:
    return bool(getattr(_cfg, "USE_TURSO_SYNC", False))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_sync_id() -> str:
    return str(uuid.uuid4())


def _table_cols(conn, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_col(conn, table: str, col: str, decl: str, cols: set[str]) -> None:
    if col not in cols:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            cols.add(col)
        except Exception as exc:
            text = str(exc).casefold()
            if "duplicate column" not in text and "already exists" not in text:
                raise
            cols.add(col)


def _sync_tables_need_upgrade(conn) -> bool:
    for table in SYNC_TABLES:
        cols = _table_cols(conn, table)
        if cols and "deleted_at" not in cols:
            return True
    return False


def ensure_sync_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """Додає sync-колонки, sync_meta, sync_outbox, media_upload_queue."""
    global _sync_schema_ready
    own = conn is None
    # Прапорець стосується лише локальної SQLite; для Turso (CloudConnection)
    # перевірка йде щоразу, але guard стоїть у journal_sync.
    is_local = own or isinstance(conn, sqlite3.Connection)
    if is_local and _sync_schema_ready and own:
        return
    if own:
        from utils.db_backend import connect_local

        conn = connect_local()
    try:
        if is_local and _sync_schema_ready and not _sync_tables_need_upgrade(conn):
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS sync_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                sync_id TEXT NOT NULL,
                op TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_sync_outbox_created ON sync_outbox(id);
            CREATE TABLE IF NOT EXISTS media_upload_queue (
                media_id INTEGER PRIMARY KEY,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                queued_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        for table in SYNC_TABLES:
            cols = _table_cols(conn, table)
            if not cols:
                continue
            _add_col(conn, table, "sync_id", "TEXT NOT NULL DEFAULT ''", cols)
            _add_col(conn, table, "updated_at", "TEXT NOT NULL DEFAULT ''", cols)
            _add_col(conn, table, "deleted_at", "TEXT NOT NULL DEFAULT ''", cols)
            _backfill_table(conn, table)
            try:
                conn.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_sync_id "
                    f"ON {table}(sync_id)"
                )
            except Exception:
                pass
        conn.commit()
        if is_local:
            _sync_schema_ready = True
    finally:
        if own:
            conn.close()


def _backfill_table(conn, table: str) -> None:
    ts = utc_now()
    ts_col = "uploaded_at" if table == "treatment_media" else "created_at"
    rows = conn.execute(
        f"SELECT id, sync_id, updated_at, {ts_col} FROM {table} "
        f"WHERE sync_id IS NULL OR sync_id = ''"
    ).fetchall()
    for row in rows:
        rid = row[0]
        sid = new_sync_id()
        updated = row[2] or row[3] or ts
        conn.execute(
            f"UPDATE {table} SET sync_id = ?, updated_at = ? WHERE id = ?",
            (sid, updated or ts, rid),
        )
    empty_ts = conn.execute(
        f"SELECT 1 FROM {table} WHERE updated_at IS NULL OR updated_at = '' LIMIT 1"
    ).fetchone()
    if empty_ts:
        conn.execute(
            f"UPDATE {table} SET updated_at = ? WHERE updated_at IS NULL OR updated_at = ''",
            (ts,),
        )


def not_deleted_sql(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"({prefix}deleted_at IS NULL OR {prefix}deleted_at = '')"


def get_meta(conn, key: str, default: str = "") -> str:
    ensure_sync_schema(conn)
    row = conn.execute(
        "SELECT value FROM sync_meta WHERE key = ?", (key,)
    ).fetchone()
    return row[0] if row else default


def set_meta(conn, key: str, value: str) -> None:
    ensure_sync_schema(conn)
    conn.execute(
        """
        INSERT INTO sync_meta (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def lookup_sync_id(conn, table: str, row_id: int) -> str:
    if not row_id:
        return ""
    row = conn.execute(
        f"SELECT sync_id FROM {table} WHERE id = ?", (int(row_id),)
    ).fetchone()
    return str(row[0]) if row and row[0] else ""


def lookup_id_by_sync_id(conn, table: str, sync_id: str) -> Optional[int]:
    if not sync_id:
        return None
    row = conn.execute(
        f"SELECT id FROM {table} WHERE sync_id = ?", (sync_id,)
    ).fetchone()
    return int(row[0]) if row else None


def row_dict(conn, table: str, sync_id: str) -> Optional[dict]:
    row = conn.execute(
        f"SELECT * FROM {table} WHERE sync_id = ?", (sync_id,)
    ).fetchone()
    if not row:
        return None
    return dict(row)


def payload_with_sync_fks(conn, table: str, data: dict) -> dict:
    """Замінює integer FK на *_sync_id для outbox / Turso push."""
    out = dict(data)
    for fk_col, (parent_table, _) in FK_SYNC_MAP.get(table, {}).items():
        fk_val = out.pop(fk_col, None)
        sync_key = f"{fk_col.replace('_id', '')}_sync_id"
        if fk_col == "patient_id":
            sync_key = "patient_sync_id"
        elif fk_col == "treatment_id":
            sync_key = "treatment_sync_id"
        elif fk_col == "visit_id":
            sync_key = "visit_sync_id"
        elif fk_col == "injury_case_id":
            sync_key = "injury_case_sync_id"
        if fk_val in (None, "", 0):
            out[sync_key] = ""
        else:
            out[sync_key] = lookup_sync_id(conn, parent_table, int(fk_val))
    out.pop("id", None)
    return out


def resolve_fks_from_sync_ids(conn, table: str, data: dict) -> dict:
    """Підставляє локальні integer FK з *_sync_id при pull."""
    out = dict(data)
    mapping = {
        "patient_sync_id": ("patient_id", "patients"),
        "treatment_sync_id": ("treatment_id", "treatments"),
        "visit_sync_id": ("visit_id", "outpatient_entries"),
        "injury_case_sync_id": ("injury_case_id", "injury_cases"),
    }
    zero_ok = {"injury_case_id"}
    for sync_key, (fk_col, parent_table) in mapping.items():
        if sync_key not in out:
            continue
        sid = out.pop(sync_key, "") or ""
        if sid:
            found = lookup_id_by_sync_id(conn, parent_table, sid)
            if found is None and fk_col in zero_ok:
                out[fk_col] = 0
            else:
                out[fk_col] = found
        else:
            out[fk_col] = 0 if fk_col in zero_ok else None
    return out


def touch_row(conn, table: str, sync_id: str, *, deleted: bool = False) -> str:
    ts = utc_now()
    if deleted:
        conn.execute(
            f"UPDATE {table} SET updated_at = ?, deleted_at = ? WHERE sync_id = ?",
            (ts, ts, sync_id),
        )
    else:
        conn.execute(
            f"UPDATE {table} SET updated_at = ? WHERE sync_id = ?",
            (ts, sync_id),
        )
    return ts


def enqueue_outbox(
    conn,
    table: str,
    sync_id: str,
    op: str,
    *,
    payload: Optional[dict] = None,
) -> None:
    if not sync_enabled():
        return
    ensure_sync_schema(conn)
    if payload is None:
        raw = row_dict(conn, table, sync_id)
        if not raw:
            return
        payload = payload_with_sync_fks(conn, table, raw)
    ts = payload.get("updated_at") or utc_now()
    conn.execute(
        """
        DELETE FROM sync_outbox
        WHERE table_name = ? AND sync_id = ? AND op = ?
        """,
        (table, sync_id, op),
    )
    conn.execute(
        """
        INSERT INTO sync_outbox (table_name, sync_id, op, payload_json, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (table, sync_id, op, json.dumps(payload, ensure_ascii=False), ts),
    )


def outbox_count(conn) -> int:
    ensure_sync_schema(conn)
    row = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()
    return int(row[0] if row else 0)


def queue_media_upload(conn, media_id: int) -> None:
    ensure_sync_schema(conn)
    conn.execute(
        """
        INSERT INTO media_upload_queue (media_id) VALUES (?)
        ON CONFLICT(media_id) DO NOTHING
        """,
        (int(media_id),),
    )


def list_media_upload_queue(conn) -> list[int]:
    ensure_sync_schema(conn)
    rows = conn.execute(
        "SELECT media_id FROM media_upload_queue ORDER BY queued_at"
    ).fetchall()
    return [int(r[0]) for r in rows]


def remove_media_upload_queue(conn, media_id: int) -> None:
    conn.execute(
        "DELETE FROM media_upload_queue WHERE media_id = ?", (int(media_id),)
    )


def bump_media_upload_attempt(conn, media_id: int, error: str = "") -> None:
    conn.execute(
        """
        UPDATE media_upload_queue
        SET attempts = attempts + 1, last_error = ?
        WHERE media_id = ?
        """,
        (error[:500], int(media_id)),
    )


def notify_write(conn, table: str, row_id: int, *, op: str = "upsert") -> None:
    """Оновлює updated_at і ставить зміну в outbox."""
    if not sync_enabled():
        return
    sid = lookup_sync_id(conn, table, int(row_id))
    if not sid:
        sid = new_sync_id()
        ts = utc_now()
        conn.execute(
            f"UPDATE {table} SET sync_id = ?, updated_at = ? WHERE id = ?",
            (sid, ts, int(row_id)),
        )
    else:
        touch_row(conn, table, sid)
    sid = lookup_sync_id(conn, table, int(row_id))
    if sid:
        enqueue_outbox(conn, table, sid, op)
