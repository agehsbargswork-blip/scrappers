"""Conservative website reader: linked page plus relevant same-domain pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup


KEYWORDS = re.compile(
    r"submit|submission|open.?call|award|prize|contest|competition|rules|news|"
    r"при[её]м|подать|публикац|конкурс|преми|награ|опен.?колл|новост",
    re.IGNORECASE,
)
MAX_PAGES = 5
MAX_CHARS_PER_PAGE = 14_000


@dataclass(frozen=True)
class PageEvidence:
    url: str
    title: str
    text: str


@dataclass(frozen=True)
class SiteEvidence:
    requested_url: str
    pages: list[PageEvidence]
    error: str | None = None


def _same_site(left: str, right: str) -> bool:
    return urlparse(left).netloc.removeprefix("www.") == urlparse(right).netloc.removeprefix("www.")


def _fetch(url: str) -> tuple[PageEvidence, list[str]]:
    response = requests.get(
        url,
        timeout=(8, 20),
        allow_redirects=True,
        headers={"User-Agent": "LiterOpportunityMonitor/1.0 (+https://github.com/agehsbargswork-blip/scrappers)"},
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type.lower():
        raise ValueError(f"Unsupported content type: {content_type}")

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else response.url
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        label = f"{anchor.get_text(' ', strip=True)} {anchor['href']}"
        if not KEYWORDS.search(label):
            continue
        candidate = urldefrag(urljoin(response.url, anchor["href"]))[0]
        if candidate.startswith(("http://", "https://")) and _same_site(response.url, candidate):
            links.append(candidate)

    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    return PageEvidence(response.url, title, text[:MAX_CHARS_PER_PAGE]), links


def collect_site_evidence(url: str) -> SiteEvidence:
    try:
        first_page, candidates = _fetch(url)
    except Exception as exc:
        return SiteEvidence(url, [], f"{type(exc).__name__}: {exc}")

    pages = [first_page]
    visited = {first_page.url}
    for candidate in dict.fromkeys(candidates):
        if len(pages) >= MAX_PAGES:
            break
        if candidate in visited:
            continue
        visited.add(candidate)
        try:
            page, _ = _fetch(candidate)
        except Exception:
            continue
        if page.text:
            pages.append(page)
    return SiteEvidence(url, pages)
