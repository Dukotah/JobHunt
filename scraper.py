"""Job scraper — pulls listings from sources with reliable public APIs/RSS.

Sources:
  - Remote OK  (JSON API, no auth)
  - Arbeitnow  (JSON API, no auth)
  - Jobicy     (RSS feed)
  - Working Nomads (RSS feed)
"""

import re
import json
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
    "Accept": "application/json, application/rss+xml, application/xml, text/xml, */*",
}

REQUEST_TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get(url: str, as_json: bool = False):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json() if as_json else resp
    except requests.RequestException as exc:
        logger.warning("Fetch failed for %s: %s", url, exc)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse error for %s: %s", url, exc)
        return None


_SALARY_RE = re.compile(
    r"\$[\d,]+(?:k)?(?:\s*[-–]\s*\$[\d,]+(?:k)?)?(?:\s*/\s*(?:yr|year|mo|month|hr|hour))?",
    re.IGNORECASE,
)


def _extract_salary(text: str) -> str:
    m = _SALARY_RE.search(text)
    return m.group(0) if m else ""


def _job(title, company, source, url, description="", salary="") -> dict:
    combined = f"{title} {description} {salary}"
    return {
        "title": _clean(title),
        "company": _clean(company),
        "source": source,
        "url": url,
        "salary": salary or _extract_salary(combined),
        "description": _clean(description)[:3000],
        "score": None,
        "claude_compatibility": None,
        "category": None,
        "reason": None,
        "date_found": _today(),
        "status": "new",
    }


# ---------------------------------------------------------------------------
# Remote OK  — JSON API, no auth, ~300 listings
# ---------------------------------------------------------------------------

def _scrape_remoteok() -> Iterator[dict]:
    data = _get("https://remoteok.com/api", as_json=True)
    if not data:
        return
    # First element is a notice object, skip it
    for item in data[1:]:
        if not isinstance(item, dict):
            continue
        url = item.get("url", "")
        if not url.startswith("http"):
            url = "https://remoteok.com" + url
        title = item.get("position", "")
        company = item.get("company", "")
        description = item.get("description", "") or ""
        salary = ""
        lo = item.get("salary_min")
        hi = item.get("salary_max")
        if lo and hi:
            salary = f"${int(lo):,}–${int(hi):,}/yr"
        elif lo:
            salary = f"${int(lo):,}/yr"
        if not title or not url:
            continue
        yield _job(title, company, "remoteok", url, description, salary)


# ---------------------------------------------------------------------------
# Arbeitnow  — JSON API, no auth, remote+English filter
# ---------------------------------------------------------------------------

def _scrape_arbeitnow() -> Iterator[dict]:
    url = "https://arbeitnow.com/api/job-board-api"
    data = _get(url, as_json=True)
    if not data:
        return
    for item in data.get("data", []):
        if not item.get("remote", False):
            continue
        job_url = item.get("url", "")
        title = item.get("title", "")
        company = item.get("company_name", "")
        description = item.get("description", "") or ""
        salary = ""
        # Arbeitnow sometimes includes salary in description
        sal_match = _extract_salary(description)
        if sal_match:
            salary = sal_match
        if not title or not job_url:
            continue
        yield _job(title, company, "arbeitnow", job_url, description, salary)


# ---------------------------------------------------------------------------
# Jobicy  — RSS feed
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

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)

    for item in items:
        def _t(tag):
            el = item.find(tag) or item.find(f"atom:{tag}", ns)
            return el.text if el is not None and el.text else ""

        title = _clean(_t("title"))
        url = _clean(_t("link") or _t("guid"))
        description = _clean(_t("description") or _t("summary"))
        if not title or not url:
            continue
        yield _job(title, "", source_name, url, description)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_all() -> list[dict]:
    jobs: list[dict] = []

    logger.info("API: Remote OK")
    batch = list(_scrape_remoteok())
    logger.info("  → %d listings", len(batch))
    jobs.extend(batch)

    logger.info("API: Arbeitnow")
    batch = list(_scrape_arbeitnow())
    logger.info("  → %d listings", len(batch))
    jobs.extend(batch)

    logger.info("RSS: Jobicy")
    batch = list(_scrape_rss("jobicy", "https://jobicy.com/?feed=job_feed"))
    logger.info("  → %d listings", len(batch))
    jobs.extend(batch)

    logger.info("RSS: Working Nomads")
    batch = list(_scrape_rss("workingnomads", "https://www.workingnomads.com/feed"))
    logger.info("  → %d listings", len(batch))
    jobs.extend(batch)

    # Deduplicate by URL
    seen: set[str] = set()
    unique: list[dict] = []
    for job in jobs:
        if job["url"] not in seen:
            seen.add(job["url"])
            unique.append(job)

    logger.info("Total unique listings: %d", len(unique))
    return unique
