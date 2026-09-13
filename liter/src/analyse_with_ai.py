"""Semantic extraction of opportunities from official website text."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from openai import OpenAI

from google_sheet import SheetRow
from web_reader import SiteEvidence


URL_PATTERN = re.compile(r"https?://[^\s)\]]+")


@dataclass(frozen=True)
class AnalysisResult:
    confident: bool
    open_call: str
    awards: str
    submissions: str
    new_opportunities: list[str]


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "confident": {"type": "boolean"},
        "open_call": {"type": "string"},
        "awards": {"type": "string"},
        "submissions": {"type": "string"},
        "new_opportunities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["confident", "open_call", "awards", "submissions", "new_opportunities"],
}


def _normalise_opportunity(value: str) -> str:
    """Keep cells empty when the model reports that nothing is active."""
    value = value.strip()
    if value.casefold().startswith("нет активных"):
        return ""
    return value


def _compact_with_source(value: str, limit: int) -> str:
    """Limit verbose model output while retaining its first official source URL."""
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    urls = URL_PATTERN.findall(value)
    source = urls[0].rstrip(".,;") if urls else ""
    text = URL_PATTERN.sub("", value).strip(" -;,.[]()")
    suffix = f" {source}" if source else ""
    budget = max(1, limit - len(suffix) - 1)
    return f"{text[:budget].rstrip()}…{suffix}"


def _compact_opportunities(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return "\n".join(_compact_with_source(line, 240) for line in lines)


def analyse_site(
    row: SheetRow,
    evidence: SiteEvidence,
    today: date,
    *,
    model: str,
) -> AnalysisResult:
    pages = "\n\n".join(
        f"SOURCE: {page.url}\nTITLE: {page.title}\nTEXT:\n{page.text}"
        for page in evidence.pages
    )
    prompt = f"""Сегодня {today.isoformat()}.

Проверь официальные материалы сайта и подготовь значения для трёх ячеек Google Sheet.

Правила:
- Пиши по-русски.
- open_call: только действующие открытые наборы, не премии и не конкурсы.
  Если действующих наборов нет, верни пустую строку.
- awards: только действующие премии, призы и конкурсы.
  Если действующих премий, призов и конкурсов нет, верни пустую строку.
- Никогда не пиши «Нет активных» и не добавляй дату проверки в пустые ячейки.
- submissions: начни строго с «Принимают», «Не принимают» или «Неясно»; затем жанры,
  способ подачи, ограничения, период приёма и прямой официальный URL.
- Для каждой возможности укажи название, дедлайн, если опубликован, и прямой URL.
- Пиши предельно кратко, без пересказа общих правил сайта и без вводных фраз.
- Каждую возможность в open_call и awards записывай с новой строки, максимум 240 символов:
  «Название — дедлайн или “не указан” — прямой официальный URL».
- submissions: максимум 350 символов вместе с URL. Укажи только статус, принимаемые жанры
  или типы текстов, способ подачи, главное ограничение и период/дедлайн. Не перечисляй
  общие запреты платформы, требования законодательства и авторского права.
- Не используй просроченные возможности.
- Не делай вывод по отсутствию информации. Если доказательств недостаточно, confident=false.
- URL должен присутствовать среди SOURCE ниже. Ничего не выдумывай.

Публикация: {row.name}
Основной URL: {row.url}
Текущие значения:
OpenCall: {row.open_call}
Awards: {row.awards}
Submissions: {row.submissions}

Официальные страницы:
{pages}
"""

    response = OpenAI().responses.create(
        model=model,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "site_analysis",
                "strict": True,
                "schema": SCHEMA,
            }
        },
    )
    payload = json.loads(response.output_text)
    return AnalysisResult(
        confident=payload["confident"],
        open_call=_normalise_opportunity(_compact_opportunities(payload["open_call"])),
        awards=_normalise_opportunity(_compact_opportunities(payload["awards"])),
        submissions=_compact_with_source(payload["submissions"], 350),
        new_opportunities=[
            _compact_with_source(str(item), 240) for item in payload["new_opportunities"]
        ],
    )
