"""Job scraper — pulls listings from RSS feeds and HTML pages.

Uses stdlib xml.etree + requests. No feedparser dependency.
"""

import re
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Iterator

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

REQUEST_TIMEOUT = 10  # seconds

RSS_SOURCES = {
    "weworkremotely": [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-copywriting-jobs.rss",
        "https://weworkremotely.com/categories/remote-marketing-and-sales-jobs.rss",
        "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
        "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss",
        "https://weworkremotely.com/categories/remote-product-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "https://weworkremotely.com/categories/remote-design-jobs.rss",
    ],
    "remotive": [
        "https://remotive.com/remote-jobs/feed/",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get(url: str) -> requests.Response | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        logger.warning("Fetch failed for %s: %s", url, exc)
        return None


_SALARY_RE = re.compile(
    r"\$[\d,]+(?:k)?(?:\s*[-–]\s*\$[\d,]+(?:k)?)?(?:\s*/\s*(?:yr|year|mo|month|hr|hour))?",
    re.IGNORECASE,
)


def _extract_salary(text: str) -> str:
    m = _SALARY_RE.search(text)
    return m.group(0) if m else ""


def _job_skeleton(title, company, source, url, description="") -> dict:
    return {
        "title": title,
        "company": company,
        "source": source,
        "url": url,
        "salary": _extract_salary(title + " " + description),
        "description": description[:3000],
        "score": None,
        "claude_compatibility": None,
        "category": None,
        "reason": None,
        "date_found": _today(),
        "status": "new",
    }


# ---------------------------------------------------------------------------
# RSS scraper (stdlib XML)
# ---------------------------------------------------------------------------

def _scrape_rss(source_name: str, feed_url: str) -> Iterator[dict]:
    resp = _get(feed_url)
    if resp is None:
        return

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        logger.warning("XML parse error for %s: %s", feed_url, exc)
        return

    # Handle both RSS <item> and Atom <entry>
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)

    for item in items:
        def _t(tag):
            el = item.find(tag) or item.find(f"atom:{tag}", ns)
            return el.text if el is not None and el.text else ""

        title = _clean(_t("title"))
        url = _clean(_t("link") or _t("guid"))
        if not title or not url:
            continue

        company = ""
        if source_name == "weworkremotely" and ": " in title:
            company, title = title.split(": ", 1)

        description = _clean(_t("description") or _t("summary") or _t("content"))

        yield _job_skeleton(title, company, source_name, url, description)


# ---------------------------------------------------------------------------
# HTML scrapers
# ---------------------------------------------------------------------------

def _scrape_remoteco() -> Iterator[dict]:
    categories = [
        "writing", "developer", "project-manager",
        "virtual-assistant", "data-entry", "analyst",
    ]
    for cat in categories:
        url = f"https://remote.co/remote-jobs/{cat}/"
        resp = _get(url)
        if resp is None:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        for card in soup.select("a.card, .job_listing a"):
            href = card.get("href", "")
            if not href.startswith("http"):
                href = "https://remote.co" + href

            title_el = card.select_one(".position_title, h2, h3, .job-title")
            company_el = card.select_one(".company_name, .company, .company-name")
            title = _clean(title_el.text) if title_el else _clean(card.get("aria-label", ""))
            company = _clean(company_el.text) if company_el else ""

            if not title or not href:
                continue
            yield _job_skeleton(title, company, "remoteco", href)


def _scrape_jobspresso() -> Iterator[dict]:
    resp = _get("https://jobspresso.co/remote-work/")
    if resp is None:
        return

    soup = BeautifulSoup(resp.text, "lxml")
    for card in soup.select("li.job_listing"):
        link_el = card.select_one("a")
        href = link_el.get("href", "") if link_el else ""
        title_el = card.select_one(".position h3, h3, .job-title")
        company_el = card.select_one(".company strong, .company")
        title = _clean(title_el.text) if title_el else ""
        company = _clean(company_el.text) if company_el else ""
        if not title or not href:
            continue
        yield _job_skeleton(title, company, "jobspresso", href)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_all() -> list[dict]:
    jobs: list[dict] = []

    for source, urls in RSS_SOURCES.items():
        for feed_url in urls:
            logger.info("RSS: %s", feed_url)
            batch = list(_scrape_rss(source, feed_url))
            logger.info("  → %d listings", len(batch))
            jobs.extend(batch)

    logger.info("HTML: remote.co")
    batch = list(_scrape_remoteco())
    logger.info("  → %d listings", len(batch))
    jobs.extend(batch)

    logger.info("HTML: Jobspresso")
    batch = list(_scrape_jobspresso())
    logger.info("  → %d listings", len(batch))
    jobs.extend(batch)

    # Deduplicate by URL within this batch
    seen: set[str] = set()
    unique: list[dict] = []
    for job in jobs:
        if job["url"] not in seen:
            seen.add(job["url"])
            unique.append(job)

    logger.info("Total unique listings: %d", len(unique))
    return unique
