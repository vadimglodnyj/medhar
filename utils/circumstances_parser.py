import re
from typing import Optional, Dict


DATE_REGEX = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{2,4})\b")


def _normalize_year(year_str: str) -> str:
    if len(year_str) == 2:
        # Assume 2000+ for two-digit years
        return f"20{year_str}"
    return year_str


def extract_injury_date(text: str) -> Optional[str]:
    if not text:
        return None
    # Take the last date in the text as injury date (often appears after time range)
    matches = list(DATE_REGEX.finditer(text))
    if not matches:
        return None
    day, month, year = matches[-1].groups()
    year = _normalize_year(year)
    return f"{day}.{month}.{year}"


def extract_location(text: str) -> Optional[str]:
    if not text:
        return None
    # Prefer "район н.п. <NAME>"
    m = re.search(r"район\s+н\.\s*п\.\s*([^,\)]+)", text, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('\"\'')
    # Fallback to "н.п. <NAME>"
    m = re.search(r"н\.\s*п\.\s*([^,\)]+)", text, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('\"\'')
    return None


def _convert_to_genitive_case(factor: str) -> str:
    """Конвертує фактор в родовому відмінку для правильного використання з 'внаслідок'"""
    factor_lower = factor.lower().strip()
    
    # Словник для перетворення в родовому відмінку
    genitive_conversions = {
        'мінометний обстріл': 'мінометного обстрілу',
        'артилерійський обстріл': 'артилерійського обстрілу',
        'ракетний обстріл': 'ракетного обстрілу',
        'обстріл': 'обстрілу',
        'вибух': 'вибуху',
        'дрг': 'ДРГ',  # ДРГ залишається незмінним
    }
    
    # Перевіряємо точне співпадіння
    if factor_lower in genitive_conversions:
        return genitive_conversions[factor_lower]
    
    # Якщо не знайдено точне співпадіння, повертаємо оригінал
    return factor


def extract_factor(text: str) -> Optional[str]:
    if not text:
        return None
    # Common factors
    factor_patterns = [
        r"мінометн\w*\s+обстріл\w*",
        r"артилерійськ\w*\s+обстріл\w*",
        r"ракетн\w*\s+обстріл\w*",
        r"обстріл\w*",
        r"вибух\w*",
        r"ДРГ",
    ]
    for pat in factor_patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            factor = m.group(0)
            return _convert_to_genitive_case(factor)
    return None


def parse_circumstances(text: str) -> Dict[str, Optional[str]]:
    return {
        "injury_date": extract_injury_date(text),
        "location": extract_location(text),
        "factor": extract_factor(text),
    }


