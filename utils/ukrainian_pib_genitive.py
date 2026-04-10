# -*- coding: utf-8 -*-
"""
Родовий відмінок ПІБ (називний → родовий) через pymorphy3 + українські словники.
Формат виводу: ПРІЗВИЩЕ Ім'я По батькові (прізвище великими літерами).
"""

from __future__ import annotations

import re
import threading
from typing import Optional

_morph = None
_morph_lock = threading.Lock()


def _get_morph():
    global _morph
    if _morph is None:
        with _morph_lock:
            if _morph is None:
                import pymorphy3

                _morph = pymorphy3.MorphAnalyzer(lang="uk")
    return _morph


def _capitalize_uk_word(s: str) -> str:
    if not s:
        return s
    return s[0].upper() + s[1:].lower()


def _gender_hint_from_second_given_name(second_word: str):
    """За іменем (другий токен) підказуємо рід для прізвища."""
    if not second_word or not second_word.strip():
        return None
    morph = _get_morph()
    for p in morph.parse(second_word.strip()):
        if "Name" in p.tag and "anim" in p.tag:
            if "femn" in p.tag:
                return "femn"
            if "masc" in p.tag:
                return "masc"
    return None


def _pick_genitive_word(
    word: str, index: int, total: int, gender_hint: Optional[str] = None
) -> str:
    """Один токен ПІБ → слово в родовому; index: 0 прізвище, 1 ім'я, 2 по батькові."""
    w = word.strip()
    if not w:
        return ""
    morph = _get_morph()
    parses = morph.parse(w)

    def first_gent(match):
        for p in parses:
            if match(p):
                inf = p.inflect({"gent"})
                if inf and inf.word:
                    return inf.word
        return None

    if total >= 3 and index == 2:
        r = first_gent(lambda p: "Patr" in p.tag and "masc" in p.tag)
        if r:
            return r
        r = first_gent(lambda p: "Patr" in p.tag and "femn" in p.tag)
        if r:
            return r
        r = first_gent(lambda p: "Patr" in p.tag)
        if r:
            return r
    if total >= 2 and index == 1:
        r = first_gent(lambda p: "Name" in p.tag and "anim" in p.tag)
        if r:
            return r
    if index == 0:
        if gender_hint == "femn":
            r = first_gent(lambda p: "Surn" in p.tag and "femn" in p.tag)
            if r:
                return r
        r = first_gent(
            lambda p: "Surn" in p.tag and "masc" in p.tag and "Fixd" not in p.tag
        )
        if r:
            return r
        r = first_gent(lambda p: "Surn" in p.tag and "femn" in p.tag)
        if r:
            return r
        r = first_gent(lambda p: "Surn" in p.tag)
        if r:
            return r

    p = parses[0]
    inf = p.inflect({"gent"})
    if inf and inf.word:
        return inf.word
    return w.lower()


def nominative_pib_to_genitive_line(pib_nazivnyi: str) -> str:
    """
    Повне ПІБ у називному → рядок для документа (родовий, прізвище CAPS).
    """
    if not pib_nazivnyi or not str(pib_nazivnyi).strip():
        return ""
    parts = re.sub(r"\s+", " ", str(pib_nazivnyi).strip()).split(" ")
    parts = [p for p in parts if p]
    if not parts:
        return ""
    total = len(parts)
    gender_hint = _gender_hint_from_second_given_name(parts[1]) if total >= 2 else None
    out = []
    for i, part in enumerate(parts):
        gw = _pick_genitive_word(part, i, total, gender_hint)
        if i == 0:
            out.append(gw.upper())
        else:
            out.append(_capitalize_uk_word(gw))
    return " ".join(out)


def format_rodovyi_manual_caps(rodovyi_user: str) -> str:
    """Користувач уже ввів родовий — лише нормалізуємо регістр: прізвище CAPS, решта як ім'я."""
    if not rodovyi_user or not str(rodovyi_user).strip():
        return ""
    parts = re.sub(r"\s+", " ", str(rodovyi_user).strip()).split(" ")
    parts = [p for p in parts if p]
    if not parts:
        return ""
    out = [parts[0].upper()]
    out.extend(_capitalize_uk_word(p) for p in parts[1:])
    return " ".join(out)


def format_nominative_pib_display(pib_nazivnyi: str) -> str:
    """ПІБ у називному для документа: прізвище великими літерами, ім'я та по батькові — з великої."""
    return format_rodovyi_manual_caps(pib_nazivnyi)


def build_pib_rodovyi_for_document(
    pib_nazivnyi: str, pib_rodovyi_manual: Optional[str] = None
) -> str:
    """
    Якщо є непорожнє ручне поле родового — застосовуємо його (з CAPS прізвища).
    Інакше — автоматично з називного.
    """
    manual = (pib_rodovyi_manual or "").strip()
    if manual:
        return format_rodovyi_manual_caps(manual)
    return nominative_pib_to_genitive_line(pib_nazivnyi)
