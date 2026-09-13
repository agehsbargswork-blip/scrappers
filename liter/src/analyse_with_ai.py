"""Semantic extraction of opportunities from official website text."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from openai import OpenAI

from google_sheet import SheetRow
from web_reader import SiteEvidence


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
- submissions: начни строго с «Принимают», «Не принимают» или «Неясно»; затем коротко
  опиши общие правила и возможности подачи текста и заверши прямым официальным URL.
- Пиши предельно кратко, без пересказа общих правил сайта и без вводных фраз.
- Каждую возможность в open_call и awards записывай отдельным блоком; между блоками оставляй
  пустую строку. Используй строго четыре строки:
  Название: точное официальное название возможности; обязательно включи номер сезона,
  год или выпуск, если они указаны в источнике
  Дедлайн: дата или «не указан»
  Описание: одно короткое предложение — что принимают или для кого возможность
  URL: прямой официальный URL
- Короткое описание должно состоять из одного предложения. Не перечисляй второстепенные
  условия, общие запреты платформы, требования законодательства и авторского права.
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
        open_call=_normalise_opportunity(payload["open_call"]),
        awards=_normalise_opportunity(payload["awards"]),
        submissions=payload["submissions"].strip(),
        new_opportunities=[str(item).strip() for item in payload["new_opportunities"]],
    )
