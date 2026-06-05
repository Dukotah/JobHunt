"""Job scraper — pulls listings from sources with reliable public APIs/RSS.

Sources:
  - Remote OK       (JSON API, no auth, ~300 listings w/ salary)
  - Arbeitnow       (JSON API, no auth, remote-filtered)
  - The Muse        (JSON API, no auth, large US company coverage)
  - Himalayas       (RSS, remote-only curated board)
  - Jobicy          (RSS, remote-only)
  - Working Nomads  (RSS, curated remote listings)
  - Remotive        (RSS, large remote board)
  - We Work Remotely (RSS, uses lxml for malformed XML tolerance)
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

RSS_FEEDS = {
    "remotive":      "https://remotive.com/remote-jobs/feed/",
    "himalayas":     "https://himalayas.app/jobs/rss",
    "jobicy":        "https://jobicy.com/?feed=job_feed",
    "workingnomads": "https://www.workingnomads.com/feed",
    "wwr":           "https://weworkremotely.com/remote-jobs.rss",
}

# US location keywords — used for soft US filtering
_US_RE = re.compile(
    r"\busa?\b|united states|u\.s\.|north america|americas?\b",
    re.IGNORECASE,
)


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


def _job(title, company, source, url, description="", salary="", location="") -> dict:
    combined = f"{title} {description} {salary}"
    return {
        "title": _clean(title),
        "company": _clean(company),
        "source": source,
        "url": url,
        "salary": salary or _extract_salary(combined),
        "description": _clean(description)[:3000],
        "location": _clean(location),
        "score": None,
        "claude_compatibility": None,
        "category": None,
        "reason": None,
        "date_found": _today(),
        "status": "new",
    }


# ---------------------------------------------------------------------------
# Remote OK  — JSON API
# ---------------------------------------------------------------------------

def _scrape_remoteok() -> Iterator[dict]:
    data = _get("https://remoteok.com/api", as_json=True)
    if not data:
        return
    for item in data[1:]:  # first element is a notice object
        if not isinstance(item, dict):
            continue
        url = item.get("url", "")
        if not url.startswith("http"):
            url = "https://remoteok.com" + url
        title = item.get("position", "")
        company = item.get("company", "")
        description = item.get("description", "") or ""
        location = item.get("location", "") or ""
        salary = ""
        lo, hi = item.get("salary_min"), item.get("salary_max")
        if lo and hi:
            salary = f"${int(lo):,}–${int(hi):,}/yr"
        elif lo:
            salary = f"${int(lo):,}/yr"
        if not title or not url:
            continue
        yield _job(title, company, "remoteok", url, description, salary, location)


# ---------------------------------------------------------------------------
# Arbeitnow  — JSON API, remote-filtered
# ---------------------------------------------------------------------------

def _scrape_arbeitnow() -> Iterator[dict]:
    for page in range(1, 4):   # pages 1–3 ≈ 300 listings
        data = _get(f"https://arbeitnow.com/api/job-board-api?page={page}", as_json=True)
        if not data:
            break
        items = data.get("data", [])
        if not items:
            break
        for item in items:
            if not item.get("remote", False):
                continue
            job_url = item.get("url", "")
            title = item.get("title", "")
            company = item.get("company_name", "")
            description = item.get("description", "") or ""
            location = item.get("location", "") or ""
            salary = _extract_salary(description)
            if not title or not job_url:
                continue
            yield _job(title, company, "arbeitnow", job_url, description, salary, location)


# ---------------------------------------------------------------------------
# The Muse  — JSON API, no auth, massive US company coverage
# ---------------------------------------------------------------------------

def _scrape_themuse() -> Iterator[dict]:
    for page in range(1, 6):   # pages 1–5 ≈ 500 listings
        data = _get(
            f"https://www.themuse.com/api/public/jobs?page={page}&level=Senior+Level"
            f"&level=Mid+Level&level=Entry+Level&descending=true",
            as_json=True,
        )
        if not data:
            break
        results = data.get("results", [])
        if not results:
            break
        for item in results:
            locations = item.get("locations", [])
            # Include remote or US-based
            loc_names = " ".join(l.get("name", "") for l in locations)
            is_remote = any("remote" in l.get("name", "").lower() for l in locations)
            is_us = bool(_US_RE.search(loc_names)) or is_remote
            if not is_us:
                continue
            job_url = item.get("refs", {}).get("landing_page", "")
            title = item.get("name", "")
            company = item.get("company", {}).get("name", "")
            description = _clean(item.get("contents", "") or "")
            if not title or not job_url:
                continue
            yield _job(title, company, "themuse", job_url, description, "", loc_names)


# ---------------------------------------------------------------------------
# RSS feeds  — shared parser with lxml fallback for malformed XML
# ---------------------------------------------------------------------------

def _scrape_rss(source_name: str, feed_url: str) -> Iterator[dict]:
    resp = _get(feed_url)
    if resp is None:
        return

    # Try strict stdlib parser first, fall back to lxml's permissive HTML parser
    root = None
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        try:
            soup = BeautifulSoup(resp.content, "lxml-xml")
            # Re-serialise through lxml then re-parse — gives us a clean tree
            root = ET.fromstring(str(soup).encode())
        except Exception as exc:
            logger.warning("RSS parse failed for %s: %s", feed_url, exc)
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

        # WWR encodes "Company: Title"
        company = ""
        if source_name == "wwr" and ": " in title:
            company, title = title.split(": ", 1)

        if not title or not url:
            continue
        yield _job(title, company, source_name, url, description)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_all() -> list[dict]:
    jobs: list[dict] = []

    logger.info("API: Remote OK")
    batch = list(_scrape_remoteok())
    logger.info("  → %d listings", len(batch))
    jobs.extend(batch)

    logger.info("API: Arbeitnow (3 pages)")
    batch = list(_scrape_arbeitnow())
    logger.info("  → %d listings", len(batch))
    jobs.extend(batch)

    logger.info("API: The Muse (5 pages, US/remote)")
    batch = list(_scrape_themuse())
    logger.info("  → %d listings", len(batch))
    jobs.extend(batch)

    for source, url in RSS_FEEDS.items():
        logger.info("RSS: %s", source)
        batch = list(_scrape_rss(source, url))
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
