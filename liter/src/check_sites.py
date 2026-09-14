"""Daily literature opportunities monitor.

Reads Sheet1, checks websites listed in column B, asks OpenAI to extract current
opportunities, updates only columns F:H, and sends a Telegram run summary.
"""

from __future__ import annotations

import argparse
import html
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from analyse_with_ai import analyse_site
from google_sheet import SheetRow, read_rows, update_rows
from telegram import send_message, send_messages
from web_reader import SiteEvidence, collect_site_evidence


RIGA = ZoneInfo("Europe/Riga")
DEFAULT_HASH_CACHE = Path("liter/.cache/site_hashes.json")
DEFAULT_DAILY_RUN_MARKER = Path("liter/.cache/last_scheduled_run.txt")
URL_PATTERN = re.compile(r"https?://[^\s)\]]+")
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")
DEADLINE_PATTERN = re.compile(
    r"\bдедлайн\s*[—–:-]\s*"
    r"((?:до\s+)?(?:\d{1,2}\s+[а-яё]+\s+\d{4}\s*(?:г(?:ода)?\.?)?"
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|[^.;\n]+))",
    re.IGNORECASE,
)
DYNAMIC_HOME_PATHS = {
    "prodaman.ru": {""},
    "litnet.com": {"", "ru"},
    "ficbook.net": {""},
}
TRACKING_QUERY_PREFIXES = ("utm_",)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _evidence_hash(evidence: SiteEvidence) -> str:
    """Return a stable SHA-256 hash for the fetched pages of one site."""
    pages_to_hash = evidence.pages
    requested = urlsplit(evidence.requested_url)
    requested_host = (requested.hostname or "").removeprefix("www.").casefold()
    requested_path = requested.path.strip("/").casefold()
    if (
        requested_path in DYNAMIC_HOME_PATHS.get(requested_host, set())
        and len(evidence.pages) > 1
    ):
        # These platforms have live rankings, counters, feeds and online statuses
        # on their home pages. The home page is useful for discovering relevant
        # links, but hashing it would trigger AI on almost every run.
        pages_to_hash = evidence.pages[1:]

    pages = [
        {
            "url": page.url,
            "title": " ".join(page.title.split()),
            "text": " ".join(page.text.split()),
        }
        for page in sorted(pages_to_hash, key=lambda item: item.url)
    ]
    payload = json.dumps(
        pages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_hash_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(url): str(site_hash)
        for url, site_hash in payload.items()
        if isinstance(url, str) and isinstance(site_hash, str)
    }


