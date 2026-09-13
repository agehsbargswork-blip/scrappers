"""Optional Telegram notifications."""

from __future__ import annotations

import os
import textwrap
import time

import requests


MAX_MESSAGE_LENGTH = 4096
MAX_SEND_ATTEMPTS = 5
SEND_DELAY_SECONDS = 1.0
RETRY_BASE_SECONDS = 2.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


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


def _retry_delay(response: requests.Response | None, attempt: int) -> float:
    if response is not None and response.status_code == 429:
        try:
            retry_after = float(response.json().get("parameters", {}).get("retry_after", 0))
        except (TypeError, ValueError):
            retry_after = 0
        if retry_after > 0:
            return retry_after
    return RETRY_BASE_SECONDS * (2**attempt)


def _send_chunk(
    token: str,
    chat_id: str,
    chunk: str,
    *,
    parse_mode: str | None,
) -> None:
    payload = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    for attempt in range(MAX_SEND_ATTEMPTS):
        response: requests.Response | None = None
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
                timeout=(10, 30),
            )
            if response.status_code < 400:
                return
            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
        except (requests.ConnectionError, requests.Timeout):
            if attempt == MAX_SEND_ATTEMPTS - 1:
                raise
        else:
            if attempt == MAX_SEND_ATTEMPTS - 1:
                response.raise_for_status()

        time.sleep(_retry_delay(response, attempt))


def _send_chunks(chunks: list[str], *, parse_mode: str | None) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return

    for index, chunk in enumerate(chunks):
        _send_chunk(token, chat_id, chunk, parse_mode=parse_mode)
        if index < len(chunks) - 1:
            time.sleep(SEND_DELAY_SECONDS)


def send_message(message: str, *, parse_mode: str | None = None) -> None:
    _send_chunks(_split_message(message), parse_mode=parse_mode)


def send_messages(messages: list[str], *, parse_mode: str | None = None) -> None:
    chunks = [chunk for message in messages for chunk in _split_message(message)]
    _send_chunks(chunks, parse_mode=parse_mode)
