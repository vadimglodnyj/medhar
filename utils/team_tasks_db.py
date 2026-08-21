# -*- coding: utf-8 -*-
"""Kanban-задачі команди: локальна SQLite + Turso sync."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

from utils import db_cache
from utils.db_backend import connect as db_connect
from utils.sync_schema import (
    enqueue_outbox,
    new_sync_id,
    not_deleted_sql,
    sync_enabled,
    touch_row,
    utc_now,
)

COLUMNS = (
    {"id": "todo", "label": "До виконання"},
    {"id": "doing", "label": "В роботі"},
    {"id": "done", "label": "Готово"},
)
STATUS_IDS = tuple(c["id"] for c in COLUMNS)

_schema_ready = False


def _connect():
    conn = db_connect()
    try:
        conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        pass
    return conn


def _commit(conn) -> None:
    conn.commit()
    db_cache.invalidate_all()


def _notify_sync() -> None:
    if not sync_enabled():
        return
    try:
        from utils.journal_sync import notify_local_change

        notify_local_change()
    except Exception:
        pass


def ensure_team_tasks_schema(conn=None) -> None:
    """Створює таблицю team_tasks (локально і на Turso)."""
    global _schema_ready
    own = conn is None
    is_local = own or isinstance(conn, sqlite3.Connection)
    if is_local and own and _schema_ready:
        return
    if own:
        conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS team_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL DEFAULT '',
                value TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                sync_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                deleted_at TEXT NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_team_settings_key
                ON team_settings(key);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_team_settings_sync_id
                ON team_settings(sync_id);
            CREATE TABLE IF NOT EXISTS team_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                discord_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                sync_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                deleted_at TEXT NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_team_members_sync_id
                ON team_members(sync_id);
            CREATE TABLE IF NOT EXISTS team_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'todo',
                position INTEGER NOT NULL DEFAULT 0,
                assignee TEXT NOT NULL DEFAULT '',
                member_sync_id TEXT NOT NULL DEFAULT '',
                deadline TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                sync_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                deleted_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_team_tasks_status
                ON team_tasks(status, position);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_team_tasks_sync_id
                ON team_tasks(sync_id);
            CREATE TABLE IF NOT EXISTS vlk_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pib TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                passed TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                sync_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                deleted_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_vlk_queue_position
                ON vlk_queue(position);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_vlk_queue_sync_id
                ON vlk_queue(sync_id);
            """
        )
        tcols = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(team_tasks)").fetchall()
        }
        if tcols and "member_sync_id" not in tcols:
            conn.execute(
                "ALTER TABLE team_tasks ADD COLUMN member_sync_id TEXT NOT NULL DEFAULT ''"
            )
        if tcols and "deadline" not in tcols:
            conn.execute(
                "ALTER TABLE team_tasks ADD COLUMN deadline TEXT NOT NULL DEFAULT ''"
            )
        mcols = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(team_members)").fetchall()
        }
        if mcols and "discord_id" not in mcols:
            conn.execute(
                "ALTER TABLE team_members ADD COLUMN discord_id TEXT NOT NULL DEFAULT ''"
            )
        vcols = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(vlk_queue)").fetchall()
        }
        if vcols and "passed" not in vcols:
            conn.execute(
                "ALTER TABLE vlk_queue ADD COLUMN passed TEXT NOT NULL DEFAULT ''"
            )
        conn.commit()
        if is_local:
            _schema_ready = True
            if own:
                _restore_team_roster(conn)
                conn.commit()
                persist_team_roster()
    finally:
        if own:
            conn.close()


def _row_to_dict(row) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {str(k): row[k] for k in row.keys()}
    return dict(row)


