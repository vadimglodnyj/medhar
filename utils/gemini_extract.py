# -*- coding: utf-8 -*-
"""Зчитування діагнозу та рекомендацій з фото/PDF через Gemini."""

from __future__ import annotations

import json
import logging
import re
import time

from config import TREATMENT_MEDIA_MAX_BYTES
import config as _cfg

logger = logging.getLogger(__name__)

ALLOWED_MIME = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
    "image/gif": "image/gif",
    "image/heic": "image/heic",
    "image/heif": "image/heif",
    "application/pdf": "application/pdf",
}

_PROMPT = """Ти медичний асистент медпункту батальйону.
З фото або PDF медичного документа (виписка, консультативний висновок, лікарняний лист, направлення)
витягни лише те, що реально написано.

Поверни JSON з полями:
- diagnosis: клінічний діагноз українською, як у документі (без ПІБ, ІПН, телефону, дат народження).
- recommendations: рекомендації лікаря. Якщо кілька пунктів — кожен з нового рядка, без нумерації «1.» якщо в документі маркери.

Правила:
- Не вигадуй діагноз і не дописуй лікування від себе.
- Якщо поля немає в документі — порожній рядок.
- Ігноруй шапки установ, печатки, підписи, персональні дані.
"""


def gemini_configured() -> bool:
    return bool(_cfg.GEMINI_API_KEY)


def _parse_json_payload(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Очікувався JSON-об'єкт")
    diagnosis = str(data.get("diagnosis") or "").strip()
    recs = str(
        data.get("recommendations")
        or data.get("exam_result")
        or data.get("рекомендації")
        or ""
    ).strip()
    return {"diagnosis": diagnosis, "recommendations": recs}


def extract_diagnosis_from_parts(parts: list[tuple[bytes, str]]) -> dict:
    """
    parts: список (bytes, mime_type).
    Повертає {diagnosis, recommendations}.
    """
    if not _cfg.GEMINI_API_KEY:
        raise RuntimeError("Не задано _cfg.GEMINI_API_KEY у файлі .env")
    if not parts:
        raise ValueError("Немає файлів для зчитування")

    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise RuntimeError(
            "Не встановлено пакет google-genai. Виконайте: pip install google-genai"
        ) from e

    contents = []
    for blob, mime in parts:
        if len(blob) > TREATMENT_MEDIA_MAX_BYTES:
            raise ValueError("Файл завеликий (макс. 10 МБ)")
        mime_n = ALLOWED_MIME.get((mime or "").lower().strip())
        if not mime_n:
            raise ValueError(f"Непідтримуваний тип файлу: {mime or 'невідомо'}")
        contents.append(types.Part.from_bytes(data=blob, mime_type=mime_n))
    contents.append(_PROMPT)

    client = genai.Client(api_key=_cfg.GEMINI_API_KEY)
    preferred = (_cfg.GEMINI_MODEL or "").strip()
    candidates = []
    for name in (
        preferred,
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-flash-latest",
    ):
        if name and name not in candidates:
            candidates.append(name)

    def _is_model_unavailable(exc: Exception) -> bool:
        msg = str(exc).casefold()
        return any(
            token in msg
            for token in (
                "404",
                "not_found",
                "no longer available",
                "503",
                "unavailable",
                "high demand",
                "overloaded",
                "resource_exhausted",
                "429",
                "try again later",
            )
        )

    last_error = None
    response = None
    used_model = candidates[0]
    for model in candidates:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                used_model = model
                break
            except Exception as e:
                last_error = e
                if _is_model_unavailable(e):
                    if attempt == 0:
                        logger.warning(
                            "Модель %s тимчасово недоступна, повтор через 1.5 с",
                            model,
                        )
                        time.sleep(1.5)
                        continue
                    logger.warning("Модель %s недоступна, пробую наступну", model)
                    break
                raise
        if response is not None:
            break
    if response is None:
        raise RuntimeError(
            "Gemini тимчасово перевантажений. Спробуйте ще раз через хвилину "
            f"або змініть _cfg.GEMINI_MODEL у .env. ({last_error})"
            if last_error
            else "Gemini не відповів"
        )
    logger.info("Gemini extract: модель %s", used_model)
    raw = (getattr(response, "text", None) or "").strip()
    if not raw:
        raise RuntimeError("Gemini повернув порожню відповідь")
    try:
        return _parse_json_payload(raw)
    except Exception:
        logger.warning("Не вдалося розібрати JSON Gemini: %s", raw[:400])
        raise RuntimeError("Не вдалося розібрати відповідь Gemini")
