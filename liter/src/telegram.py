"""Optional Telegram notifications."""

from __future__ import annotations

import os
import textwrap

import requests


MAX_MESSAGE_LENGTH = 4096


def _split_message(message: str) -> list[str]:
    if len(message) <= MAX_MESSAGE_LENGTH:
        return [message]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in message.splitlines():
        parts = textwrap.wrap(
            line,
            width=MAX_MESSAGE_LENGTH,
            break_long_words=True,
            break_on_hyphens=False,
            replace_whitespace=False,
            drop_whitespace=False,
        ) or [""]
        for part in parts:
            separator_length = 1 if current else 0
            if current and current_length + separator_length + len(part) > MAX_MESSAGE_LENGTH:
                chunks.append("\n".join(current).rstrip())
                current = []
                current_length = 0
                separator_length = 0
            current.append(part)
            current_length += separator_length + len(part)
    if current:
        chunks.append("\n".join(current).rstrip())
    return chunks

def send_message(message: str, *, parse_mode: str | None = None) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return

    for chunk in _split_message(message):
        payload = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
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