def _normalize_id_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = [str(x).strip() for x in value]
    else:
        text = str(value).strip()
        if not text:
            return []
        items = [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def parse_member_sync_ids(value) -> list[str]:
    return _normalize_id_list(value)


def _member_index() -> tuple[dict[str, dict], dict[str, dict]]:
    by_sid: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for member in list_members():
        sid = str(member.get("sync_id") or "").strip()
        name = _clean_text(member.get("name"), 120).casefold()
        if sid:
            by_sid[sid] = member
        if name:
            by_name[name] = member
    return by_sid, by_name


def _decorate_task(
    task: dict,
    by_sid: Optional[dict[str, dict]] = None,
    by_name: Optional[dict[str, dict]] = None,
) -> dict:
    if not task:
        return task
    task["deadline"] = task.get("deadline") or ""
    task["deadline_overdue"] = deadline_overdue(task["deadline"])
    if by_sid is None or by_name is None:
        by_sid, by_name = _member_index()
    ids = parse_member_sync_ids(task.get("member_sync_id"))
    names = [part.strip() for part in str(task.get("assignee") or "").split(",") if part.strip()]
    assignees: list[dict] = []
    seen: set[str] = set()
    for i, sid in enumerate(ids):
        member = by_sid.get(sid) or {}
        name = str(member.get("name") or "").strip() or (names[i] if i < len(names) else "")
        key = sid or name.casefold()
        if key in seen:
            continue
        seen.add(key)
        assignees.append(
            {
                "name": name or sid,
                "sync_id": sid,
                "discord_id": str(member.get("discord_id") or "").strip(),
            }
        )
    if not assignees:
        for name in names:
            member = by_name.get(name.casefold()) or {}
            sid = str(member.get("sync_id") or "").strip()
            key = sid or name.casefold()
            if key in seen:
                continue
            seen.add(key)
            assignees.append(
                {
                    "name": name,
                    "sync_id": sid,
                    "discord_id": str(member.get("discord_id") or "").strip(),
                }
            )
    task["assignees"] = assignees
    task["member_sync_ids"] = [a["sync_id"] for a in assignees if a.get("sync_id")]
    if assignees:
        task["assignee"] = ", ".join(a["name"] for a in assignees if a.get("name"))
        task["member_sync_id"] = ",".join(a["sync_id"] for a in assignees if a.get("sync_id"))
    return task


def _clean_status(value: str) -> str:
    text = str(value or "").strip()
    return text if text in STATUS_IDS else "todo"


def _clean_text(value, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def normalize_deadline(value: str) -> str:
    text = _clean_text(value, 16)
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    if len(digits) == 8:
        text = f"{digits[0:2]}.{digits[2:4]}.{digits[4:8]}"
    try:
        dt = datetime.strptime(text, "%d.%m.%Y")
    except ValueError as exc:
        raise ValueError("Дедлайн має бути у форматі дд.мм.рррр") from exc
    if dt.year < 1900 or dt.year > 2200:
        raise ValueError("Дедлайн має бути у форматі дд.мм.рррр")
    return dt.strftime("%d.%m.%Y")


def deadline_overdue(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    try:
        dt = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        return False
    return dt < datetime.now().date()


SETTING_DISCORD_WEBHOOK = "discord_webhook"
SETTING_MANAGER_SYNC_IDS = "manager_sync_ids"
SETTING_VLK_DATE = "vlk_date"
SETTING_VLK_ORDER = "vlk_queue_order"

_ROSTER_FILENAME = "team_roster.json"
_roster_io = False


def _roster_file_paths() -> list[str]:
    paths: list[str] = []
    try:
        import config as cfg

        data_dir = getattr(cfg, "DATA_DIR", "") or ""
        if data_dir:
            paths.append(os.path.join(data_dir, _ROSTER_FILENAME))
        excel_dir = getattr(cfg, "EXCEL_DATA_DIR", "") or ""
        if excel_dir and os.path.normpath(excel_dir) != os.path.normpath(data_dir):
            paths.append(os.path.join(excel_dir, _ROSTER_FILENAME))
    except Exception:
        pass
    return paths


def _read_team_roster_file() -> dict:
    for path in _roster_file_paths():
        try:
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (OSError, TypeError, ValueError):
            continue
    return {}


def persist_team_roster() -> None:
    """Копія webhook + команди в JSON (AppData і папка Excel/Dropbox)."""
    global _roster_io
    if _roster_io:
        return
    _roster_io = True
    try:
        payload = {
            "discord_webhook": get_setting(SETTING_DISCORD_WEBHOOK),
            "manager_sync_ids": get_manager_sync_ids(),
            "members": [
                {
                    "sync_id": str(m.get("sync_id") or "").strip(),
                    "name": str(m.get("name") or "").strip(),
                    "discord_id": str(m.get("discord_id") or "").strip(),
                    "phone": str(m.get("phone") or "").strip(),
                }
                for m in list_members()
            ],
        }
        if not payload["discord_webhook"] and not payload["members"]:
            return
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        for path in _roster_file_paths():
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(raw)
            except OSError:
                continue
    finally:
        _roster_io = False


def _restore_team_roster(conn) -> None:
    """Якщо в БД порожньо (нова версія / новий ПК) — підтягнути JSON."""
    global _roster_io
    if _roster_io:
        return
    data = _read_team_roster_file()
    if not data:
        return
    try:
        n_members = int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM team_members WHERE {not_deleted_sql()}",
            ).fetchone()[0]
        )
    except Exception:
        return
    hook_row = conn.execute(
        f"""
        SELECT value FROM team_settings
        WHERE key = ? AND {not_deleted_sql()}
        """,
        (SETTING_DISCORD_WEBHOOK,),
    ).fetchone()
    hook_now = str(hook_row[0] if hook_row else "") if hook_row is not None else ""
    if n_members and hook_now:
        return
    _roster_io = True
    try:
        ts = utc_now()
        if not hook_now:
            hook = str(data.get("discord_webhook") or "").strip()
            if hook:
                sid = new_sync_id()
                conn.execute(
                    """
                    INSERT INTO team_settings (key, value, created_at, sync_id, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (SETTING_DISCORD_WEBHOOK, hook, ts, sid, ts),
                )
                enqueue_outbox(conn, "team_settings", sid, "upsert")
        existing_sids = {
            str(r[0])
            for r in conn.execute(
                f"SELECT sync_id FROM team_members WHERE {not_deleted_sql()}"
            ).fetchall()
            if r[0]
        }
        existing_names = {
            str(r[0]).casefold()
            for r in conn.execute(
                f"SELECT name FROM team_members WHERE {not_deleted_sql()}"
            ).fetchall()
            if r[0]
        }
        restored_sids: list[str] = []
        for item in data.get("members") or []:
            if not isinstance(item, dict):
                continue
            name = _clean_text(item.get("name"), 120)
            if not name or name.casefold() in existing_names:
                if name:
                    restored_sids.append(str(item.get("sync_id") or "").strip())
                continue
            sid = str(item.get("sync_id") or "").strip() or new_sync_id()
            if sid in existing_sids:
                continue
            discord_n = str(item.get("discord_id") or "").strip()
            phone_n = str(item.get("phone") or "").strip()
            conn.execute(
                """
                INSERT INTO team_members
                (name, phone, discord_id, created_at, sync_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, phone_n, discord_n, ts, sid, ts),
            )
            enqueue_outbox(conn, "team_members", sid, "upsert")
            existing_sids.add(sid)
            existing_names.add(name.casefold())
            restored_sids.append(sid)
        mgr_now = conn.execute(
            f"""
            SELECT value FROM team_settings
            WHERE key = ? AND {not_deleted_sql()}
            """,
            (SETTING_MANAGER_SYNC_IDS,),
        ).fetchone()
        if not (mgr_now and str(mgr_now[0] or "").strip()):
            managers = [
                str(x).strip()
                for x in (data.get("manager_sync_ids") or [])
                if str(x).strip()
            ]
            if not managers:
                managers = [s for s in restored_sids if s]
            if managers:
                sid = new_sync_id()
                conn.execute(
                    """
                    INSERT INTO team_settings (key, value, created_at, sync_id, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (SETTING_MANAGER_SYNC_IDS, ",".join(managers), ts, sid, ts),
                )
                enqueue_outbox(conn, "team_settings", sid, "upsert")
    finally:
        _roster_io = False

VLK_DOCTORS = (
    {"id": "surgeon", "label": "Хірург", "short": "Хірург"},
    {"id": "ent", "label": "Отоларинголог", "short": "ЛОР"},
    {"id": "eye", "label": "Офтальмолог", "short": "Офт"},
    {"id": "therapist", "label": "Терапевт", "short": "Тер"},
    {"id": "derm", "label": "Дерматолог", "short": "Дерм"},
    {"id": "dentist", "label": "Стоматолог", "short": "Стомат"},
    {"id": "neuro", "label": "Невропатолог", "short": "Невр"},
    {"id": "tests", "label": "Тести", "short": "Тести"},
)
VLK_DOCTOR_IDS = tuple(d["id"] for d in VLK_DOCTORS)


def parse_vlk_passed(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(x).strip() for x in value]
    else:
        raw = [part.strip() for part in str(value).replace(";", ",").split(",") if part.strip()]
    known = set(VLK_DOCTOR_IDS)
    out = []
    seen = set()
    for item in raw:
        if item in known and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def serialize_vlk_passed(value) -> str:
    return ",".join(parse_vlk_passed(value))


def _decorate_vlk(item: dict) -> dict:
    if not item:
        return item
    item["passed"] = parse_vlk_passed(item.get("passed"))
    return item


def next_vlk_copy_date(today=None) -> str:
    """Наступний робочий день: завжди +1, у п’ятницю — понеділок."""
    if today is None:
        d = datetime.now().date()
    elif hasattr(today, "date"):
        d = today.date()
    else:
        d = today
    wd = d.weekday()
    if wd == 4:
        delta = 3
    elif wd == 5:
        delta = 2
    elif wd == 6:
        delta = 1
    else:
        delta = 1
    return (d + timedelta(days=delta)).strftime("%d.%m.%Y")


def list_vlk_copy_people() -> list[dict]:
    """Люди з активним ВЛК у журналі — для черги / статусу WhatsApp."""
    from utils import patient_cards_db as cards_db

    out = []
    for person in cards_db.list_active_vlk_people():
        pid = person.get("patient_id")
        try:
            pid_n = int(pid) if pid not in (None, "") else 0
        except (TypeError, ValueError):
            pid_n = 0
        out.append(
            {
                "id": int(person.get("id") or 0),
                "pib": person.get("pib") or "",
                "note": person.get("lpz") or "",
                "passed": parse_vlk_passed(person.get("vlk_passed")),
                "patient_id": pid_n,
            }
        )
    order = _vlk_saved_order_ids()
    rank = {vid: i for i, vid in enumerate(order)}
    out.sort(
        key=lambda x: (
            rank.get(int(x.get("id") or 0), 10_000),
            (x.get("pib") or "").casefold(),
        )
    )
    return out


def _vlk_saved_order_ids() -> list[int]:
    raw = get_setting(SETTING_VLK_ORDER, "") or ""
    try:
        data = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    ids = []
    seen = set()
    for item in data:
        try:
            vid = int(item)
        except (TypeError, ValueError):
            continue
        if vid and vid not in seen:
            ids.append(vid)
            seen.add(vid)
    return ids


def reorder_vlk_copy_people(ordered_ids: list) -> list[dict]:
    """Зберігає порядок плиток подачі на ВЛК (id звернень журналу)."""
    people = {int(p["id"]): p for p in list_vlk_copy_people() if int(p.get("id") or 0)}
    clean: list[int] = []
    seen = set()
    for raw in ordered_ids or []:
        try:
            vid = int(raw)
        except (TypeError, ValueError):
            continue
        if vid in people and vid not in seen:
            clean.append(vid)
            seen.add(vid)
    for person in people.values():
        vid = int(person["id"])
        if vid not in seen:
            clean.append(vid)
    set_setting(SETTING_VLK_ORDER, json.dumps(clean, ensure_ascii=False))
    return list_vlk_copy_people()


def format_vlk_queue_copy(items: list[dict], date: str = "") -> str:
    due = (date or "").strip()
    lines = ["Подача на чергу ВЛК" + (f" {due}" if due else "")]
    for item in items or []:
        pib = _clean_text(item.get("pib"), 200)
        if pib:
            lines.append(pib)
    return "\n".join(lines).strip()


def format_vlk_status_copy(items: list[dict], date: str = "") -> str:
    due = (date or "").strip()
    lines = [f"ВЛК на {due}" if due else "ВЛК"]
    labels = {d["id"]: d["label"] for d in VLK_DOCTORS}
    for item in items or []:
        pib = _clean_text(item.get("pib"), 200)
        if not pib:
            continue
        passed = set(parse_vlk_passed(item.get("passed")))
        if not passed:
            lines.append(f"{pib} - вперше")
            continue
        done = [labels[did] for did in VLK_DOCTOR_IDS if did in passed]
        left = [labels[did] for did in VLK_DOCTOR_IDS if did not in passed]
        if not left:
            lines.append(f"{pib} пройшов усе")
        else:
            lines.append(
                f"{pib} пройшов {', '.join(done)}, залишились {', '.join(left)}"
            )
    return "\n".join(lines).strip()


def normalize_discord_id(value: str, *, required: bool = False) -> str:
    from utils.discord_api import DiscordError, normalize_user_id

    try:
        uid = normalize_user_id(value)
    except DiscordError as exc:
        raise ValueError(str(exc)) from exc
    if required and not uid:
        raise ValueError("Вкажіть Discord ID виконавця")
    return uid


def get_setting(key: str, default: str = "") -> str:
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        row = conn.execute(
            f"""
            SELECT value FROM team_settings
            WHERE key = ? AND {not_deleted_sql()}
            """,
            (str(key),),
        ).fetchone()
        if not row:
            return default
        return str(row["value"] if hasattr(row, "keys") else row[0] or default)
    finally:
        conn.close()


def set_setting(key: str, value: str) -> str:
    key_n = _clean_text(key, 80)
    if not key_n:
        raise ValueError("Порожній ключ налаштування")
    val_n = str(value or "").strip()
    if key_n == SETTING_DISCORD_WEBHOOK:
        from utils.discord_api import DiscordError, normalize_webhook_url

        try:
            val_n = normalize_webhook_url(val_n)
        except DiscordError as exc:
            raise ValueError(str(exc)) from exc
    elif key_n == SETTING_VLK_DATE:
        val_n = normalize_deadline(val_n) if val_n else ""
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT id, sync_id FROM team_settings WHERE key = ? AND {not_deleted_sql()}",
            (key_n,),
        ).fetchone()
        ts = utc_now()
        if row:
            rid = int(row["id"] if hasattr(row, "keys") else row[0])
            sid = str(row["sync_id"] if hasattr(row, "keys") else row[1] or "") or new_sync_id()
            conn.execute(
                """
                UPDATE team_settings
                SET value = ?, sync_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (val_n, sid, ts, rid),
            )
        else:
            sid = new_sync_id()
            conn.execute(
                """
                INSERT INTO team_settings (key, value, created_at, sync_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (key_n, val_n, ts, sid, ts),
            )
        enqueue_outbox(conn, "team_settings", sid, "upsert")
        _commit(conn)
        _notify_sync()
        if key_n in (SETTING_DISCORD_WEBHOOK, SETTING_MANAGER_SYNC_IDS):
            persist_team_roster()
        return val_n
    finally:
        conn.close()


def get_manager_sync_ids() -> list[str]:
    return parse_member_sync_ids(get_setting(SETTING_MANAGER_SYNC_IDS))


def set_manager_sync_ids(value) -> list[str]:
    by_sid, _ = _member_index()
    ids = [sid for sid in _normalize_id_list(value) if sid in by_sid]
    set_setting(SETTING_MANAGER_SYNC_IDS, ",".join(ids))
    return ids


def list_managers() -> list[dict]:
    by_sid, _ = _member_index()
    out: list[dict] = []
    seen: set[str] = set()
    for sid in get_manager_sync_ids():
        member = by_sid.get(sid)
        if not member:
            continue
        key = str(member.get("sync_id") or sid)
        if key in seen:
            continue
        seen.add(key)
        out.append(member)
    return out


def normalize_member_phone(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return ""
    if digits.startswith("0") and len(digits) == 10:
        digits = "38" + digits
    if digits.startswith("80") and len(digits) == 11:
        digits = "3" + digits
    return digits


def _member_row(conn, member_id: int) -> Optional[dict]:
    row = conn.execute(
        f"SELECT * FROM team_members WHERE id = ? AND {not_deleted_sql()}",
        (int(member_id),),
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_members() -> list[dict]:
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM team_members
            WHERE {not_deleted_sql()}
            ORDER BY name COLLATE NOCASE ASC, id ASC
            """
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_member(member_id: int) -> Optional[dict]:
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        return _member_row(conn, member_id)
    finally:
        conn.close()


def get_member_by_sync_id(sync_id: str) -> Optional[dict]:
    sid = (sync_id or "").strip()
    if not sid:
        return None
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT * FROM team_members WHERE sync_id = ? AND {not_deleted_sql()}",
            (sid,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def find_member_by_name(name: str) -> Optional[dict]:
    needle = _clean_text(name, 120).casefold()
    if not needle:
        return None
    for member in list_members():
        if _clean_text(member.get("name"), 120).casefold() == needle:
            return member
    return None


def resolve_task_members(task: dict | None) -> list[dict]:
    if not task:
        return []
    by_sid, by_name = _member_index()
    decorated = _decorate_task(dict(task), by_sid, by_name)
    out: list[dict] = []
    seen: set[str] = set()
    for item in decorated.get("assignees") or []:
        sid = str(item.get("sync_id") or "").strip()
        member = by_sid.get(sid) if sid else None
        if not member:
            member = by_name.get(_clean_text(item.get("name"), 120).casefold())
        if not member:
            continue
        key = str(member.get("sync_id") or member.get("id") or item.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(member)
    return out


def resolve_task_member(task: dict | None) -> Optional[dict]:
    members = resolve_task_members(task)
    return members[0] if members else None


def create_member(*, name: str, discord_id: str = "", phone: str = "") -> dict:
    name_n = _clean_text(name, 120)
    if not name_n:
        raise ValueError("Вкажіть ім’я виконавця")
    discord_n = normalize_discord_id(discord_id)
    phone_n = normalize_member_phone(phone)
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        sid = new_sync_id()
        ts = utc_now()
        cur = conn.execute(
            """
            INSERT INTO team_members
            (name, phone, discord_id, created_at, sync_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name_n, phone_n, discord_n, ts, sid, ts),
        )
        mid = int(cur.lastrowid)
        enqueue_outbox(conn, "team_members", sid, "upsert")
        _commit(conn)
        _notify_sync()
        persist_team_roster()
        return _member_row(conn, mid) or {}
    finally:
        conn.close()


def update_member(member_id: int, **fields) -> Optional[dict]:
    existing = get_member(member_id)
    if not existing:
        return None
    patch: dict[str, Any] = {}
    if "name" in fields:
        name_n = _clean_text(fields.get("name"), 120)
        if not name_n:
            raise ValueError("Вкажіть ім’я виконавця")
        patch["name"] = name_n
    if "discord_id" in fields:
        patch["discord_id"] = normalize_discord_id(fields.get("discord_id") or "")
    if "phone" in fields:
        patch["phone"] = normalize_member_phone(fields.get("phone") or "")
    if not patch:
        return existing
    conn = _connect()
    try:
        sets = ", ".join(f"{k} = ?" for k in patch)
        conn.execute(
            f"UPDATE team_members SET {sets} WHERE id = ?",
            list(patch.values()) + [int(member_id)],
        )
        sid = existing.get("sync_id") or new_sync_id()
        if not existing.get("sync_id"):
            conn.execute(
                "UPDATE team_members SET sync_id = ? WHERE id = ?",
                (sid, int(member_id)),
            )
        touch_row(conn, "team_members", sid)
        enqueue_outbox(conn, "team_members", sid, "upsert")
        _commit(conn)
        _notify_sync()
        persist_team_roster()
        return _member_row(conn, int(member_id))
    finally:
        conn.close()


def delete_member(member_id: int) -> bool:
    existing = get_member(member_id)
    if not existing:
        return False
    conn = _connect()
    try:
        mid = int(member_id)
        if sync_enabled():
            sid = existing.get("sync_id") or new_sync_id()
            if not existing.get("sync_id"):
                conn.execute(
                    "UPDATE team_members SET sync_id = ? WHERE id = ?",
                    (sid, mid),
                )
            touch_row(conn, "team_members", sid, deleted=True)
            enqueue_outbox(conn, "team_members", sid, "delete")
        else:
            conn.execute("DELETE FROM team_members WHERE id = ?", (mid,))
        _commit(conn)
        _notify_sync()
        persist_team_roster()
        return True
    finally:
        conn.close()


def _resolve_assignees(
    *,
    assignee: str = "",
    member_sync_id: str = "",
    member_sync_ids=None,
) -> tuple[str, str]:
    ids_given = member_sync_ids is not None
    ids = _normalize_id_list(member_sync_ids) if ids_given else _normalize_id_list(member_sync_id)
    names = [part.strip() for part in str(assignee or "").split(",") if part.strip()]
    by_sid, by_name = _member_index()
    members: list[dict] = []
    seen: set[str] = set()
    for sid in ids:
        member = by_sid.get(sid)
        if not member:
            continue
        key = str(member.get("sync_id") or sid)
        if key in seen:
            continue
        seen.add(key)
        members.append(member)
    if not members and not ids_given:
        for name in names:
            member = by_name.get(name.casefold())
            if member:
                key = str(member.get("sync_id") or name.casefold())
                if key in seen:
                    continue
                seen.add(key)
                members.append(member)
            else:
                key = name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                members.append({"name": name, "sync_id": ""})
    names_str = ", ".join(_clean_text(m.get("name"), 120) for m in members if m.get("name"))
    ids_str = ",".join(str(m.get("sync_id") or "") for m in members if m.get("sync_id"))
    return names_str[:800], ids_str


def list_tasks() -> list[dict]:
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM team_tasks
            WHERE {not_deleted_sql()}
            ORDER BY status ASC, position ASC, id ASC
            """
        ).fetchall()
        by_sid, by_name = _member_index()
        return [_decorate_task(_row_to_dict(r), by_sid, by_name) for r in rows]
    finally:
        conn.close()


def list_tasks_grouped() -> dict[str, list]:
    grouped = {col["id"]: [] for col in COLUMNS}
    for task in list_tasks():
        grouped.setdefault(_clean_status(task.get("status")), []).append(task)
    return grouped


def get_task(task_id: int) -> Optional[dict]:
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT * FROM team_tasks WHERE id = ? AND {not_deleted_sql()}",
            (int(task_id),),
        ).fetchone()
        return _decorate_task(_row_to_dict(row)) if row else None
    finally:
        conn.close()


def _lookup_sync_id(conn, task_id: int) -> str:
    row = conn.execute(
        "SELECT sync_id FROM team_tasks WHERE id = ?", (int(task_id),)
    ).fetchone()
    if not row:
        return ""
    return str(row["sync_id"] if hasattr(row, "keys") else row[0] or "")


def _ensure_sid(conn, task_id: int) -> str:
    sid = _lookup_sync_id(conn, task_id)
    if sid:
        return sid
    sid = new_sync_id()
    conn.execute(
        "UPDATE team_tasks SET sync_id = ? WHERE id = ?",
        (sid, int(task_id)),
    )
    return sid


def _next_position(conn, status: str) -> int:
    row = conn.execute(
        f"""
        SELECT COALESCE(MAX(position), -1) AS m FROM team_tasks
        WHERE status = ? AND {not_deleted_sql()}
        """,
        (status,),
    ).fetchone()
    if not row:
        return 0
    val = row["m"] if hasattr(row, "keys") else row[0]
    return int(val or -1) + 1


def create_task(
    *,
    title: str,
    description: str = "",
    assignee: str = "",
    member_sync_id: str = "",
    member_sync_ids=None,
    deadline: str = "",
    status: str = "todo",
) -> dict:
    title_n = _clean_text(title, 200)
    if not title_n:
        raise ValueError("Назва задачі порожня")
    status_n = _clean_status(status)
    assignee_n, member_sid = _resolve_assignees(
        assignee=assignee,
        member_sync_id=member_sync_id,
        member_sync_ids=member_sync_ids,
    )
    deadline_n = normalize_deadline(deadline)
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        sid = new_sync_id()
        ts = utc_now()
        pos = _next_position(conn, status_n)
        cur = conn.execute(
            """
            INSERT INTO team_tasks
            (title, description, status, position, assignee, member_sync_id,
             deadline, created_at, sync_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title_n,
                _clean_text(description, 4000),
                status_n,
                pos,
                assignee_n,
                member_sid,
                deadline_n,
                ts,
                sid,
                ts,
            ),
        )
        tid = int(cur.lastrowid)
        enqueue_outbox(conn, "team_tasks", sid, "upsert")
        _commit(conn)
        _notify_sync()
        row = conn.execute("SELECT * FROM team_tasks WHERE id = ?", (tid,)).fetchone()
        return _decorate_task(_row_to_dict(row))
    finally:
        conn.close()


def update_task(task_id: int, **fields) -> Optional[dict]:
    existing = get_task(task_id)
    if not existing:
        return None
    patch: dict[str, Any] = {}
    if "title" in fields:
        title_n = _clean_text(fields.get("title"), 200)
        if not title_n:
            raise ValueError("Назва задачі порожня")
        patch["title"] = title_n
    if "description" in fields:
        patch["description"] = _clean_text(fields.get("description"), 4000)
    if "assignee" in fields or "member_sync_id" in fields or "member_sync_ids" in fields:
        assignee_n, member_sid = _resolve_assignees(
            assignee=fields.get("assignee", existing.get("assignee") or ""),
            member_sync_id=fields.get("member_sync_id", existing.get("member_sync_id") or ""),
            member_sync_ids=fields.get("member_sync_ids") if "member_sync_ids" in fields else None,
        )
        patch["assignee"] = assignee_n
        patch["member_sync_id"] = member_sid
    if "deadline" in fields:
        patch["deadline"] = normalize_deadline(fields.get("deadline") or "")
    if not patch:
        return existing
    conn = _connect()
    try:
        sets = ", ".join(f"{k} = ?" for k in patch)
        vals = list(patch.values()) + [int(task_id)]
        conn.execute(f"UPDATE team_tasks SET {sets} WHERE id = ?", vals)
        sid = _ensure_sid(conn, int(task_id))
        touch_row(conn, "team_tasks", sid)
        enqueue_outbox(conn, "team_tasks", sid, "upsert")
        _commit(conn)
        _notify_sync()
        row = conn.execute("SELECT * FROM team_tasks WHERE id = ?", (int(task_id),)).fetchone()
        return _decorate_task(_row_to_dict(row))
    finally:
        conn.close()


def _column_ids(conn, status: str, exclude_id: Optional[int] = None) -> list[int]:
    rows = conn.execute(
        f"""
        SELECT id FROM team_tasks
        WHERE status = ? AND {not_deleted_sql()}
        ORDER BY position ASC, id ASC
        """,
        (status,),
    ).fetchall()
    ids = []
    for row in rows:
        tid = int(row["id"] if hasattr(row, "keys") else row[0])
        if exclude_id is not None and tid == int(exclude_id):
            continue
        ids.append(tid)
    return ids


def _apply_order(conn, status: str, ordered_ids: list[int]) -> None:
    ts = utc_now()
    for index, tid in enumerate(ordered_ids):
        conn.execute(
            """
            UPDATE team_tasks
            SET status = ?, position = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, index, ts, int(tid)),
        )
        sid = _ensure_sid(conn, int(tid))
        enqueue_outbox(conn, "team_tasks", sid, "upsert")


def move_task(task_id: int, status: str, index: int) -> Optional[dict]:
    existing = get_task(task_id)
    if not existing:
        return None
    dest = _clean_status(status)
    conn = _connect()
    try:
        tid = int(task_id)
        source = _clean_status(existing.get("status"))
        dest_ids = _column_ids(conn, dest, exclude_id=tid)
        if index < 0:
            index = 0
        if index > len(dest_ids):
            index = len(dest_ids)
        dest_ids.insert(index, tid)
        _apply_order(conn, dest, dest_ids)
        if source != dest:
            source_ids = _column_ids(conn, source, exclude_id=tid)
            _apply_order(conn, source, source_ids)
        _commit(conn)
        _notify_sync()
        row = conn.execute("SELECT * FROM team_tasks WHERE id = ?", (tid,)).fetchone()
        task = _decorate_task(_row_to_dict(row))
        task["previous_status"] = source
        return task
    finally:
        conn.close()


def delete_task(task_id: int) -> bool:
    existing = get_task(task_id)
    if not existing:
        return False
    conn = _connect()
    try:
        tid = int(task_id)
        if sync_enabled():
            sid = _ensure_sid(conn, tid)
            touch_row(conn, "team_tasks", sid, deleted=True)
            enqueue_outbox(conn, "team_tasks", sid, "delete")
        else:
            conn.execute("DELETE FROM team_tasks WHERE id = ?", (tid,))
        _commit(conn)
        _notify_sync()
        return True
    finally:
        conn.close()


def _vlk_row(conn, item_id: int) -> Optional[dict]:
    row = conn.execute(
        f"SELECT * FROM vlk_queue WHERE id = ? AND {not_deleted_sql()}",
        (int(item_id),),
    ).fetchone()
    return _decorate_vlk(_row_to_dict(row)) if row else None


def list_vlk_queue() -> list[dict]:
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM vlk_queue
            WHERE {not_deleted_sql()}
            ORDER BY position ASC, id ASC
            """
        ).fetchall()
        return [_decorate_vlk(_row_to_dict(r)) for r in rows]
    finally:
        conn.close()


def _vlk_next_position(conn) -> int:
    row = conn.execute(
        f"SELECT COALESCE(MAX(position), -1) AS m FROM vlk_queue WHERE {not_deleted_sql()}"
    ).fetchone()
    if not row:
        return 0
    val = row["m"] if hasattr(row, "keys") else row[0]
    return int(val or -1) + 1


def add_vlk_queue_item(*, pib: str, note: str = "") -> dict:
    pib_n = _clean_text(pib, 200)
    if not pib_n:
        raise ValueError("Вкажіть ПІБ")
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        for row in conn.execute(
            f"SELECT id, pib FROM vlk_queue WHERE {not_deleted_sql()}"
        ).fetchall():
            existing = _clean_text(row["pib"] if hasattr(row, "keys") else row[1], 200)
            if existing.casefold() == pib_n.casefold():
                raise ValueError("Ця людина вже в переліку ВЛК")
        sid = new_sync_id()
        ts = utc_now()
        pos = _vlk_next_position(conn)
        cur = conn.execute(
            """
            INSERT INTO vlk_queue (pib, note, position, created_at, sync_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pib_n, _clean_text(note, 400), pos, ts, sid, ts),
        )
        iid = int(cur.lastrowid)
        enqueue_outbox(conn, "vlk_queue", sid, "upsert")
        _commit(conn)
        _notify_sync()
        return _vlk_row(conn, iid) or {}
    finally:
        conn.close()


def update_vlk_queue_item(item_id: int, **fields) -> Optional[dict]:
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        existing = _vlk_row(conn, item_id)
        if not existing:
            return None
        patch: dict[str, Any] = {}
        if "pib" in fields:
            pib_n = _clean_text(fields.get("pib"), 200)
            if not pib_n:
                raise ValueError("Вкажіть ПІБ")
            patch["pib"] = pib_n
        if "note" in fields:
            patch["note"] = _clean_text(fields.get("note"), 400)
        if "passed" in fields:
            patch["passed"] = serialize_vlk_passed(fields.get("passed"))
        if not patch:
            return existing
        sets = ", ".join(f"{k} = ?" for k in patch)
        conn.execute(
            f"UPDATE vlk_queue SET {sets} WHERE id = ?",
            list(patch.values()) + [int(item_id)],
        )
        sid = existing.get("sync_id") or new_sync_id()
        if not existing.get("sync_id"):
            conn.execute(
                "UPDATE vlk_queue SET sync_id = ? WHERE id = ?",
                (sid, int(item_id)),
            )
        touch_row(conn, "vlk_queue", sid)
        enqueue_outbox(conn, "vlk_queue", sid, "upsert")
        _commit(conn)
        _notify_sync()
        return _vlk_row(conn, int(item_id))
    finally:
        conn.close()


def delete_vlk_queue_item(item_id: int) -> bool:
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        existing = _vlk_row(conn, item_id)
        if not existing:
            return False
        if sync_enabled():
            sid = existing.get("sync_id") or new_sync_id()
            if not existing.get("sync_id"):
                conn.execute(
                    "UPDATE vlk_queue SET sync_id = ? WHERE id = ?",
                    (sid, int(item_id)),
                )
            touch_row(conn, "vlk_queue", sid, deleted=True)
            enqueue_outbox(conn, "vlk_queue", sid, "delete")
        else:
            conn.execute("DELETE FROM vlk_queue WHERE id = ?", (int(item_id),))
        _commit(conn)
        _notify_sync()
        return True
    finally:
        conn.close()


def reorder_vlk_queue(ordered_ids: list[int]) -> list[dict]:
    ensure_team_tasks_schema()
    conn = _connect()
    try:
        ts = utc_now()
        seen = set()
        index = 0
        for raw_id in ordered_ids:
            try:
                iid = int(raw_id)
            except (TypeError, ValueError):
                continue
            if iid in seen:
                continue
            seen.add(iid)
            row = _vlk_row(conn, iid)
            if not row:
                continue
            conn.execute(
                "UPDATE vlk_queue SET position = ?, updated_at = ? WHERE id = ?",
                (index, ts, iid),
            )
            sid = row.get("sync_id") or new_sync_id()
            if not row.get("sync_id"):
                conn.execute(
                    "UPDATE vlk_queue SET sync_id = ? WHERE id = ?",
                    (sid, iid),
                )
            enqueue_outbox(conn, "vlk_queue", sid, "upsert")
            index += 1
        _commit(conn)
        _notify_sync()
    finally:
        conn.close()
    return list_vlk_queue()


def import_vlk_from_journal() -> dict:
    """Додає в чергу людей з відкритим ВЛК у журналі, яких ще немає в списку."""
    from utils import patient_cards_db as cards_db

    people = cards_db.list_active_vlk_people()
    added = 0
    skipped = 0
    for person in people:
        try:
            add_vlk_queue_item(
                pib=person.get("pib") or "",
                note=_clean_text(person.get("lpz") or person.get("diagnosis") or "", 400),
            )
            added += 1
        except ValueError:
            skipped += 1
    return {"added": added, "skipped": skipped, "items": list_vlk_queue()}
