"""Range-precise Google Sheets reads and writes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


@dataclass(frozen=True)
class SheetRow:
    row_number: int
    name: str
    url: str
    open_call: str
    awards: str
    submissions: str


def _service():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _cell(row: list[str], index: int) -> str:
    return str(row[index]).strip() if index < len(row) else ""


def read_rows(spreadsheet_id: str, sheet_name: str) -> list[SheetRow]:
    response = (
        _service()
        .spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A:H")
        .execute()
    )
    values = response.get("values", [])
    result: list[SheetRow] = []
    for row_number, row in enumerate(values[1:], start=2):
        url = _cell(row, 1)
        if not url.startswith(("http://", "https://")):
            continue
        result.append(
            SheetRow(
                row_number=row_number,
                name=_cell(row, 2),
                url=url,
                open_call=_cell(row, 5),
                awards=_cell(row, 6),
                submissions=_cell(row, 7),
            )
        )
    return result


def update_rows(
    spreadsheet_id: str,
    sheet_name: str,
    changes: dict[int, tuple[str, str, str]],
) -> None:
    data = [
        {
            "range": f"{sheet_name}!F{row_number}:H{row_number}",
            "values": [[open_call, awards, submissions]],
        }
        for row_number, (open_call, awards, submissions) in sorted(changes.items())
    ]
    (
        _service()
        .spreadsheets()
        .values()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        )
        .execute()
    )

    # Read back the exact ranges so a silent partial write becomes a failed run.
    verification = (
        _service()
        .spreadsheets()
        .values()
        .batchGet(
            spreadsheetId=spreadsheet_id,
            ranges=[item["range"] for item in data],
        )
        .execute()
    )
    actual = [item.get("values", [[]])[0] for item in verification.get("valueRanges", [])]
    expected = [item["values"][0] for item in data]
    if actual != expected:
        raise RuntimeError("Google Sheets verification failed after batchUpdate")
