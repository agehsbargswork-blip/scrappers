"""Optional Telegram notifications."""

from __future__ import annotations

import os

import requests


MAX_MESSAGE_LENGTH = 4096


def send_message(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return

    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[: MAX_MESSAGE_LENGTH - 2].rstrip() + "\n…"

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=20,
    )
    response.raise_for_status()


def send_messages(messages: list[str]) -> None:
    for message in messages:
        send_message(message)
