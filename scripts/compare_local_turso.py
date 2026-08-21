# -*- coding: utf-8 -*-
"""
Звірка локальної SQLite з Turso (журнал).

Запуск з кореня проєкту:
  .\\venv\\Scripts\\python.exe scripts\\compare_local_turso.py
  .\\venv\\Scripts\\python.exe scripts\\compare_local_turso.py --pib \"Терентюк\"
  .\\venv\\Scripts\\python.exe scripts\\compare_local_turso.py --fix-patients
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("MEDHAR_DESKTOP", "1")


def _short(sid: str, n: int = 8) -> str:
    sid = (sid or "").strip()
    return sid[:n] if sid else "—"


def _count(conn, table: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM {table} WHERE COALESCE(deleted_at,'') = ''"
    ).fetchone()
    return int(row["c"] if hasattr(row, "keys") else row[0])


def compare_counts(local, remote) -> None:
    from utils.sync_schema import SYNC_TABLES

    print("=== Counts (not deleted) ===")
    print(f"{'table':<24} {'local':>8} {'turso':>8} {'delta':>8}")
    for table in SYNC_TABLES:
        try:
            lc = _count(local, table)
            rc = _count(remote, table)
        except Exception as exc:
            print(f"{table:<24} error: {exc}")
            continue
        mark = "" if lc == rc else " <<<"
        print(f"{table:<24} {lc:8d} {rc:8d} {rc - lc:8d}{mark}")


def compare_patient_sync_ids(local, remote) -> list[tuple[str, str, str]]:
    local_map = {
        (row["pib"] or "").strip(): (row["sync_id"] or "").strip()
        for row in local.execute(
            "SELECT pib, sync_id FROM patients WHERE COALESCE(deleted_at,'') = ''"
        ).fetchall()
        if (row["pib"] or "").strip()
    }
    remote_map = {
        (row["pib"] or "").strip(): (row["sync_id"] or "").strip()
        for row in remote.execute(
            "SELECT pib, sync_id FROM patients WHERE COALESCE(deleted_at,'') = ''"
        ).fetchall()
        if (row["pib"] or "").strip()
    }
    mism: list[tuple[str, str, str]] = []
    for pib, ls in sorted(local_map.items()):
        rs = remote_map.get(pib)
        if rs and rs != ls:
            mism.append((pib, ls, rs))
    only_local = sorted(set(local_map) - set(remote_map))
    only_remote = sorted(set(remote_map) - set(local_map))
    print(f"=== Patient sync_id mismatches: {len(mism)} ===")
    for pib, ls, rs in mism[:40]:
        print(f"  {pib}: local={_short(ls)} turso={_short(rs)}")
    if len(mism) > 40:
        print(f"  … +{len(mism) - 40} more")
    print(f"=== PIB only local: {len(only_local)} | only turso: {len(only_remote)} ===")
    for pib in only_remote[:20]:
        print(f"  turso-only: {pib}")
    return mism


def show_pib(local, remote, needle: str) -> None:
    needle = (needle or "").strip()
    if not needle:
        return
    print(f"=== Detail PIB ~ {needle!r} ===")
    for label, conn in (("LOCAL", local), ("TURSO", remote)):
        rows = conn.execute(
            """
            SELECT id, sync_id, pib, unit_short, phone, birth_date, updated_at
            FROM patients
            WHERE pib LIKE ?
            """,
            (f"%{needle}%",),
        ).fetchall()
        print(f"-- {label} patients --")
        if not rows:
            print("  (none)")
            continue
        for p in rows:
            p = dict(p)
            print(
                f"  id={p['id']} sync={p['sync_id']} unit={p.get('unit_short')} "
                f"phone={p.get('phone')} birth={p.get('birth_date')}"
            )
            pid = int(p["id"])
            treats = conn.execute(
                """
                SELECT id, sync_id, title, started_on, cause, injury_case_id, updated_at
                FROM treatments WHERE patient_id = ? AND COALESCE(deleted_at,'') = ''
                """,
                (pid,),
            ).fetchall()
            print(f"  treatments: {len(treats)}")
            for t in treats:
                t = dict(t)
                print(
                    f"    #{t['id']} {t['title']} sync={_short(t['sync_id'])} "
                    f"started={t['started_on']} cause={t['cause']}"
                )
            cases = conn.execute(
                """
                SELECT id, sync_id, title, injury_date
                FROM injury_cases WHERE patient_id = ? AND COALESCE(deleted_at,'') = ''
                """,
                (pid,),
            ).fetchall()
            print(f"  injury_cases: {len(cases)}")
            entries = conn.execute(
                """
                SELECT e.id, e.sync_id, e.date, e.care_type, e.lpz, e.leave_start, e.leave_end,
                       substr(COALESCE(e.diagnosis,''),1,80) AS dx
                FROM outpatient_entries e
                LEFT JOIN treatments t ON t.id = e.treatment_id
                WHERE (t.patient_id = ? OR e.pib LIKE ?)
                  AND COALESCE(e.deleted_at,'') = ''
                ORDER BY e.id
                """,
                (pid, f"%{needle}%"),
            ).fetchall()
            print(f"  outpatient: {len(entries)}")
            for e in entries:
                e = dict(e)
                print(
                    f"    #{e['id']} {e['date']} {e['care_type']} "
                    f"{e.get('lpz') or ''} | {e.get('dx') or ''}"
                )


def fix_patients(local, remote) -> None:
    from utils.journal_sync import align_patient_sync_ids, run_sync

    n = align_patient_sync_ids(local, remote)
    print(f"Aligned patient sync_ids: {n}")
    if n:
        from utils.sync_schema import set_meta

        set_meta(local, "last_pull_at", "1970-01-01T00:00:00+00:00")
        local.commit()
    print("Running sync…")
    print(run_sync(force=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare local journal DB with Turso")
    parser.add_argument("--pib", default="", help="Show detail for PIB substring")
    parser.add_argument(
        "--fix-patients",
        action="store_true",
        help="Adopt Turso patient sync_id by PIB and re-pull",
    )
    args = parser.parse_args()

    from utils.db_backend import connect_local, connect_turso
    from utils.sync_schema import get_meta

    local = connect_local()
    remote = connect_turso()
    try:
        print("last_pull_at:", get_meta(local, "last_pull_at", ""))
        print("last_push_at:", get_meta(local, "last_push_at", ""))
        compare_counts(local, remote)
        compare_patient_sync_ids(local, remote)
        if args.pib:
            show_pib(local, remote, args.pib)
        if args.fix_patients:
            fix_patients(local, remote)
            if args.pib:
                print("\nAfter fix:")
                show_pib(local, remote, args.pib)
    finally:
        remote.close()
        local.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