def _save_hash_cache(path: Path, hashes: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(hashes, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _scheduled_run_already_completed(path: Path, checked_at: datetime) -> bool:
    try:
        completed_date = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return completed_date == checked_at.date().isoformat()


def _mark_scheduled_run_completed(path: Path, checked_at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(checked_at.date().isoformat() + "\n", encoding="utf-8")
    temporary.replace(path)


def _collect_all(rows: list[SheetRow], workers: int) -> dict[int, SiteEvidence]:
    evidence: dict[int, SiteEvidence] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(collect_site_evidence, row.url): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            try:
                evidence[row.row_number] = future.result()
            except Exception as exc:  # one broken site must not stop the run
                evidence[row.row_number] = SiteEvidence(
                    requested_url=row.url,
                    pages=[],
                    error=f"{type(exc).__name__}: {exc}",
                )
    return evidence


def _structure_opportunities(value: str) -> str:
    value = value.strip()
    if any(line.startswith("Название:") for line in value.splitlines()):
        return value

    # Old sheet values can use two or more lines for one opportunity: a title,
    # followed by a description/deadline line that ends with the official URL.
    # Group through the URL instead of treating every line as a separate item.
    legacy_blocks: list[list[str]] = []
    current_block: list[str] = []
    for line in (line.strip() for line in value.splitlines() if line.strip()):
        current_block.append(line)
        if URL_PATTERN.search(line):
            legacy_blocks.append(current_block)
            current_block = []
    if current_block:
        legacy_blocks.append(current_block)

    blocks: list[str] = []
    for legacy_lines in legacy_blocks:
        raw_block = "\n".join(legacy_lines)
        urls = URL_PATTERN.findall(raw_block)
        source = urls[-1].rstrip(".,;") if urls else "не указан"

        clean_lines: list[str] = []
        for line in legacy_lines:
            line = MARKDOWN_LINK_PATTERN.sub("", line)
            line = URL_PATTERN.sub("", line).strip(" -;,.[]()")
            if line:
                clean_lines.append(line)

        if len(clean_lines) > 1:
            title = clean_lines[0]
            description = " ".join(clean_lines[1:])
            deadline_match = DEADLINE_PATTERN.search(description)
            if deadline_match:
                deadline = deadline_match.group(1).strip(" -;,.[]()")
                description = (
                    description[: deadline_match.start()]
                    + description[deadline_match.end() :]
                ).strip(" -;,.[]()")
            else:
                deadline = "не указан"
            description = description or "Подробности — по ссылке."
        else:
            body = clean_lines[0] if clean_lines else ""
            parts = [part.strip(" -;,.[]()") for part in body.split(" — ", 2)]
            title = parts[0] or "Не указано"
            deadline = parts[1] if len(parts) > 1 and parts[1] else "не указан"
            description = (
                parts[2] if len(parts) > 2 and parts[2] else "Подробности — по ссылке."
            )
        blocks.append(
            "\n".join([
                f"Название: {title}",
                f"Дедлайн: {deadline}",
                f"Описание: {description}",
                f"URL: {source}",
            ])
        )
    return "\n\n".join(blocks)


def _opportunity_blocks(value: str) -> list[str]:
    """Return one structured block per current opportunity."""
    if not value.strip():
        return []
    structured = _structure_opportunities(value)
    return [
        block.strip()
        for block in re.split(r"(?m)(?=^Название:\s*)", structured)
        if block.strip()
    ]


def _canonical_url(value: str) -> str:
    """Normalise harmless URL variations without merging distinct pages."""
    parsed = urlsplit(value.rstrip(".,;"))
    host = (parsed.hostname or "").removeprefix("www.").casefold()
    port = f":{parsed.port}" if parsed.port else ""
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/") or "/"
    query = urlencode(sorted([
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith(TRACKING_QUERY_PREFIXES)
    ]))
    return urlunsplit(("", host + port, path, query, ""))


def _opportunity_identity(block: str) -> tuple[str, str]:
    """Identify an opportunity by official URL plus official title/season."""
    title = ""
    source = ""
    for line in block.splitlines():
        if line.startswith("Название:"):
            title = line.removeprefix("Название:").strip()
        elif line.startswith("URL:"):
            urls = URL_PATTERN.findall(line)
            if urls:
                source = _canonical_url(urls[-1])
    normalised_title = " ".join(title.casefold().split()).strip(" -;,.[]()«»\"")
    return source, normalised_title


def _partition_opportunities(previous: str, current: str) -> tuple[list[str], list[str]]:
    """Split current opportunities into new and already known blocks."""
    previous_identities = {
        _opportunity_identity(block) for block in _opportunity_blocks(previous)
    }
    new: list[str] = []
    old: list[str] = []
    for block in _opportunity_blocks(current):
        target = old if _opportunity_identity(block) in previous_identities else new
        target.append(block)
    return new, old


def _change_item(row: SheetRow, value: str, *, bold_labels: bool = False) -> str:
    name = html.escape(row.name or row.url)
    if bold_labels:
        value = _structure_opportunities(value)
    lines: list[str] = []
    for raw_line in value.strip().splitlines():
        line = html.escape(raw_line)
        if bold_labels:
            for label in ("Название", "Дедлайн", "Описание", "URL"):
                prefix = f"{label}:"
                if line.startswith(prefix):
                    line = f"<b>{prefix}</b>{line[len(prefix):]}"
                    break
        lines.append(line)
    return f"\n<b>Платформа:</b> {name}\n{'\n'.join(lines)}"


def _change_message(title: str, new: list[str], old: list[str]) -> str:
    lines = [f"<b>{html.escape(title)}</b>", "", "<b>Новые:</b>"]
    lines.extend(new or ["Нет"])
    lines.extend(["", "<b>Старые:</b>"])
    lines.extend(old or ["Нет"])
    return "\n".join(lines)


def _unclear_message(rows: list[SheetRow]) -> str:
    lines = ["Не удалось разобраться с этими платформами:"]
    if not rows:
        return "\n".join([*lines, "", "Нет"])
    for row in rows:
        name = html.escape(row.name or row.url)
        telegram_url = html.escape(row.telegram_url or "не указан")
        lines.extend([
            "",
            f"<b>Платформа:</b> {name}",
            f"<b>Телеграм:</b> {telegram_url}",
        ])
    return "\n".join(lines)


def _stats_message(
    checked_at: datetime,
    *,
    checked: int,
    ai_checked: int,
    unchanged: int,
    unclear: int,
) -> str:
    return "\n".join([
        f"Литературный монитор: {checked_at:%Y-%m-%d %H:%M}",
        f"Проверено сайтов: {checked}",
        f"Проанализировано AI: {ai_checked}",
        f"Без изменений, AI пропущен: {unchanged}",
        f"Неясные данные: {unclear}",
    ])


def _accepts_submissions(value: str) -> bool:
    return value.strip().casefold().startswith("принимают")


def run(*, scheduled_marker_path: Path | None = None) -> int:
    spreadsheet_id = os.getenv(
        "GOOGLE_SPREADSHEET_ID",
        "18vAZy_ftbN9wXRuwcXPUzrlTO8wHlGsUlzEppEO3Jok",
    )
    sheet_name = os.getenv("GOOGLE_SHEET_NAME", "Sheet1")
    model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    workers = max(1, min(int(os.getenv("FETCH_WORKERS", "8")), 16))
    hash_cache_path = Path(os.getenv("SITE_HASH_CACHE", str(DEFAULT_HASH_CACHE)))

    _required_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    _required_env("OPENAI_API_KEY")

    checked_at = datetime.now(RIGA)
    rows = read_rows(spreadsheet_id, sheet_name)
    evidence_by_row = _collect_all(rows, workers)
    site_hashes = _load_hash_cache(hash_cache_path)

    changes: dict[int, tuple[str, str, str]] = {}
    failures: list[str] = []
    new_items: list[str] = []
    changed_urls: list[str] = []
    new_submission_rows: set[int] = set()
    unclear_rows: list[SheetRow] = []
    ai_checked = 0
    unchanged = 0
    unclear = 0

    for row in rows:
        evidence = evidence_by_row[row.row_number]
        if evidence.error or not evidence.pages:
            unclear += 1
            unclear_rows.append(row)
            failures.append(f"{row.name or row.url}: {evidence.error or 'нет текста'}")
            continue

        current_hash = _evidence_hash(evidence)
        if site_hashes.get(row.url) == current_hash:
            unchanged += 1
            continue

        changed_urls.append(row.url)
        ai_checked += 1
        try:
            result = analyse_site(row, evidence, checked_at.date(), model=model)
        except Exception as exc:
            unclear += 1
            unclear_rows.append(row)
            failures.append(f"{row.name or row.url}: AI — {type(exc).__name__}: {exc}")
            continue

        # Cache every successfully analysed page, including ambiguous results.
        # Otherwise unchanged ambiguous sites would be sent to the AI again daily.
        site_hashes[row.url] = current_hash

        if not result.confident:
            unclear += 1
            unclear_rows.append(row)
            failures.append(f"{row.name or row.url}: неоднозначные данные; старые значения сохранены")
            continue

        proposed = (result.open_call, result.awards, result.submissions)
        if proposed != (row.open_call, row.awards, row.submissions):
            changes[row.row_number] = proposed
        if result.submissions != row.submissions:
            if _accepts_submissions(result.submissions):
                new_submission_rows.add(row.row_number)
        new_items.extend(result.new_opportunities)

    if changes:
        update_rows(spreadsheet_id, sheet_name, changes)

    active_urls = {row.url for row in rows}
    _save_hash_cache(
        hash_cache_path,
        {url: site_hash for url, site_hash in site_hashes.items() if url in active_urls},
    )
    if scheduled_marker_path is not None:
        # The site scan and Sheet update completed. Mark the day before Telegram
        # delivery so a temporary Telegram error cannot trigger duplicate AI work.
        _mark_scheduled_run_completed(scheduled_marker_path, checked_at)

    new_open_calls: list[str] = []
    old_open_calls: list[str] = []
    new_awards: list[str] = []
    old_awards: list[str] = []
    new_submissions: list[str] = []
    old_submissions: list[str] = []
    for row in rows:
        open_call, awards, submissions = changes.get(
            row.row_number,
            (row.open_call, row.awards, row.submissions),
        )
        row_new_open_calls, row_old_open_calls = _partition_opportunities(
            row.open_call,
            open_call,
        )
        new_open_calls.extend(
            _change_item(row, block, bold_labels=True) for block in row_new_open_calls
        )
        old_open_calls.extend(
            _change_item(row, block, bold_labels=True) for block in row_old_open_calls
        )

        row_new_awards, row_old_awards = _partition_opportunities(row.awards, awards)
        new_awards.extend(
            _change_item(row, block, bold_labels=True) for block in row_new_awards
        )
        old_awards.extend(
            _change_item(row, block, bold_labels=True) for block in row_old_awards
        )
        if _accepts_submissions(submissions):
            target = new_submissions if row.row_number in new_submission_rows else old_submissions
            target.append(_change_item(row, submissions))

    stats_message = _stats_message(
        checked_at,
        checked=len(rows),
        ai_checked=ai_checked,
        unchanged=unchanged,
        unclear=unclear,
    )
    telegram_messages = [
        stats_message,
        _change_message("Новости по опен-коллам", new_open_calls, old_open_calls),
        _change_message("Новости по конкурсам", new_awards, old_awards),
        _change_message("Куда можно отправить текст", new_submissions, old_submissions),
        _unclear_message(unclear_rows),
    ]

    summary_lines = [*telegram_messages, f"Изменено строк: {len(changes)}"]
    if changed_urls:
        summary_lines.append("Изменившийся SHA-256:")
        summary_lines.extend(f"• {url}" for url in changed_urls[:20])
        if len(changed_urls) > 20:
            summary_lines.append(f"• …и ещё {len(changed_urls) - 20}")
    if new_items:
        summary_lines.append("Новые возможности:")
        summary_lines.extend(f"• {item}" for item in new_items[:20])
    if failures:
        summary_lines.append("Не удалось надёжно проверить:")
        summary_lines.extend(f"• {item}" for item in failures[:20])

    summary = "\n\n".join(summary_lines)
    print(summary)
    send_messages(telegram_messages, parse_mode="HTML")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Run at most once per Europe/Riga calendar date.",
    )
    args = parser.parse_args()

    checked_at = datetime.now(RIGA)
    scheduled_marker_path = Path(
        os.getenv("SCHEDULED_RUN_MARKER", str(DEFAULT_DAILY_RUN_MARKER))
    )
    if args.scheduled and _scheduled_run_already_completed(
        scheduled_marker_path,
        checked_at,
    ):
        print(
            "Skipping scheduled run: "
            f"{checked_at.date().isoformat()} was already completed."
        )
        return 0

    try:
        return run(
            scheduled_marker_path=scheduled_marker_path if args.scheduled else None
        )
    except Exception as exc:
        message = f"Литературный монитор не запустился: {type(exc).__name__}: {exc}"
        print(message, file=sys.stderr)
        send_message(message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
