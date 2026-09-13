"""Regression tests for hashing and Telegram new/old classification."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


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

from check_sites import _evidence_hash, _partition_opportunities  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
