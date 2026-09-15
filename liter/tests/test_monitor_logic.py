"""Regression tests for hashing and Telegram new/old classification."""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

# The tested helpers are pure. Stub network-facing modules so this regression
# suite does not require credentials or third-party packages.
for module_name, attributes in {
    "analyse_with_ai": {"analyse_site": lambda *args, **kwargs: None},
    "google_sheet": {
        "SheetRow": object,
        "read_rows": lambda *args, **kwargs: [],
        "update_rows": lambda *args, **kwargs: None,
    },
    "telegram": {
        "send_message": lambda *args, **kwargs: None,
        "send_messages": lambda *args, **kwargs: None,
    },
    "web_reader": {
        "SiteEvidence": object,
        "collect_site_evidence": lambda *args, **kwargs: None,
    },
}.items():
    module = types.ModuleType(module_name)
    for attribute_name, attribute_value in attributes.items():
        setattr(module, attribute_name, attribute_value)
    sys.modules[module_name] = module

from check_sites import (  # noqa: E402
    _change_message,
    _deliver_telegram_outbox,
    _evidence_hash,
    _load_telegram_outbox,
    _mark_scheduled_run_completed,
    _partition_opportunities,
    _save_telegram_outbox,
    _scheduled_run_already_completed,
    _stats_message,
    _unclear_message,
)

for module_name in ("analyse_with_ai", "google_sheet", "telegram", "web_reader"):
    sys.modules.pop(module_name, None)


def page(url: str, title: str, text: str):
    return SimpleNamespace(url=url, title=title, text=text)


def evidence(requested_url: str, pages: list[SimpleNamespace]):
    return SimpleNamespace(requested_url=requested_url, pages=pages)


class EvidenceHashTests(unittest.TestCase):
    def test_dynamic_home_is_discovery_only_for_selected_platforms(self):
        platforms = [
            ("https://prodaman.ru/", "https://prodaman.ru/contests/?sortby=16"),
            ("https://litnet.com/ru", "https://litnet.com/ru/contests"),
            ("https://ficbook.net/", "https://ficbook.net/competitions"),
        ]
        for root, opportunity_page in platforms:
            with self.subTest(root=root):
                before = evidence(root, [
                    page(root, "Главная", "счётчик 1"),
                    page(opportunity_page, "Конкурсы", "Напарники"),
                ])
                noisy_home = evidence(root, [
                    page(root, "Главная", "счётчик 999"),
                    page(opportunity_page, "Конкурсы", "Напарники"),
                ])
                changed_opportunity = evidence(root, [
                    page(root, "Главная", "счётчик 999"),
                    page(opportunity_page, "Конкурсы", "Новый конкурс"),
                ])

                self.assertEqual(_evidence_hash(before), _evidence_hash(noisy_home))
                self.assertNotEqual(
                    _evidence_hash(before),
                    _evidence_hash(changed_opportunity),
                )

    def test_home_remains_fallback_when_no_relevant_page_was_found(self):
        before = evidence(
            "https://prodaman.ru/",
            [page("https://prodaman.ru/", "Главная", "счётчик 1")],
        )
        after = evidence(
            "https://prodaman.ru/",
            [page("https://prodaman.ru/", "Главная", "счётчик 2")],
        )
        self.assertNotEqual(_evidence_hash(before), _evidence_hash(after))


class ScheduledRunMarkerTests(unittest.TestCase):
    def test_marker_skips_only_the_same_calendar_date(self):
        with TemporaryDirectory() as directory:
            marker = Path(directory) / "last_scheduled_run.txt"
            first_run = datetime(2026, 9, 14, 8, 15)
            next_day = datetime(2026, 9, 15, 7, 55)

            self.assertFalse(_scheduled_run_already_completed(marker, first_run))

            _mark_scheduled_run_completed(marker, first_run)

            self.assertTrue(_scheduled_run_already_completed(marker, first_run))
            self.assertFalse(_scheduled_run_already_completed(marker, next_day))
            self.assertEqual(
                marker.read_text(encoding="utf-8"),
                "2026-09-14\n",
            )


