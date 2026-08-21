from __future__ import annotations

import json
import mimetypes
import os
import re
import uuid
from urllib import error, request

import config as _cfg


class WhatsAppError(RuntimeError):
    """Помилка Meta WhatsApp Business API."""


SUPPORTED_MIME_KINDS = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "image/gif": "image",
    "application/pdf": "document",
}


def whatsapp_api_ready() -> bool:
    """Чи можна слати на довільний номер (без спільного WHATSAPP_RECIPIENT)."""
    return bool(
        (_cfg.WHATSAPP_ACCESS_TOKEN or "").strip()
        and (_cfg.WHATSAPP_PHONE_NUMBER_ID or "").strip()
    )


def whatsapp_configured() -> bool:
    return bool(whatsapp_api_ready() and (_cfg.WHATSAPP_RECIPIENT or "").strip())


def default_recipient() -> str:
    return normalize_recipient(_cfg.WHATSAPP_RECIPIENT or "")


def normalize_recipient(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        raise WhatsAppError("Не задано номер одержувача WhatsApp.")
    return digits


def supported_message_kind(mime: str) -> str:
    kind = SUPPORTED_MIME_KINDS.get((mime or "").strip().lower(), "")
    if not kind:
        raise WhatsAppError(f"Непідтримуваний тип медіа: {mime or 'unknown'}")
    return kind


def _base_url() -> str:
    version = (_cfg.WHATSAPP_API_VERSION or "v20.0").strip() or "v20.0"
    return f"https://graph.facebook.com/{version}"


def _json_request(url: str, payload: dict) -> dict:
    token = (_cfg.WHATSAPP_ACCESS_TOKEN or "").strip()
    if not token:
        raise WhatsAppError("Не задано WHATSAPP_ACCESS_TOKEN.")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return _load_json(req)


def _load_json(req: request.Request) -> dict:
    try:
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise WhatsAppError(_meta_error_message(body) or f"HTTP {exc.code}") from exc
    except Exception as exc:
        raise WhatsAppError(f"Помилка з'єднання з WhatsApp API: {exc}") from exc
    try:
        return json.loads(raw) if raw else {}
    except ValueError as exc:
        raise WhatsAppError("WhatsApp API повернув некоректний JSON.") from exc


def _meta_error_message(body: str) -> str:
    try:
        payload = json.loads(body or "{}")
    except ValueError:
        return body.strip()
    err = payload.get("error") or {}
    message = str(err.get("message") or "").strip()
    code = err.get("code")
    subcode = err.get("error_subcode")
    if code and subcode:
        return f"{message} (code {code}, subcode {subcode})"
    if code:
        return f"{message} (code {code})"
    return message or body.strip()


def open_whatsapp_chat(to: str, text: str) -> None:
    """Відкриває чат WhatsApp Desktop / wa.me з готовим текстом."""
    recipient = normalize_recipient(to)
    body = (text or "").strip()
    from urllib.parse import quote

    uri = f"whatsapp://send?phone={recipient}&text={quote(body)}"
    try:
        if os.name == "nt":
            os.startfile(uri)  # type: ignore[attr-defined]
            return
        import webbrowser

        webbrowser.open(uri)
        return
    except OSError:
        pass
    import webbrowser

    webbrowser.open(f"https://wa.me/{recipient}?text={quote(body)}")


def send_text(to: str, text: str) -> dict:
    phone_id = (_cfg.WHATSAPP_PHONE_NUMBER_ID or "").strip()
    if not phone_id:
        raise WhatsAppError("Не задано WHATSAPP_PHONE_NUMBER_ID.")
    recipient = normalize_recipient(to)
    body = (text or "").strip()
    if not body:
        raise WhatsAppError("Немає тексту для WhatsApp.")
    return _json_request(
        f"{_base_url()}/{phone_id}/messages",
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        },
    )


def upload_media(path: str, mime: str = "") -> str:
    phone_id = (_cfg.WHATSAPP_PHONE_NUMBER_ID or "").strip()
    token = (_cfg.WHATSAPP_ACCESS_TOKEN or "").strip()
    if not phone_id:
        raise WhatsAppError("Не задано WHATSAPP_PHONE_NUMBER_ID.")
    if not token:
        raise WhatsAppError("Не задано WHATSAPP_ACCESS_TOKEN.")
    if not path or not os.path.isfile(path):
        raise WhatsAppError("Файл медіа для WhatsApp не знайдено локально.")

    mime = (mime or mimetypes.guess_type(path)[0] or "application/octet-stream").strip().lower()
    boundary = f"----MedharWa{uuid.uuid4().hex}"
    filename = os.path.basename(path)
    with open(path, "rb") as fh:
        blob = fh.read()
    parts = [
        f"--{boundary}\r\n".encode("utf-8"),
        b'Content-Disposition: form-data; name="messaging_product"\r\n\r\n',
        b"whatsapp\r\n",
        f"--{boundary}\r\n".encode("utf-8"),
        f'Content-Disposition: form-data; name="type"\r\n\r\n{mime}\r\n'.encode("utf-8"),
        f"--{boundary}\r\n".encode("utf-8"),
        (
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8"),
        blob,
        b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    req = request.Request(
        f"{_base_url()}/{phone_id}/media",
        data=b"".join(parts),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    payload = _load_json(req)
    media_id = str(payload.get("id") or "").strip()
    if not media_id:
        raise WhatsAppError("WhatsApp API не повернув media id.")
    return media_id


def send_media(to: str, media_id: str, kind: str, caption: str = "", filename: str = "") -> dict:
    phone_id = (_cfg.WHATSAPP_PHONE_NUMBER_ID or "").strip()
    if not phone_id:
        raise WhatsAppError("Не задано WHATSAPP_PHONE_NUMBER_ID.")
    recipient = normalize_recipient(to)
    kind = (kind or "").strip().lower()
    if kind not in ("image", "document"):
        raise WhatsAppError(f"Непідтримуваний WhatsApp media kind: {kind}")

    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": kind,
        kind: {"id": str(media_id or "").strip()},
    }
    if caption and kind == "image":
        body[kind]["caption"] = caption
    if filename and kind == "document":
        body[kind]["filename"] = filename
    return _json_request(f"{_base_url()}/{phone_id}/messages", body)

