#!/usr/bin/env python3
"""Build data/lpz_list.json from the canonical LPZ facility name list."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).resolve().parent / "lpz_names.txt"
OUTPUT = ROOT / "data" / "lpz_list.json"


def normalize(name: str) -> str:
    name = name.replace("\u00a0", " ")
    name = re.sub(r" +", " ", name.strip())
    return name


def build_unique_list(raw_lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in raw_lines:
        name = normalize(raw)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def main() -> None:
    raw_lines = [
        line
        for line in SOURCE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    unique = build_unique_list(raw_lines)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(unique, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT} ({len(unique)} entries from {len(raw_lines)} raw lines)")


if __name__ == "__main__":
    main()
