# -*- coding: utf-8 -*-
"""
Створює data/team.vault з поточного .env (шифрування спільним PIN).

  .\\venv\\Scripts\\python.exe .\\scripts\\build_team_vault.py --env .env --pin "ВАШ_PIN"

PIN не зберігається у файлі — лише у вашій голові / у команди.
Пакет team.vault можна сміливо класти в Install.exe: без PIN він марний.
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.team_vault import (  # noqa: E402
    encrypt_secrets,
    save_vault_file,
    secrets_from_env_map,
    vault_path,
)


def _parse_env_file(path: str) -> dict:
    out = {}
    with open(path, "r", encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip().lstrip("\ufeff")] = value.strip().strip('"').strip("'")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build encrypted Medhar team vault")
    parser.add_argument(
        "--env",
        default=os.path.join(ROOT, ".env"),
        help="Шлях до .env з секретами команди",
    )
    parser.add_argument("--pin", required=True, help="Спільний PIN команди")
    parser.add_argument(
        "--out",
        default=vault_path(ROOT),
        help="Куди записати team.vault",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.env):
        print(f"Немає файлу: {args.env}", file=sys.stderr)
        return 1
    pin = str(args.pin or "").strip()
    if len(pin) < 4:
        print("PIN має бути щонайменше 4 символи.", file=sys.stderr)
        return 1

    secrets = secrets_from_env_map(_parse_env_file(args.env))
    if not secrets.get("TURSO_DATABASE_URL") or not secrets.get("TURSO_AUTH_TOKEN"):
        print("У .env потрібні TURSO_DATABASE_URL і TURSO_AUTH_TOKEN.", file=sys.stderr)
        return 1
    if not secrets.get("GEMINI_API_KEY"):
        print("Попередження: GEMINI_API_KEY порожній.", file=sys.stderr)
    if not (
        secrets.get("DROPBOX_REFRESH_TOKEN")
        or secrets.get("DROPBOX_ACCESS_TOKEN")
    ):
        print("Попередження: немає Dropbox токенів.", file=sys.stderr)
    if not (
        secrets.get("WHATSAPP_ACCESS_TOKEN")
        and secrets.get("WHATSAPP_PHONE_NUMBER_ID")
        and secrets.get("WHATSAPP_RECIPIENT")
    ):
        print("Попередження: WhatsApp credentials не заповнені.", file=sys.stderr)

    vault = encrypt_secrets(secrets, pin)
    save_vault_file(args.out, vault)
    print(f"OK: {args.out}")
    print("Передайте колегам Install.exe і PIN окремо (не в тому ж чаті/файлі).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
