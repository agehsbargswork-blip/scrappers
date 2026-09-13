"""Optional Telegram notifications."""

from __future__ import annotations

import os

import requests


def send_message(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message[:4096], "disable_web_page_preview": True},
        timeout=20,
    )
    response.raise_for_status()