class TelegramOutboxTests(unittest.TestCase):
    def test_failed_delivery_keeps_current_and_later_messages(self):
        with TemporaryDirectory() as directory:
            outbox = Path(directory) / "pending_telegram.json"
            _save_telegram_outbox(
                outbox,
                ["first", "second", "third"],
                parse_mode="HTML",
            )

            with patch(
                "check_sites.send_message",
                side_effect=[None, RuntimeError("Telegram unavailable")],
            ):
                with self.assertRaisesRegex(RuntimeError, "Telegram unavailable"):
                    _deliver_telegram_outbox(outbox)

            messages, parse_mode = _load_telegram_outbox(outbox)
            self.assertEqual(messages, ["second", "third"])
            self.assertEqual(parse_mode, "HTML")

            with patch("check_sites.send_message") as send:
                self.assertTrue(_deliver_telegram_outbox(outbox))

            self.assertFalse(outbox.exists())
            self.assertEqual(
                [item.args[0] for item in send.call_args_list],
                ["second", "third"],
            )
            self.assertTrue(
                all(item.kwargs["parse_mode"] == "HTML" for item in send.call_args_list)
            )

    def test_missing_outbox_has_nothing_to_deliver(self):
        with TemporaryDirectory() as directory:
            outbox = Path(directory) / "pending_telegram.json"
            with patch("check_sites.send_message") as send:
                self.assertFalse(_deliver_telegram_outbox(outbox))
            send.assert_not_called()


class OpportunityClassificationTests(unittest.TestCase):
    def test_same_url_and_title_stays_old_when_description_changes(self):
        previous = """Название: Напарники — сезон 2026
Дедлайн: 20 сентября 2026
Описание: Старое описание.
URL: https://prodaman.ru/prodaman/contests/Naparniki"""
        current = """Название: Напарники — сезон 2026
Дедлайн: 20 сентября 2026
Описание: Новая формулировка того же конкурса.
URL: https://prodaman.ru/prodaman/contests/Naparniki"""

        new, old = _partition_opportunities(previous, current)

        self.assertEqual(new, [])
        self.assertEqual(len(old), 1)

    def test_new_season_on_same_url_is_new(self):
        previous = """Название: Некроманты — 4 сезон
Дедлайн: не указан
Описание: Для авторов фэнтези.
URL: https://prodaman.ru/contests/necromancers"""
        current = """Название: Некроманты — 5 сезон
Дедлайн: не указан
Описание: Для авторов фэнтези.
URL: https://prodaman.ru/contests/necromancers"""

        new, old = _partition_opportunities(previous, current)

        self.assertEqual(len(new), 1)
        self.assertEqual(old, [])

    def test_one_platform_can_have_new_and_old_opportunities(self):
        previous = """Название: Старый конкурс
Дедлайн: не указан
Описание: Старый.
URL: https://example.com/old"""
        current = previous + """

Название: Новый конкурс
Дедлайн: не указан
Описание: Новый.
URL: https://example.com/new"""

        new, old = _partition_opportunities(previous, current)

        self.assertEqual(len(new), 1)
        self.assertEqual(len(old), 1)


class TelegramReportTests(unittest.TestCase):
    def test_stats_include_date_and_time(self):
        message = _stats_message(
            datetime(2026, 9, 13, 18, 7),
            checked=85,
            ai_checked=3,
            unchanged=80,
            unclear=2,
        )
        self.assertTrue(message.startswith("Литературный монитор: 2026-09-13 18:07"))

    def test_section_titles_are_bold(self):
        message = _change_message("Новости по опен-коллам", ["new"], ["old"])
        self.assertIn("<b>Новости по опен-коллам</b>", message)
        self.assertIn("<b>Новые:</b>", message)
        self.assertIn("<b>Старые:</b>", message)

    def test_unclear_platforms_include_telegram_from_column_e(self):
        rows = [
            SimpleNamespace(
                name="Платформа & журнал",
                url="https://example.com",
                telegram_url="https://t.me/example",
            ),
            SimpleNamespace(
                name="Без канала",
                url="https://without.example",
                telegram_url="",
            ),
        ]

        message = _unclear_message(rows)

        self.assertIn("<b>Платформа:</b> Платформа &amp; журнал", message)
        self.assertIn("<b>Телеграм:</b> https://t.me/example", message)
        self.assertIn("<b>Платформа:</b> Без канала", message)
        self.assertIn("<b>Телеграм:</b> не указан", message)


if __name__ == "__main__":
    unittest.main()
