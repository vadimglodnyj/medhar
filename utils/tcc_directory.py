# -*- coding: utf-8 -*-
"""
Довідник ТЦК та СП: варіанти для автокомпліту «Військовий комісаріат».

Джерело — data/all_tcc_ukraine.json. Для кожного ТЦК будується готовий рядок
в орудному відмінку разом з регіоном у родовому:
    «Голосіївський РТЦК та СП» + «м. Київ» → «Голосіївським РТЦК та СП міста Києва»
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

DATA_FILENAME = "all_tcc_ukraine.json"

# Абревіатури не відмінюються.
_ABBREVIATIONS = {"ТЦК", "РТЦК", "СП", "ОТЦК", "МТЦК", "ОМТЦК", "ОТКЦ", "ЦК"}
_STOP_WORDS = {"та", "і", "й", "у", "в", "на", "the"}

# Ознаки того, що ТЦК уже обласного/міського рівня — регіон дублювати не треба.
_REGIONAL_MARKERS = (
    "обласн",
    "міськ",
    "республіканськ",
    "отцк",
    "омтцк",
    "мтцк",
    "откц",
)

# Уже відмінені форми (родовий/давальний у назвах відділів) не чіпаємо.
_INFLECTED_ENDINGS = (
    "ого",
    "ому",
    "ої",
    "ою",
    "им",
    "іми",
    "ами",
    "ями",
    "их",
    "іх",
)

# Міста: називний → родовий (нерегулярні форми).
_CITY_GENITIVE = {
    "Київ": "Києва",
    "Севастополь": "Севастополя",
}

_cache: Optional[dict[str, Any]] = None


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip()


def _is_abbreviation(word: str) -> bool:
    core = word.strip(".,;:()")
    if core.upper() in _ABBREVIATIONS:
        return True
    letters = [ch for ch in core if ch.isalpha()]
    return bool(letters) and all(ch.isupper() for ch in letters)


def adjective_to_instrumental(word: str) -> str:
    """Прикметник чоловічого роду в орудний: Голосіївський → Голосіївським."""
    if not word:
        return word
    # Складені назви відмінюються за останньою частиною: Могилів-Подільський.
    if "-" in word:
        head, _, tail = word.rpartition("-")
        # Порядкові «1-й» / «2-й».
        if head.isdigit() and tail.casefold() in {"й", "ий"}:
            return f"{head}-м"
        return f"{head}-{adjective_to_instrumental(tail)}"
    low = word.casefold()
    if low == "відділ":
        return "відділом"
    if any(low.endswith(end) for end in _INFLECTED_ENDINGS):
        return word
    if word.endswith("ий"):
        return word[:-2] + "им"
    if word.endswith("ій"):
        return word[:-2] + "ім"
    return word


def tcc_to_instrumental(name: str) -> str:
    """«Голосіївський РТЦК та СП» → «Голосіївським РТЦК та СП»."""
    words = _normalize_spaces(name).split(" ")
    out = []
    for word in words:
        if word.isdigit() or _is_abbreviation(word) or word.casefold() in _STOP_WORDS:
            out.append(word)
            continue
        out.append(adjective_to_instrumental(word))
    return " ".join(out)


def _city_to_genitive(city: str) -> str:
    if city in _CITY_GENITIVE:
        return _CITY_GENITIVE[city]
    if city.endswith("ь"):
        return city[:-1] + "я"
    if city.endswith(("а", "я")):
        return city[:-1] + "и"
    return city + "а"


def _feminine_adjective_to_genitive(word: str) -> str:
    if word.endswith("а"):
        return word[:-1] + "ої"
    if word.endswith("я"):
        return word[:-1] + "ої"
    return word


def region_to_genitive(region: str) -> str:
    """«м. Київ» → «міста Києва»; «Київська область» → «Київської області»."""
    text = _normalize_spaces(region)
    if not text:
        return ""

    city = re.match(r"^м\.?\s*(.+)$", text)
    if city:
        return f"міста {_city_to_genitive(city.group(1).strip())}"

    if text.endswith("область"):
        adj = _normalize_spaces(text[: -len("область")])
        return f"{_feminine_adjective_to_genitive(adj)} області"

    if "Республіка" in text:
        # Автономна Республіка Крим → Автономної Республіки Крим
        parts = text.split(" ")
        out = []
        for part in parts:
            if part == "Республіка":
                out.append("Республіки")
            elif part.endswith(("а", "я")) and part != "Крим":
                out.append(_feminine_adjective_to_genitive(part))
            else:
                out.append(part)
        return " ".join(out)

    return text


def _stem(word: str) -> str:
    """Груба основа для порівняння: Київський/Київська → Київ."""
    text = (word or "").casefold()
    for suffix in ("ського", "ська", "ський", "цький", "цька", "ний", "на", "ий", "а"):
        if text.endswith(suffix) and len(text) > len(suffix) + 2:
            return text[: -len(suffix)]
    return text


def _region_is_redundant(tcc_name: str, region: str) -> bool:
    """Для обласних/міських ТЦК регіон уже є в самій назві."""
    low = tcc_name.casefold()
    if not any(marker in low for marker in _REGIONAL_MARKERS):
        return False
    tcc_stem = _stem(_normalize_spaces(tcc_name).split(" ")[0])
    region_core = _region_core(region)
    return bool(tcc_stem) and _stem(region_core).startswith(tcc_stem[:4])


def _region_core(region: str) -> str:
    """Ключове слово регіону: «Автономна Республіка Крим» → «Крим»."""
    text = re.sub(r"^м\.?\s*", "", _normalize_spaces(region))
    if "Республіка" in text:
        return text.split(" ")[-1]
    return text.split(" ")[0] if text else ""


def build_komisariat_value(tcc_name: str, region: str) -> str:
    """Готовий рядок для документа: ТЦК в орудному + регіон у родовому."""
    name = _normalize_spaces(tcc_name)
    if not name:
        return ""
    instrumental = tcc_to_instrumental(name)
    if _region_is_redundant(name, region):
        return instrumental
    genitive = region_to_genitive(region)
    return f"{instrumental} {genitive}".strip()


def _directory_path(*roots: str) -> str:
    for root in roots:
        if not root:
            continue
        path = os.path.join(root, "data", DATA_FILENAME)
        if os.path.isfile(path):
            return path
    return ""


def load_options(*, refresh: bool = False) -> list[dict[str, str]]:
    """Список варіантів автокомпліту (кешується в пам'яті)."""
    global _cache
    if _cache is not None and not refresh:
        return _cache["options"]

    import config as _cfg

    path = _directory_path(
        getattr(_cfg, "RESOURCE_ROOT", ""),
        getattr(_cfg, "USER_DATA_ROOT", ""),
    )
    options: list[dict[str, str]] = []
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        for block in (raw or {}).values():
            if not isinstance(block, dict):
                continue
            region = _normalize_spaces(block.get("region") or "")
            names = _block_names(block)
            for name in names:
                options.append(_make_option(name, region, classify_kind(name)))

    _kind_order = {"regional": 0, "city": 1, "district": 2, "department": 3}
    options.sort(
        key=lambda o: (
            o["region"],
            _kind_order.get(o["kind"], 9),
            o["name"],
        )
    )
    _cache = {"options": options, "path": path}
    return options


def classify_kind(name: str) -> str:
    """regional / city / department / district — за назвою з довідника МОУ."""
    low = (name or "").casefold()
    if re.search(r"\bвідділ\b", low):
        return "department"
    if "отцк" in low or "откц" in low or "обласн" in low or "республіканськ" in low:
        return "regional"
    if "омтцк" in low or "мтцк" in low or "міськ" in low:
        return "city"
    return "district"


def _block_names(block: dict[str, Any]) -> list[str]:
    """Нова схема `entries`; сумісність зі старими regional_tcc / district_tcc."""
    names: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        name = _normalize_spaces(str(raw or ""))
        if not name:
            return
        key = name.casefold()
        if key in seen:
            return
        seen.add(key)
        names.append(name)

    for item in block.get("entries") or []:
        add(item)
    if names:
        return names
    add(block.get("regional_tcc"))
    for item in block.get("district_tcc") or []:
        add(item)
    return names


def _make_option(name: str, region: str, kind: str) -> dict[str, str]:
    return {
        "value": build_komisariat_value(name, region),
        "name": name,
        "region": region,
        "kind": kind,
    }
