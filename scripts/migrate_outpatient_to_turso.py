"""Одноразове перенесення локального outpatient_journal.db у порожню Turso DB."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import OUTPATIENT_JOURNAL_DB, USE_TURSO  # noqa: E402
from utils.db_backend import connect_turso as destination_connect  # noqa: E402


TABLES = (
    "patients",
    "injury_cases",
    "treatments",
    "outpatient_entries",
    "treatment_restrictions",
    "treatment_media",
)


def _columns(conn, table: str) -> list[str]:
    return [
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    ]


def migrate(source_path: str) -> dict[str, int]:
    if not USE_TURSO:
        raise RuntimeError(
            "У .env мають бути TURSO_DATABASE_URL і TURSO_AUTH_TOKEN"
        )
    source_path = os.path.abspath(source_path)
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"Локальну БД не знайдено: {source_path}")

    # Створити актуальну схему у Turso без демо-даних.
    from app import _outpatient_init_db

    _outpatient_init_db()

    src = sqlite3.connect(source_path)
    src.row_factory = sqlite3.Row
    dst = destination_connect()
    copied: dict[str, int] = {}
    try:
        occupied = {}
        for table in TABLES:
            if not _columns(src, table):
                continue
            row = dst.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            occupied[table] = int(row["n"] if row else 0)
        nonempty = {name: count for name, count in occupied.items() if count}
        if nonempty:
            details = ", ".join(f"{name}={count}" for name, count in nonempty.items())
            raise RuntimeError(
                "Turso DB вже містить дані; міграцію зупинено, щоб не створити "
                f"дублікати ({details})"
            )

        for table in TABLES:
            src_cols = _columns(src, table)
            dst_cols = set(_columns(dst, table))
            cols = [name for name in src_cols if name in dst_cols]
            if not cols:
                continue
            rows = src.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            quoted = ", ".join(f'"{name}"' for name in cols)
            placeholders = ", ".join("?" for _ in cols)
            sql = f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})'
            for row in rows:
                dst.execute(sql, tuple(row[name] for name in cols))
            dst.commit()
            copied[table] = len(rows)
    finally:
        src.close()
        dst.close()
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Перенести локальну БД амбулаторного журналу в порожню Turso DB"
    )
    parser.add_argument(
        "--source",
        default=OUTPATIENT_JOURNAL_DB,
        help=f"Шлях до локального outpatient_journal.db (default: {OUTPATIENT_JOURNAL_DB})",
    )
    args = parser.parse_args()
    copied = migrate(args.source)
    print("Міграція завершена:")
    for table, count in copied.items():
        print(f"  {table}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

