# -*- coding: utf-8 -*-
"""Сповіщення в Discord через Incoming Webhook каналу."""

from __future__ import annotations

import json
import re
from urllib import error, request

WEBHOOK_RE = re.compile(
    r"^https://(?:discord|discordapp)\.com/api/webhooks/\d+/[A-Za-z0-9_.-]+/?$",
    re.IGNORECASE,
)


class DiscordError(RuntimeError):
    """Помилка Discord webhook."""


def normalize_webhook_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not WEBHOOK_RE.match(text.rstrip("/")) and not WEBHOOK_RE.match(text):
        raise DiscordError(
            "Це не схоже на Discord webhook. У каналі: Редагувати → Інтеграції → Вебхуки."
        )
    return text.rstrip("/")


def normalize_user_id(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return ""
    if len(digits) < 17 or len(digits) > 20:
        raise DiscordError("Discord ID має бути числом з профілю (Копіювати ID користувача).")
    return digits


def send_webhook(
    url: str,
    content: str,
    *,
    user_ids: list[str] | None = None,
    poll: dict | None = None,
) -> dict:
    webhook = normalize_webhook_url(url)
    if not webhook:
        raise DiscordError("Не задано Discord webhook.")
    body = (content or "").strip()
    if not body and not poll:
        raise DiscordError("Немає тексту для Discord.")
    if len(body) > 1900:
        body = body[:1897] + "…"
    mentions = [uid for uid in (user_ids or []) if uid]
    payload: dict = {
        "content": body,
        "allowed_mentions": {"parse": [], "users": mentions},
    }
    if poll:
        payload["poll"] = poll
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        webhook + "?wait=true",
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "MedharBot/1.0",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise DiscordError(err.strip() or f"HTTP {exc.code}") from exc
    except Exception as exc:
        raise DiscordError(f"Не вдалося надіслати в Discord: {exc}") from exc
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def ack_poll(*, hours: int = 168) -> dict:
    """Кнопка «ПлюсПлюс» як опитування Discord — клікається без бота."""
    duration = max(1, min(int(hours or 168), 768))
    return {
        "question": {"text": "Для ознайомлення натисніть кнопку"},
        "answers": [
            {
                "poll_media": {
                    "text": "ПлюсПлюс",
                    "emoji": {"name": "➕"},
                }
            }
        ],
        "duration": duration,
        "allow_multiselect": False,
        "layout_type": 1,
    }
