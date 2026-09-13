"""Daily literature opportunities monitor.

Reads Sheet1, checks websites listed in column B, asks OpenAI to extract current
opportunities, updates only columns F:H, and sends a Telegram run summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from analyse_with_ai import analyse_site
from google_sheet import SheetRow, read_rows, update_rows
from telegram import send_message
from web_reader import SiteEvidence, collect_site_evidence


RIGA = ZoneInfo("Europe/Riga")
DEFAULT_HASH_CACHE = Path("liter/.cache/site_hashes.json")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _evidence_hash(evidence: SiteEvidence) -> str:
    """Return a stable SHA-256 hash for the fetched pages of one site."""
    pages = [
        {
            "url": page.url,
            "title": " ".join(page.title.split()),
            "text": " ".join(page.text.split()),
        }
        for page in sorted(evidence.pages, key=lambda item: item.url)
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


def run() -> int:
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
    ai_checked = 0
    unchanged = 0

    for row in rows:
        evidence = evidence_by_row[row.row_number]
        if evidence.error or not evidence.pages:
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
            failures.append(f"{row.name or row.url}: AI — {type(exc).__name__}: {exc}")
            continue

        # Cache every successfully analysed page, including ambiguous results.
        # Otherwise unchanged ambiguous sites would be sent to the AI again daily.
        site_hashes[row.url] = current_hash

        if not result.confident:
            failures.append(f"{row.name or row.url}: неоднозначные данные; старые значения сохранены")
            continue

        proposed = (result.open_call, result.awards, result.submissions)
        if proposed != (row.open_call, row.awards, row.submissions):
            changes[row.row_number] = proposed
        new_items.extend(result.new_opportunities)

    if changes:
        update_rows(spreadsheet_id, sheet_name, changes)

    active_urls = {row.url for row in rows}
    _save_hash_cache(
        hash_cache_path,
        {url: site_hash for url, site_hash in site_hashes.items() if url in active_urls},
    )

    summary_lines = [
        f"Литературный монитор: {checked_at:%Y-%m-%d}",
        f"Проверено сайтов: {len(rows)}",
        f"Проанализировано AI: {ai_checked}",
        f"Без изменений, AI пропущен: {unchanged}",
        f"Изменено строк: {len(changes)}",
        f"Ошибок/нужна проверка: {len(failures)}",
    ]
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

    summary = "\n".join(summary_lines)
    print(summary)
    send_message(summary)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Run only when the current Europe/Riga hour is 08.",
    )
    args = parser.parse_args()

    if args.scheduled and datetime.now(RIGA).hour != 8:
        print("Skipping duplicate UTC cron slot; it is not 08:00 in Europe/Riga.")
        return 0

    try:
        return run()
    except Exception as exc:
        message = f"Литературный монитор не запустился: {type(exc).__name__}: {exc}"
        print(message, file=sys.stderr)
        send_message(message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
