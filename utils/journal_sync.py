# -*- coding: utf-8 -*-
"""Фонова двостороння синхронізація локальної SQLite ↔ Turso (LWW)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Any, Optional

import config as _cfg
from utils.db_backend import connect_local, connect_turso, set_turso_reach_cache, turso_reachable
from utils.sync_schema import (
    FK_SYNC_MAP,
    SYNC_TABLES,
    ensure_sync_schema,
    enqueue_outbox,
    get_meta,
    list_media_upload_queue,
    lookup_id_by_sync_id,
    not_deleted_sql,
    outbox_count,
    payload_with_sync_fks,
    remove_media_upload_queue,
    resolve_fks_from_sync_ids,
    row_dict,
    set_meta,
    sync_enabled,
    utc_now,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_running = False
_remote_schema_ready = False
_last_status: dict[str, Any] = {
    "enabled": False,
    "online": False,
    "syncing": False,
    "pending": 0,
    "last_pull_at": "",
    "last_push_at": "",
    "last_error": "",
    "message": "",
}

SYNC_INTERVAL_SEC = 45

# Колонки без id для upsert (Turso / local)
_DATA_COLS: dict[str, list[str]] = {}


def _data_columns(conn, table: str) -> list[str]:
    if table in _DATA_COLS:
        return _DATA_COLS[table]
    skip = {"id"}
    cols = [
        str(r[1])
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
        if str(r[1]) not in skip
    ]
    if cols:
        _DATA_COLS[table] = cols
    return cols


def _parse_ts(value: str) -> float:
    text = (value or "").strip()
    if not text:
        return 0.0
    try:
        from datetime import datetime

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _remote_row_ts(row: dict) -> float:
    return max(
        _parse_ts(row.get("updated_at") or ""),
        _parse_ts(row.get("deleted_at") or ""),
    )


def _adopt_sync_id(
    local: sqlite3.Connection,
    table: str,
    local_id: int,
    old_sid: str,
    new_sid: str,
) -> None:
    """Локальний рядок приймає sync_id хмари, щоб ідентичності зійшлись."""
    if not new_sid or old_sid == new_sid:
        return
    try:
        local.execute(
            f"UPDATE {table} SET sync_id = ? WHERE id = ?", (new_sid, int(local_id))
        )
        local.execute(
            "UPDATE sync_outbox SET sync_id = ? WHERE table_name = ? AND sync_id = ?",
            (new_sid, table, old_sid),
        )
    except sqlite3.IntegrityError:
        logger.warning("Adopt sync_id conflict: %s %s -> %s", table, old_sid, new_sid)


def _ensure_remote_parent(local, remote, parent_table: str, sid: str):
    """Гарантує наявність батьківського рядка на Turso; повертає remote id."""
    if not sid:
        return None
    row = remote.execute(
        f"SELECT id FROM {parent_table} WHERE sync_id = ?", (sid,)
    ).fetchone()
    if row:
        return int(row[0])
    if local is None:
        return None
    local_row = local.execute(
        f"SELECT * FROM {parent_table} WHERE sync_id = ?", (sid,)
    ).fetchone()
    if local_row is None:
        return None
    data = dict(local_row)
    if parent_table == "patients":
        pib = (data.get("pib") or "").strip()
        if pib:
            match = remote.execute(
                "SELECT id, sync_id FROM patients WHERE pib = ?", (pib,)
            ).fetchone()
            if match:
                if match["sync_id"] and match["sync_id"] != sid:
                    _adopt_sync_id(
                        local, "patients", int(data["id"]), sid, match["sync_id"]
                    )
                    local.commit()
                return int(match["id"])
    elif parent_table == "treatments":
        from utils.sync_schema import lookup_sync_id as _lookup_sid

        patient_sid = _lookup_sid(local, "patients", int(data.get("patient_id") or 0))
        remote_patient_id = _ensure_remote_parent(
            local, remote, "patients", patient_sid
        )
        if remote_patient_id:
            title = (data.get("title") or "").strip()
            started = data.get("started_on") or ""
            match = remote.execute(
                """
                SELECT id, sync_id FROM treatments
                WHERE patient_id = ? AND title = ? AND started_on = ?
                """,
                (remote_patient_id, title, started),
            ).fetchone()
            if match:
                if match["sync_id"] and match["sync_id"] != sid:
                    _adopt_sync_id(
                        local, "treatments", int(data["id"]), sid, match["sync_id"]
                    )
                    local.commit()
                return int(match["id"])
    payload = payload_with_sync_fks(local, parent_table, data)
    _upsert_remote(remote, parent_table, payload, local=local)
    row = remote.execute(
        f"SELECT id FROM {parent_table} WHERE sync_id = ?",
        (data.get("sync_id"),),
    ).fetchone()
    return int(row[0]) if row else None


def _resolve_remote_fks(conn, table: str, payload: dict, local=None) -> dict:
    """Підставляє integer FK на Turso з *_sync_id; за потреби пушить батьків."""
    out = dict(payload)
    fk_keys = {
        "patient_sync_id": ("patient_id", "patients"),
        "treatment_sync_id": ("treatment_id", "treatments"),
        "visit_sync_id": ("visit_id", "outpatient_entries"),
        "injury_case_sync_id": ("injury_case_id", "injury_cases"),
    }
    zero_ok = {"injury_case_id"}
    for sync_key, (fk_col, parent_table) in fk_keys.items():
        if sync_key not in out and fk_col not in FK_SYNC_MAP.get(table, {}):
            continue
        sid = out.pop(sync_key, "") or ""
        if not sid:
            if fk_col in zero_ok and out.get(fk_col) in (None, ""):
                out[fk_col] = 0
            continue
        remote_id = _ensure_remote_parent(local, conn, parent_table, sid)
        if remote_id is None and fk_col in zero_ok:
            out[fk_col] = 0
        else:
            out[fk_col] = remote_id
    return _coerce_not_null_zero_fks(table, out)


def align_patient_sync_ids(local: sqlite3.Connection, remote) -> int:
    """
    Вирівнює sync_id пацієнтів: однаковий ПІБ → sync_id з Turso.
    Інакше FK під час pull не знаходить батька і лікування «висять» лише на одному ПК.
    """
    n = 0
    try:
        rows = remote.execute(
            "SELECT sync_id, pib FROM patients WHERE COALESCE(deleted_at,'') = ''"
        ).fetchall()
    except Exception as exc:
        logger.warning("align_patient_sync_ids remote: %s", exc)
        return 0
    for row in rows:
        pib = (row["pib"] or "").strip()
        sid = (row["sync_id"] or "").strip()
        if not pib or not sid:
            continue
        by_sid = local.execute(
            "SELECT id FROM patients WHERE sync_id = ?", (sid,)
        ).fetchone()
        if by_sid:
            continue
        by_pib = local.execute(
            "SELECT id, sync_id FROM patients WHERE pib = ?", (pib,)
        ).fetchone()
        if not by_pib:
            continue
        old = str(by_pib["sync_id"] or "")
        if old == sid:
            continue
        # якщо цей sync_id уже зайнятий іншим рядком — не чіпаємо
        clash = local.execute(
            "SELECT id FROM patients WHERE sync_id = ? AND id != ?",
            (sid, int(by_pib["id"])),
        ).fetchone()
        if clash:
            logger.warning(
                "align skip %s: turso sync_id already on local id=%s",
                pib,
                clash["id"],
            )
            continue
        _adopt_sync_id(local, "patients", int(by_pib["id"]), old, sid)
        n += 1
    if n:
        local.commit()
        logger.info("Aligned patient sync_id by PIB: %s", n)
    return n


def _resolve_fks_from_remote_ids(
    local: sqlite3.Connection,
    remote,
    table: str,
    data: dict,
) -> dict:
    """Під час pull/bootstrap: Turso integer FK → local integer FK через sync_id."""
    # Колонки з NOT NULL DEFAULT 0 (немає поранення = 0, не NULL).
    zero_ok = {"injury_case_id"}
    out = dict(data)
    for fk_col, (parent_table, _) in FK_SYNC_MAP.get(table, {}).items():
        fk_val = out.get(fk_col)
        if fk_val in (None, "", 0):
            out[fk_col] = 0 if fk_col in zero_ok else None
            continue
        parent = remote.execute(
            f"SELECT * FROM {parent_table} WHERE id = ?", (int(fk_val),)
        ).fetchone()
        if not parent or not parent["sync_id"]:
            out[fk_col] = 0 if fk_col in zero_ok else None
            continue
        parent = dict(parent)
        local_id = lookup_id_by_sync_id(local, parent_table, parent["sync_id"])
        # Пацієнти часто мають різний sync_id на ПК і в хмарі (той самий ПІБ).
        # Без PIB-fallback лікування/записи журналу ніколи не підтягнуться.
        if local_id is None and parent_table == "patients":
            pib = (parent.get("pib") or "").strip()
            if pib:
                row = local.execute(
                    "SELECT id, sync_id FROM patients WHERE pib = ?", (pib,)
                ).fetchone()
                if row:
                    local_id = int(row["id"])
                    old_sid = str(row["sync_id"] or "")
                    new_sid = str(parent.get("sync_id") or "")
                    if new_sid and old_sid != new_sid:
                        _adopt_sync_id(local, "patients", local_id, old_sid, new_sid)
        if local_id is None and fk_col in zero_ok:
            out[fk_col] = 0
        else:
            out[fk_col] = local_id
    return out


def _coerce_not_null_zero_fks(table: str, data: dict) -> dict:
    """SQLite NOT NULL DEFAULT 0: ніколи не пишемо NULL у injury_case_id."""
    out = dict(data)
    if table in ("treatments", "treatment_media"):
        if out.get("injury_case_id") in (None, ""):
            out["injury_case_id"] = 0
    return out


def _coerce_text_not_null(table: str, data: dict, local: sqlite3.Connection) -> dict:
    """Turso інколи віддає NULL у TEXT NOT NULL DEFAULT '' — підставляємо ''."""
    out = dict(data)
    try:
        cols = {
            str(r[1]): (int(r[3] or 0), r[4])
            for r in local.execute(f"PRAGMA table_info({table})").fetchall()
        }
    except Exception:
        return out
    for name, (notnull, dflt) in cols.items():
        if name not in out:
            continue
        if out[name] is not None:
            continue
        if not notnull:
            continue
        # INTEGER NOT NULL без default (patient_id / treatment_id) — не чіпаємо
        if name.endswith("_id") and name not in ("injury_case_id",):
            continue
        if name == "injury_case_id":
            out[name] = 0
            continue
        if dflt is not None and str(dflt).upper() not in ("NULL",):
            # dflt у SQLite часто "'active'" або "''"
            text = str(dflt).strip()
            if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
                text = text[1:-1]
            out[name] = text
        else:
            out[name] = ""
    return out


def _safe_upsert_local(
    local: sqlite3.Connection,
    table: str,
    remote: dict,
    *,
    remote_conn=None,
) -> str:
    """
    Upsert одного рядка; IntegrityError не валить увесь sync (savepoint).
    Повертає: 'ok' | 'defer' (немає батька) | 'error'.
    """
    sid = (remote or {}).get("sync_id") or ""
    try:
        local.execute("SAVEPOINT sync_row")
    except Exception:
        try:
            ok = bool(_upsert_local(local, table, remote, remote_conn=remote_conn))
            return "ok" if ok else "defer"
        except Exception as exc:
            logger.warning("Sync skip row %s %s: %s", table, sid, exc)
            return "error"
    try:
        ok = bool(_upsert_local(local, table, remote, remote_conn=remote_conn))
        local.execute("RELEASE SAVEPOINT sync_row")
        return "ok" if ok else "defer"
    except Exception as exc:
        try:
            local.execute("ROLLBACK TO SAVEPOINT sync_row")
            local.execute("RELEASE SAVEPOINT sync_row")
        except Exception:
            try:
                local.rollback()
            except Exception:
                pass
        logger.warning("Sync skip row %s %s: %s", table, sid, exc)
        return "error"


def _upsert_local(
    local: sqlite3.Connection,
    table: str,
    remote: dict,
    *,
    remote_conn=None,
) -> bool:
    """Записує рядок з Turso в локальну БД. False = відкладено (немає батька)."""
    raw = dict(remote)
    data = dict(remote)
    if remote_conn is not None:
        data = _resolve_fks_from_remote_ids(local, remote_conn, table, data)
    else:
        data = resolve_fks_from_sync_ids(local, table, data)
    data = _coerce_not_null_zero_fks(table, data)
    data = _coerce_text_not_null(table, data, local)

    def _had_ref(fk_col: str, sync_key: str) -> bool:
        return raw.get(fk_col) not in (None, "", 0) or bool(raw.get(sync_key))

    if table == "treatments" and not data.get("patient_id"):
        return False
    if table == "injury_cases" and not data.get("patient_id"):
        return False
    if table == "treatment_restrictions" and not data.get("treatment_id"):
        return False
    if table == "treatment_media" and not data.get("treatment_id") and not data.get(
        "injury_case_id"
    ):
        return False
    # Медіа без локального treatment_id — чекаємо наступний pull (колонка NOT NULL).
    if (
        table == "treatment_media"
        and _had_ref("treatment_id", "treatment_sync_id")
        and not data.get("treatment_id")
    ):
        return False
    if table == "treatment_media" and not data.get("treatment_id"):
        return False
    # У журналі treatment_id необов'язковий: пропускаємо лише коли посилання
    # було, але батька ще нема локально (підтягнеться наступним pull).
    if (
        table == "outpatient_entries"
        and _had_ref("treatment_id", "treatment_sync_id")
        and not data.get("treatment_id")
    ):
        return False
    # Поранення ще не підтягнулось — не блокуємо лікування (ставимо 0).
    if (
        table == "treatments"
        and _had_ref("injury_case_id", "injury_case_sync_id")
        and not data.get("injury_case_id")
    ):
        data["injury_case_id"] = 0
    sid = data.get("sync_id") or ""
    if not sid:
        return False
    matched_by_pib = False
    local_row = local.execute(
        f"SELECT id FROM {table} WHERE sync_id = ?", (sid,)
    ).fetchone()
    if not local_row and table == "patients":
        pib = (data.get("pib") or "").strip()
        if pib:
            local_row = local.execute(
                "SELECT id, sync_id FROM patients WHERE pib = ?", (pib,)
            ).fetchone()
            matched_by_pib = local_row is not None
    if not local_row and table == "team_settings":
        key = (data.get("key") or "").strip()
        if key:
            local_row = local.execute(
                "SELECT id, sync_id FROM team_settings WHERE key = ?", (key,)
            ).fetchone()
            matched_by_pib = local_row is not None
    cols = [c for c in _data_columns(local, table) if c in data]
    if local_row:
        local_id = local_row["id"] if hasattr(local_row, "keys") else local_row[0]
        if matched_by_pib:
            # Хмара — джерело істини для ідентичності: приймаємо її sync_id.
            tbl = "patients" if table == "patients" else table
            _adopt_sync_id(
                local, tbl, int(local_id), str(local_row["sync_id"] or ""), sid
            )
        sets = ", ".join(f"{c} = ?" for c in cols if c != "sync_id")
        vals = [data[c] for c in cols if c != "sync_id"]
        if table == "patients" or matched_by_pib:
            local.execute(
                f"UPDATE {table} SET {sets} WHERE id = ?",
                vals + [local_id],
            )
        else:
            vals.append(sid)
            local.execute(f"UPDATE {table} SET {sets} WHERE sync_id = ?", vals)
    else:
        ins_cols = [c for c in cols if c in data]
        # Гарантуємо injury_case_id у INSERT навіть якщо колонки не було в remote payload
        if table in ("treatments", "treatment_media") and "injury_case_id" not in ins_cols:
            if "injury_case_id" in _data_columns(local, table):
                ins_cols.append("injury_case_id")
                data["injury_case_id"] = int(data.get("injury_case_id") or 0)
        placeholders = ", ".join("?" for _ in ins_cols)
        quoted = ", ".join(ins_cols)
        local.execute(
            f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})",
            [data[c] for c in ins_cols],
        )
    return True


def _upsert_remote(conn, table: str, payload: dict, local=None) -> None:
    data = _resolve_remote_fks(conn, table, dict(payload), local=local)
    data = _coerce_not_null_zero_fks(table, data)
    if table in ("treatments", "treatment_media"):
        data["injury_case_id"] = int(data.get("injury_case_id") or 0)
    sid = data.get("sync_id") or ""
    if not sid:
        return
    if table == "treatments" and not data.get("patient_id"):
        raise RuntimeError("на Turso не знайдено пацієнта для лікування")
    if table == "injury_cases" and not data.get("patient_id"):
        raise RuntimeError("на Turso не знайдено пацієнта для поранення")
    if table == "treatment_restrictions" and not data.get("treatment_id"):
        raise RuntimeError("на Turso не знайдено лікування для запису")
    if table == "treatment_media" and not data.get("treatment_id") and not int(
        data.get("injury_case_id") or 0
    ):
        raise RuntimeError("на Turso не знайдено лікування або випадок поранення для медіа")
    remote = conn.execute(
        f"SELECT id FROM {table} WHERE sync_id = ?", (sid,)
    ).fetchone()
    if not remote and table == "patients":
        pib = (data.get("pib") or "").strip()
        if pib:
            match = conn.execute(
                "SELECT id, sync_id FROM patients WHERE pib = ?", (pib,)
            ).fetchone()
            if match:
                remote = match
                remote_sid = str(match["sync_id"] or "") or sid
                if remote_sid != sid and local is not None:
                    from utils.sync_schema import lookup_id_by_sync_id as _lookup

                    lid = _lookup(local, "patients", sid)
                    if lid:
                        _adopt_sync_id(local, "patients", lid, sid, remote_sid)
                        local.commit()
                data["sync_id"] = remote_sid
                sid = remote_sid
    if not remote and table == "team_settings":
        key = (data.get("key") or "").strip()
        if key:
            match = conn.execute(
                "SELECT id, sync_id FROM team_settings WHERE key = ?", (key,)
            ).fetchone()
            if match:
                remote = match
                remote_sid = str(match["sync_id"] or "") or sid
                if remote_sid != sid and local is not None:
                    from utils.sync_schema import lookup_id_by_sync_id as _lookup

                    lid = _lookup(local, "team_settings", sid)
                    if lid:
                        _adopt_sync_id(local, "team_settings", lid, sid, remote_sid)
                        local.commit()
                data["sync_id"] = remote_sid
                sid = remote_sid
    if not remote and table == "treatments":
        patient_id = data.get("patient_id")
        title = (data.get("title") or "").strip()
        started = data.get("started_on") or ""
        if patient_id and title:
            match = conn.execute(
                """
                SELECT id, sync_id FROM treatments
                WHERE patient_id = ? AND title = ? AND started_on = ?
                """,
                (patient_id, title, started),
            ).fetchone()
            if match:
                remote = match
                remote_sid = str(match["sync_id"] or "") or sid
                if remote_sid != sid and local is not None:
                    from utils.sync_schema import lookup_id_by_sync_id as _lookup

                    lid = _lookup(local, "treatments", sid)
                    if lid:
                        _adopt_sync_id(local, "treatments", lid, sid, remote_sid)
                        local.commit()
                data["sync_id"] = remote_sid
                sid = remote_sid
    cols = [c for c in _data_columns(conn, table) if c in data]
    if table in ("treatments", "treatment_media"):
        if "injury_case_id" in _data_columns(conn, table):
            data["injury_case_id"] = int(data.get("injury_case_id") or 0)
            if "injury_case_id" not in cols:
                cols.append("injury_case_id")
    if remote:
        remote_id = remote["id"] if hasattr(remote, "keys") else remote[0]
        sets = ", ".join(f"{c} = ?" for c in cols if c != "sync_id")
        vals = [data[c] for c in cols if c != "sync_id"]
        conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", vals + [remote_id])
    else:
        ins_cols = [c for c in cols if c in data]
        placeholders = ", ".join("?" for _ in ins_cols)
        quoted = ", ".join(ins_cols)
        try:
            conn.execute(
                f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})",
                [data[c] for c in ins_cols],
            )
        except Exception as exc:
            text = str(exc).lower()
            if table == "patients" and "unique" in text and "pib" in text:
                pib = (data.get("pib") or "").strip()
                match = conn.execute(
                    "SELECT id, sync_id FROM patients WHERE pib = ?", (pib,)
                ).fetchone() if pib else None
                if not match:
                    raise
                remote_id = int(match["id"] if hasattr(match, "keys") else match[0])
                remote_sid = str(match["sync_id"] or "") or sid
                if remote_sid != sid and local is not None:
                    from utils.sync_schema import lookup_id_by_sync_id as _lookup

                    lid = _lookup(local, "patients", sid)
                    if lid:
                        _adopt_sync_id(local, "patients", lid, sid, remote_sid)
                        local.commit()
                sets = ", ".join(f"{c} = ?" for c in cols if c != "sync_id")
                vals = [data[c] for c in cols if c != "sync_id"]
                conn.execute(
                    f"UPDATE {table} SET {sets} WHERE id = ?", vals + [remote_id]
                )
            else:
                raise
    conn.commit()


def _ensure_remote_schema(conn) -> None:
    """Створює таблиці та sync-колонки на Turso (один раз за процес)."""
    global _remote_schema_ready
    if _remote_schema_ready:
        return
    from utils.patient_cards_db import ensure_card_schema
    from utils.team_tasks_db import ensure_team_tasks_schema

    ensure_team_tasks_schema(conn)
    ensure_card_schema(conn)
    ensure_sync_schema(conn)
    _remote_schema_ready = True


def _local_has_data(conn) -> bool:
    for table in SYNC_TABLES:
        try:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        except Exception:
            continue
        if row and int(row[0] if not isinstance(row, sqlite3.Row) else row["n"]) > 0:
            return True
    return False


def _local_needs_bootstrap(local: sqlite3.Connection) -> bool:
    if get_meta(local, "bootstrapped", ""):
        return False
    for table in ("patients", "treatments"):
        row = local.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        n = int(row["n"] if hasattr(row, "keys") else row[0])
        if n > 0:
            return False
    return True


def _bootstrap_from_turso(local: sqlite3.Connection, remote) -> None:
    logger.info("Journal sync bootstrap: Turso → local")
    for table in SYNC_TABLES:
        try:
            rows = remote.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
        except Exception as exc:
            logger.warning("Sync bootstrap skip table %s: %s", table, exc)
            continue
        n = 0
        for row in rows:
            if _safe_upsert_local(local, table, dict(row), remote_conn=remote) == "ok":
                n += 1
            if n % 25 == 0 and n:
                local.commit()
        if n:
            local.commit()
    set_meta(local, "last_pull_at", utc_now())
    set_meta(local, "bootstrapped", "1")
    local.commit()


def _pull(local: sqlite3.Connection, remote) -> int:
    since = get_meta(local, "last_pull_at", "1970-01-01T00:00:00+00:00")
    pulled = 0
    deferred = False
    for table in SYNC_TABLES:
        try:
            rows = remote.execute(
                f"SELECT * FROM {table} WHERE updated_at > ? OR deleted_at > ?",
                (since, since),
            ).fetchall()
        except Exception as exc:
            logger.warning("Sync pull skip table %s: %s", table, exc)
            continue
        table_n = 0
        for row in rows:
            remote_row = dict(row)
            sid = remote_row.get("sync_id") or ""
            if not sid:
                continue
            local_row = row_dict(local, table, sid)
            if local_row and _parse_ts(local_row.get("updated_at") or "") > _remote_row_ts(remote_row):
                # Довідки: метадані injury_cert з хмари завжди важливіші за локальний
                # «новіший» рядок (часто kind='' / injury_case_id=0 після старого sync).
                remote_kind = (remote_row.get("kind") or "").strip()
                if not (
                    table == "treatment_media" and remote_kind == "injury_cert"
                ):
                    logger.debug("Sync pull skip (local wins): %s %s", table, sid)
                    continue
            result = _safe_upsert_local(local, table, remote_row, remote_conn=remote)
            if result == "defer":
                # Немає батька — не зсуваємо курсор, щоб рядок прийшов знову.
                deferred = True
                continue
            if result != "ok":
                continue
            pulled += 1
            table_n += 1
            # Короткі транзакції: UI не має чекати весь pull (database is locked).
            if table_n % 25 == 0:
                local.commit()
        if table_n:
            local.commit()
    if deferred:
        logger.info("Sync pull deferred — keep last_pull_at=%s", since)
    else:
        set_meta(local, "last_pull_at", utc_now())
    local.commit()
    return pulled


def _push(local: sqlite3.Connection, remote) -> int:
    rows = local.execute(
        "SELECT * FROM sync_outbox ORDER BY id ASC LIMIT 500"
    ).fetchall()
    order = {name: idx for idx, name in enumerate(SYNC_TABLES)}
    items = sorted(rows, key=lambda r: (order.get(r["table_name"], 99), int(r["id"])))
    pushed = 0
    for item in items:
        table = item["table_name"]
        sync_id = item["sync_id"]
        fresh = row_dict(local, table, sync_id)
        if fresh:
            payload = payload_with_sync_fks(local, table, fresh)
        else:
            payload = json.loads(item["payload_json"] or "{}")
        remote_row = remote.execute(
            f"SELECT * FROM {table} WHERE sync_id = ?", (sync_id,)
        ).fetchone()
        if remote_row and _remote_row_ts(dict(remote_row)) > _parse_ts(payload.get("updated_at") or ""):
            logger.info(
                "Sync push conflict (remote wins): %s %s", table, sync_id
            )
            local.execute("DELETE FROM sync_outbox WHERE id = ?", (item["id"],))
            continue
        try:
            _upsert_remote(remote, table, payload, local=local)
        except Exception as exc:
            logger.warning("Sync push skip %s %s: %s", table, sync_id, exc)
            local.execute(
                "UPDATE sync_outbox SET attempts = attempts + 1 WHERE id = ?",
                (item["id"],),
            )
            continue
        local.execute("DELETE FROM sync_outbox WHERE id = ?", (item["id"],))
        pushed += 1
        local.commit()
    if pushed:
        set_meta(local, "last_push_at", utc_now())
        local.commit()
    return pushed


def _sync_media_uploads(local: Optional[sqlite3.Connection] = None) -> int:
    if not _cfg.DROPBOX_ACCESS_TOKEN and not _cfg.DROPBOX_REFRESH_TOKEN:
        return 0
    from utils import patient_cards_db as cards_db

    own = local is None
    if own:
        local = connect_local()
    uploaded = 0
    try:
        for media_id in list_media_upload_queue(local):
            media = cards_db.get_media(media_id)
            if not media:
                remove_media_upload_queue(local, media_id)
                continue
            path = cards_db.resolve_media_file_path(media)
            if not path:
                continue
            try:
                from app import _push_media_to_dropbox

                if _push_media_to_dropbox(int(media["treatment_id"]), media):
                    remove_media_upload_queue(local, media_id)
                    uploaded += 1
            except Exception as exc:
                from utils.sync_schema import bump_media_upload_attempt

                bump_media_upload_attempt(local, media_id, str(exc))
        local.commit()
    finally:
        if own:
            local.close()
    return uploaded


def _friendly_sync_error(exc: BaseException | str) -> str:
    text = str(exc or "").strip()
    low = text.casefold()
    if "timed out" in low or "timeout" in low:
        return "Хмара не відповідає (таймаут). Журнал збережено локально."
    if "turso недоступна" in low or "name or service not known" in low:
        return "Немає зв'язку з хмарою. Журнал збережено локально."
    if "injury_case_id" in low and "not null" in low:
        return (
            "Тимчасова помилка синхронізації лікування. "
            "Натисніть індикатор «Синх» ще раз."
        )
    if "not null constraint" in low:
        return (
            "Тимчасова помилка синхронізації даних. "
            "Натисніть індикатор «Синх» ще раз."
        )
    if "unique constraint" in low and "pib" in low:
        return "Конфлікт ПІБ під час синхронізації. Натисніть «Синх» ще раз."
    # Старі тексти після оновлення програми — не показуємо як актуальну помилку
    if "встановіть оновлення" in low or "1.2.11" in low:
        return ""
    return text[:300]


def clear_stale_errors_on_upgrade() -> None:
    """
    Після встановлення нової версії прибирає застарілий last_error
    (наприклад «встановіть 1.2.11+»), щоб не лякав після вже зробленого оновлення.
    """
    if not sync_enabled():
        return
    try:
        import config as cfg

        from utils.app_update import version_newer

        ver = (getattr(cfg, "APP_VERSION", "") or "").strip()
        local = connect_local()
        ensure_sync_schema(local)
        prev = get_meta(local, "app_version_seen", "")
        err = (get_meta(local, "last_error", "") or "").strip()
        low = err.casefold()
        stale = (
            "встановіть оновлення" in low
            or "1.2.11" in low
            or ("injury_case_id" in low and "not null" in low)
            or _is_connectivity_error(err)
        )
        if ver and (prev != ver or stale):
            if stale or (prev and version_newer(ver, prev)):
                set_meta(local, "last_error", "")
                _last_status["last_error"] = ""
            set_meta(local, "app_version_seen", ver)
            local.commit()
        local.close()
    except Exception:
        logger.debug("clear_stale_errors_on_upgrade failed", exc_info=True)


def run_sync(*, force: bool = False) -> dict[str, Any]:
    """Один цикл pull + push + media queue."""
    global _last_status
    clear_stale_errors_on_upgrade()
    if not sync_enabled():
        _last_status = {
            "enabled": False,
            "online": False,
            "syncing": False,
            "pending": 0,
            "last_pull_at": "",
            "last_push_at": "",
            "last_error": "",
            "message": "Синхронізація вимкнена (локальний режим)",
        }
        return dict(_last_status)

    if force:
        acquired = _lock.acquire(timeout=20)
    else:
        acquired = _lock.acquire(blocking=False)
    if not acquired:
        return dict(_last_status)

    try:
        _last_status["enabled"] = True
        _last_status["syncing"] = True
        local = connect_local()
        local.execute("PRAGMA foreign_keys = ON")
        from utils.team_tasks_db import ensure_team_tasks_schema

        ensure_team_tasks_schema(local)
        ensure_sync_schema(local)
        pending = outbox_count(local)
        _last_status["pending"] = pending
        _last_status["last_pull_at"] = get_meta(local, "last_pull_at", "")
        _last_status["last_push_at"] = get_meta(local, "last_push_at", "")

        if not turso_reachable(force=True):
            _last_status["online"] = False
            _last_status["message"] = "Офлайн — зміни збережено локально"
            _last_status["last_error"] = ""
            set_turso_reach_cache(False)
            local.close()
            return dict(_last_status)

        _last_status["online"] = True
        set_turso_reach_cache(True)
        remote = connect_turso()
        try:
            _ensure_remote_schema(remote)
            bootstrapped = get_meta(local, "bootstrapped", "")
            aligned = 0
            try:
                aligned = align_patient_sync_ids(local, remote)
            except Exception as exc:
                logger.warning("align_patient_sync_ids: %s", exc)
            if aligned:
                # Старі лікування вже «пройшли» watermark без батька — тягнемо знову.
                set_meta(local, "last_pull_at", "1970-01-01T00:00:00+00:00")
                local.commit()
                logger.info(
                    "Reset last_pull_at after aligning %s patient sync_ids", aligned
                )
            if _local_needs_bootstrap(local):
                _bootstrap_from_turso(local, remote)
            elif not bootstrapped:
                set_meta(local, "bootstrapped", "1")
                local.commit()
            pulled = _pull(local, remote)
            pushed = _push(local, remote)
            media_n = _sync_media_uploads(local)
            _last_status["pending"] = outbox_count(local)
            _last_status["last_pull_at"] = get_meta(local, "last_pull_at", "")
            _last_status["last_push_at"] = get_meta(local, "last_push_at", "")
            _last_status["last_error"] = ""
            set_meta(local, "last_error", "")
            pending_left = _last_status["pending"]
            _last_status["message"] = (
                f"Синхронізовано: pull {pulled}, push {pushed}"
                + (f", media {media_n}" if media_n else "")
                + (
                    f" — ще {pending_left} у черзі"
                    if pending_left
                    else ""
                )
            )
            logger.info("Journal sync OK: pull=%s push=%s media=%s", pulled, pushed, media_n)
            set_turso_reach_cache(True)
            if pulled or pushed:
                try:
                    from utils import db_cache

                    db_cache.invalidate_all()
                except Exception:
                    pass
        finally:
            remote.close()
            local.close()
    except Exception as exc:
        logger.exception("Journal sync failed")
        set_turso_reach_cache(False)
        friendly = _friendly_sync_error(exc)
        _last_status["online"] = False
        _last_status["last_error"] = friendly
        _last_status["message"] = "Помилка синхронізації"
        try:
            err_conn = connect_local()
            set_meta(err_conn, "last_error", friendly)
            err_conn.commit()
            err_conn.close()
        except Exception:
            pass
    finally:
        _last_status["syncing"] = False
        if acquired:
            _lock.release()

    return dict(_last_status)


def _is_connectivity_error(text: str) -> bool:
    low = (text or "").casefold()
    return any(
        token in low
        for token in (
            "немає зв'язку з хмарою",
            "хмара не відповідає",
            "таймаут",
            "timeout",
            "офлайн",
            "turso недоступна",
            "name or service not known",
        )
    )


def get_status() -> dict[str, Any]:
    status = dict(_last_status)
    if sync_enabled():
        clear_stale_errors_on_upgrade()
        try:
            local = connect_local()
            ensure_sync_schema(local)
            status["pending"] = outbox_count(local)
            status["last_pull_at"] = get_meta(local, "last_pull_at", "")
            status["last_push_at"] = get_meta(local, "last_push_at", "")
            status["last_error"] = _friendly_sync_error(
                get_meta(local, "last_error", "")
            )
            # Якщо хмара знову доступна — прибрати застарілу «немає зв'язку»
            if not status.get("syncing"):
                online_now = turso_reachable()
                status["online"] = online_now
                err_now = (status.get("last_error") or "").strip()
                if online_now and err_now and _is_connectivity_error(err_now):
                    status["last_error"] = ""
                    set_meta(local, "last_error", "")
                    local.commit()
                    _last_status["last_error"] = ""
                    _last_status["online"] = True
            local.close()
        except Exception:
            pass
        if not status.get("syncing") and "online" not in status:
            status["online"] = turso_reachable()
        pending = int(status.get("pending") or 0)
        err = (status.get("last_error") or "").strip()
        locked = "database is locked" in err.lower()
        if err and not locked and not (
            status.get("online") and _is_connectivity_error(err)
        ):
            status["message"] = "Помилка синхронізації"
        elif pending > 0 and status.get("online"):
            status["message"] = (
                f"Онлайн — {pending} локальних змін чекають відправки в хмару"
            )
            if locked:
                status["last_error"] = ""
        elif pending > 0:
            status["message"] = (
                f"Офлайн — {pending} змін збережено локально"
            )
        elif status.get("online"):
            status["message"] = status.get("message") or "Журнал синхронізовано"
        else:
            status["message"] = status.get("message") or "Офлайн"
    status["enabled"] = sync_enabled()
    return status


def _daemon_loop() -> None:
    global _running
    time.sleep(3)
    while _running:
        try:
            run_sync()
        except Exception:
            logger.exception("Sync daemon iteration")
        time.sleep(SYNC_INTERVAL_SEC)


def start_daemon() -> None:
    global _running
    if not sync_enabled() or _running:
        return
    clear_stale_errors_on_upgrade()
    _running = True
    thread = threading.Thread(target=_daemon_loop, name="journal-sync", daemon=True)
    thread.start()
    logger.info("Journal sync daemon started (interval %ss)", SYNC_INTERVAL_SEC)


def stop_daemon() -> None:
    global _running
    _running = False


def notify_local_change() -> None:
    """Легкий тригер після локального запису (не блокує UI)."""
    if not sync_enabled():
        return
    threading.Thread(
        target=lambda: run_sync(force=False),
        name="journal-sync-trigger",
        daemon=True,
    ).start()
