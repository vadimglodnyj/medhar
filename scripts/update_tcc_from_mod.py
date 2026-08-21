#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Оновити data/all_tcc_ukraine.json з офіційного довідника МОУ.

Джерела (у такому порядку):
1. HTML: https://mod.gov.ua/vijskovij-oblik/teritorialni-czentri-komplektuvannya-ta-soczialnoyi-pidtrimki
2. PDF-сторінка / файл довідника від 04.11.2025
3. Дзеркало того ж довідника, якщо HTML/PDF недоступні (JS/таймаут)

Крим і Севастополь на сайті МОУ зазвичай відсутні — зберігаються з попереднього JSON.
"""

from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "all_tcc_ukraine.json"

MOD_HTML_URL = (
    "https://mod.gov.ua/vijskovij-oblik/"
    "teritorialni-czentri-komplektuvannya-ta-soczialnoyi-pidtrimki"
)
MOD_PDF_PAGE_URL = (
    "https://mod.gov.ua/diyalnist/normativno-pravova-baza/"
    "dovidnik-nomeriv-telefoniv-ta-imejliv-t-cz-k-ta-sp"
)
# Дзеркало офіційного довідника МОУ від 04.11.2025 (~461 запис).
MIRROR_URL = (
    "https://www.online.ua/ru/aktualnye-kontakty-i-adresa-tck-i-sp-ukrainy-878906/"
)

MIN_ENTRIES = 300
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REGION_KEYS: dict[str, str] = {
    "Житомирська область": "Zhytomyr_Oblast",
    "м. Київ": "Kyiv_City",
    "Київ": "Kyiv_City",
    "Київська область": "Kyiv_Oblast",
    "Полтавська область": "Poltava_Oblast",
    "Сумська область": "Sumy_Oblast",
    "Черкаська область": "Cherkasy_Oblast",
    "Чернігівська область": "Chernihiv_Oblast",
    "Одеська область": "Odesa_Oblast",
    "Херсонська область": "Kherson_Oblast",
    "Миколаївська область": "Mykolaiv_Oblast",
    "Вінницька область": "Vinnytsia_Oblast",
    "Кіровоградська область": "Kirovohrad_Oblast",
    "Волинська область": "Volyn_Oblast",
    "Закарпатська область": "Zakarpattia_Oblast",
    "Івано-Франківська область": "Ivano_Frankivsk_Oblast",
    "Львівська область": "Lviv_Oblast",
    "Рівненська область": "Rivne_Oblast",
    "Тернопільська область": "Ternopil_Oblast",
    "Хмельницька область": "Khmelnytskyi_Oblast",
    "Чернівецька область": "Chernivtsi_Oblast",
    "Дніпропетровська область": "Dnipropetrovsk_Oblast",
    "Донецька область": "Donetsk_Oblast",
    "Луганська область": "Luhansk_Oblast",
    "Харківська область": "Kharkiv_Oblast",
    "Запорізька область": "Zaporizhzhia_Oblast",
    "Автономна Республіка Крим": "Crimea_AR",
    "м. Севастополь": "Sevastopol_City",
    "Севастополь": "Sevastopol_City",
}

OCCUPIED_KEYS = ("Crimea_AR", "Sevastopol_City")

_REGION_RE = re.compile(
    r"^(м\.\s*.+|.+?\s+область|Автономна Республіка Крим)$",
    re.IGNORECASE,
)
_ENTRY_HINT = re.compile(
    r"(ТЦК|РТЦК|ОТЦК|МТЦК|ОМТЦК|ОТКЦ|відділ)",
    re.IGNORECASE,
)
_SKIP_LINE = re.compile(
    r"^(оперативний|гаряча лінія|e-mail|email|тел\.|завантаж|"
    r"для швидкого|на комп|на телефон|перейти до|завантаження)",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    text = (text or "").replace("\u00a0", " ").replace("\xa0", " ")
    text = text.replace("’", "'").replace("ʼ", "'").replace("`", "'")
    return re.sub(r"\s+", " ", text).strip(" \t\r\n-|")


def _fetch(url: str, timeout: int = 45) -> bytes:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp1251", "windows-1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _is_region_heading(text: str) -> Optional[str]:
    t = _normalize(text)
    t = re.sub(r"\s+[—–-]\s+\d+\s+контакт.*$", "", t, flags=re.IGNORECASE)
    t = t.strip(" :")
    t = re.sub(r"^місто\s+", "м. ", t, flags=re.IGNORECASE)
    if t in REGION_KEYS:
        return t if t != "Київ" else "м. Київ"
    m = re.match(r"^(.+?\s+область)\b", t, re.IGNORECASE)
    if m and m.group(1) in REGION_KEYS:
        return m.group(1)
    if _REGION_RE.match(t) and t in REGION_KEYS:
        return t
    return None


_KYIV_CITY_MARKERS = (
    "київський мтцк",
    "голосіївський ртцк",
    "дарницький ртцк",
    "деснянський ртцк",
    "дніпровський ртцк",
    "оболонський ртцк",
    "печерський ртцк",
    "подільський ртцк",
    "святошинський ртцк",
    "солом'янський ртцк",
    "шевченківський ртцк",
)


def _split_kyiv_city(by_region: dict[str, list[str]]) -> dict[str, list[str]]:
    """На сторінці МОУ місто Київ часто йде одним блоком із Київською областю."""
    oblast = list(by_region.get("Київська область") or [])
    if not oblast:
        return by_region
    if by_region.get("м. Київ"):
        return by_region

    city: list[str] = []
    rest: list[str] = []
    seen_otck = False
    for name in oblast:
        low = name.casefold()
        if "київський отцк" in low:
            seen_otck = True
        if not seen_otck and any(m in low for m in _KYIV_CITY_MARKERS) and "відділ" not in low:
            city.append(name)
        else:
            rest.append(name)
    if len(city) >= 8:
        by_region["м. Київ"] = city
        by_region["Київська область"] = rest
    return by_region


def _is_entry(text: str) -> bool:
    t = _normalize(text)
    if not t or len(t) > 180:
        return False
    if _SKIP_LINE.search(t):
        return False
    if _is_region_heading(t):
        return False
    if not _ENTRY_HINT.search(t):
        return False
    # Відсікти речення з контактами.
    if t.lower().startswith(("оперативний", "гаряча")):
        return False
    return True


class _ModHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._buf: list[str] = []
        self.headings: list[str] = []
        self.table_cells: list[str] = []
        self._in_td = False
        self._td_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"}:
            self._capture = True
            self._buf = []
        if tag in {"td", "th"}:
            self._in_td = True
            self._td_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"} and self._capture:
            text = _normalize("".join(self._buf))
            if text:
                self.headings.append(text)
            self._capture = False
            self._buf = []
        if tag in {"td", "th"} and self._in_td:
            text = _normalize("".join(self._td_buf))
            if text:
                self.table_cells.append(text)
            self._in_td = False
            self._td_buf = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buf.append(data)
        if self._in_td:
            self._td_buf.append(data)


def _collect_from_sequence(lines: list[str]) -> dict[str, list[str]]:
    by_region: dict[str, list[str]] = {}
    current: Optional[str] = None
    seen: dict[str, set[str]] = {}

    def add(region: str, name: str) -> None:
        key = name.casefold()
        bucket = seen.setdefault(region, set())
        if key in bucket:
            return
        bucket.add(key)
        by_region.setdefault(region, []).append(name)

    for raw in lines:
        text = _normalize(raw)
        if not text:
            continue
        region = _is_region_heading(text)
        if region:
            current = region
            continue
        if current and _is_entry(text):
            add(current, text)
    return by_region


def parse_html(html: str) -> dict[str, list[str]]:
    parser = _ModHtmlParser()
    parser.feed(html)
    from_headings = _collect_from_sequence(parser.headings)

    # Таблиці: колонка з назвою ТЦК (часто друга після №).
    table_lines: list[str] = []
    current_region: Optional[str] = None
    for cell in parser.table_cells:
        region = _is_region_heading(cell)
        if region:
            current_region = region
            table_lines.append(cell)
            continue
        if _is_entry(cell):
            if current_region:
                table_lines.append(cell)
            else:
                table_lines.append(cell)
    from_tables = _collect_from_sequence(table_lines)

    # Markdown-таблиці, якщо контент уже сконвертований.
    md_lines: list[str] = []
    for line in html.splitlines():
        text = _normalize(re.sub(r"<[^>]+>", " ", line))
        if text:
            md_lines.append(text)
        # Рядок таблиці: | 24 | Київський МТЦК та СП | ...
        m = re.search(
            r"\|\s*\d+\s*\|\s*([^|]+?)\s*\|",
            line,
        )
        if m:
            md_lines.append(_normalize(m.group(1)))
    from_md = _collect_from_sequence(md_lines)

    return _split_kyiv_city(_merge_region_maps(from_headings, from_tables, from_md))


def _merge_region_maps(*maps: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for mapping in maps:
        for region, names in mapping.items():
            bucket = seen.setdefault(region, set())
            dest = out.setdefault(region, [])
            for name in names:
                key = name.casefold()
                if key in bucket:
                    continue
                bucket.add(key)
                dest.append(name)
    return out


def _count(mapping: dict[str, list[str]]) -> int:
    return sum(len(v) for v in mapping.values())


def parse_pdf_bytes(data: bytes) -> dict[str, list[str]]:
    try:
        import io

        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return {}
    reader = PdfReader(io.BytesIO(data))
    lines: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            line = _normalize(raw)
            if line:
                lines.append(line)
    return _collect_from_sequence(lines)


def _find_pdf_url(html: str) -> Optional[str]:
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    for href in hrefs:
        if ".pdf" in href.lower():
            if href.startswith("http"):
                return href
            if href.startswith("//"):
                return "https:" + href
            if href.startswith("/"):
                return "https://mod.gov.ua" + href
    return None


def load_previous() -> dict:
    if not OUTPUT.is_file():
        return {}
    with OUTPUT.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def occupied_from_previous(prev: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key in OCCUPIED_KEYS:
        block = prev.get(key) or {}
        if not isinstance(block, dict):
            continue
        region = _normalize(block.get("region") or "")
        entries = list(block.get("entries") or [])
        if not entries:
            regional = _normalize(block.get("regional_tcc") or "")
            if regional:
                entries.append(regional)
            for name in block.get("district_tcc") or []:
                n = _normalize(str(name))
                if n:
                    entries.append(n)
        if region and entries:
            out[key] = {"region": region, "entries": entries}
    return out


def build_output(by_region: dict[str, list[str]], occupied: dict[str, dict]) -> dict:
    result: dict[str, dict] = {}
    # Стабільний порядок: як у REGION_KEYS.
    used: set[str] = set()
    for region_ua, key in REGION_KEYS.items():
        if key in occupied:
            result[key] = occupied[key]
            used.add(key)
            continue
        names = by_region.get(region_ua) or []
        if not names:
            continue
        result[key] = {"region": region_ua if region_ua != "Київ" else "м. Київ", "entries": names}
        used.add(key)
    for region_ua, names in by_region.items():
        key = REGION_KEYS.get(region_ua)
        if not key or key in used:
            continue
        result[key] = {"region": region_ua, "entries": names}
    for key, block in occupied.items():
        result.setdefault(key, block)
    return result


def _kind_stats(entries: list[str]) -> dict[str, int]:
    counts = {"regional": 0, "city": 0, "district": 0, "department": 0}
    for name in entries:
        low = name.casefold()
        if re.search(r"\bвідділ\b", low):
            counts["department"] += 1
        elif "отцк" in low or "откц" in low or "обласн" in low or "республіканськ" in low:
            counts["regional"] += 1
        elif "омтцк" in low or "мтцк" in low or "міськ" in low:
            counts["city"] += 1
        else:
            counts["district"] += 1
    return counts


def main() -> int:
    prev = load_previous()
    occupied = occupied_from_previous(prev)
    collected: dict[str, list[str]] = {}
    source = ""

    try:
        print(f"Fetching HTML {MOD_HTML_URL}")
        html = _decode(_fetch(MOD_HTML_URL))
        collected = parse_html(html)
        source = "mod.gov.ua HTML"
        print(f"  HTML entries: {_count(collected)}")
    except Exception as exc:
        print(f"  HTML failed: {exc}")

    if _count(collected) < MIN_ENTRIES:
        try:
            print(f"Fetching PDF page {MOD_PDF_PAGE_URL}")
            pdf_page = _decode(_fetch(MOD_PDF_PAGE_URL))
            pdf_url = _find_pdf_url(pdf_page)
            if pdf_url:
                print(f"  PDF url: {pdf_url}")
                pdf_bytes = _fetch(pdf_url, timeout=90)
                from_pdf = parse_pdf_bytes(pdf_bytes)
                print(f"  PDF entries: {_count(from_pdf)}")
                collected = _merge_region_maps(collected, from_pdf)
                source = (source + " + PDF").strip(" +")
            else:
                print("  PDF link not found on the page")
        except Exception as exc:
            print(f"  PDF failed: {exc}")

    if _count(collected) < MIN_ENTRIES:
        try:
            print(f"Fetching official-directory mirror {MIRROR_URL}")
            mirror = _decode(_fetch(MIRROR_URL))
            from_mirror = parse_html(mirror)
            print(f"  mirror entries: {_count(from_mirror)}")
            collected = _merge_region_maps(collected, from_mirror)
            source = (source + " + mirror").strip(" +") or "mirror"
        except Exception as exc:
            print(f"  mirror failed: {exc}")

    total = _count(collected)
    collected = _split_kyiv_city(collected)
    total = _count(collected)
    if total < MIN_ENTRIES:
        print(f"ERROR: only {total} entries (need >={MIN_ENTRIES})", file=sys.stderr)
        return 1

    data = build_output(collected, occupied)
    all_names = [n for b in data.values() for n in b.get("entries") or []]
    stats = _kind_stats(all_names)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(data, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT}")
    print(f"source={source}")
    print(f"regions={len(data)} entries={len(all_names)} {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
