"""Optional Telegram notifications."""

from __future__ import annotations

import os

import requests


MAX_MESSAGE_LENGTH = 4096


def _truncate(message: str, *, parse_mode: str | None) -> str:
    if len(message) <= MAX_MESSAGE_LENGTH:
        return message
    if parse_mode != "HTML":
        return message[: MAX_MESSAGE_LENGTH - 2].rstrip() + "\n…"

    message = message[: MAX_MESSAGE_LENGTH - 32].rstrip()
    if message.rfind("<") > message.rfind(">"):
        message = message[: message.rfind("<")].rstrip()
    if message.rfind("&") > message.rfind(";"):
        message = message[: message.rfind("&")].rstrip()
    message += "</b>" * max(0, message.count("<b>") - message.count("</b>"))
    return message + "\n…"


def send_message(message: str, *, parse_mode: str | None = None) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return

    message = _truncate(message, parse_mode=parse_mode)
    payload = {"chat_id": chat_id, "text": message, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=20,
    )
    response.raise_for_status()


def send_messages(messages: list[str], *, parse_mode: str | None = None) -> None:
    for message in messages:
        send_message(message, parse_mode=parse_mode)
