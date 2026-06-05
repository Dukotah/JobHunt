"""Job scraper — pulls listings from multiple free sources, no API keys needed.

Sources:
  - Greenhouse ATS   (public JSON API — 80+ remote-friendly companies)
  - Lever ATS        (public JSON API — 60+ remote-friendly companies)
  - Remote OK        (public JSON API, ~300 listings w/ salary)
  - Arbeitnow        (public JSON API, remote-filtered)
  - The Muse         (public JSON API, large US company coverage)
  - Himalayas        (RSS, remote-only curated board)
  - Remotive         (RSS, large remote board)
  - We Work Remotely (RSS, lxml fallback for malformed XML)
  - Jobicy           (RSS, remote-only)
  - Working Nomads   (RSS, curated remote listings)
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
# Greenhouse — public API, no auth
# boards-api.greenhouse.io/v1/boards/{slug}/jobs
# ---------------------------------------------------------------------------

GREENHOUSE_COMPANIES = [
    # Dev tools / infra
    "vercel", "supabase", "planetscale", "render", "railway", "fly",
    "netlify", "cloudflare", "fastly", "elastic", "mongodb", "redis",
    "hashicorp", "datadog", "newrelic", "pagerduty", "segment",
    "mixpanel", "amplitude", "heap", "fullstory",
    # SaaS / product
    "notion", "airtable", "loom", "linear", "figma", "miro",
    "zapier", "monday", "intercom", "zendesk", "freshworks",
    "hubspot", "drift", "outreach", "salesloft", "gong",
    "brex", "ramp", "rippling", "gusto", "lattice", "culture-amp",
    "greenhouse", "lever", "workday",
    # Media / content / writing
    "substack", "medium", "buzzfeed", "vox", "axios",
    # E-commerce / marketplace
    "shopify", "gumroad", "stripe", "square", "braintree",
    "affirm", "klarna", "marqeta",
    # Health / edtech
    "calm", "headspace", "noom", "hims", "ro",
    "duolingo", "coursera", "udemy", "masterclass",
    # Remote-first companies
    "gitlab", "automattic", "invision", "doist", "basecamp",
    "buffer", "helpscout", "hotjar", "close", "convertkit",
]

# ---------------------------------------------------------------------------
# Lever — public API, no auth
# api.lever.co/v0/postings/{slug}?mode=json
# ---------------------------------------------------------------------------

LEVER_COMPANIES = [
    # Dev tools / infra
    "anthropic", "openai", "scale", "weights-biases", "huggingface",
    "replit", "codeium", "cursor", "anyscale", "modal",
    "turso", "neon", "xata", "upstash",
    # SaaS
    "asana", "carta", "doordash", "lyft", "robinhood",
    "plaid", "chime", "coinbase", "gemini", "alchemy",
    "postman", "retool", "airplane", "superblocks", "tooljet",
    "clerk", "auth0", "stytch", "workos",
    # Marketing / content
    "semrush", "ahrefs", "clearscope", "marketmuse", "jasper",
    "copy-ai", "writesonic",
    # Remote-first
    "remote", "deel", "oyster", "papaya-global", "justworks",
    "toptal", "andela", "turing",
    # Agency / services
    "thoughtworks", "slalom", "ideo",
    # Media
    "buzzsprout", "anchor", "transistor",
]

RSS_FEEDS = {
    "remotive":      "https://remotive.com/remote-jobs/feed/",
    "himalayas":     "https://himalayas.app/jobs/rss",
    "jobicy":        "https://jobicy.com/?feed=job_feed",
    "workingnomads": "https://www.workingnomads.com/feed",
    "wwr":           "https://weworkremotely.com/remote-jobs.rss",
}

_REMOTE_RE = re.compile(r"\bremote\b|\banywhere\b|\bwfh\b", re.IGNORECASE)
_US_RE = re.compile(r"\busa?\b|\bunited states\b|\bu\.s\.\b|\bnorth america\b", re.IGNORECASE)
_SALARY_RE = re.compile(
    r"\$[\d,]+(?:k)?(?:\s*[-–]\s*\$[\d,]+(?:k)?)?(?:\s*/\s*(?:yr|year|mo|month|hr|hour))?",
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
        logger.warning("Fetch failed %s: %s", url, exc)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("JSON error %s: %s", url, exc)
        return None


def _extract_salary(text: str) -> str:
    m = _SALARY_RE.search(text)
    return m.group(0) if m else ""


def _is_remote(location: str, description: str = "") -> bool:
    return bool(_REMOTE_RE.search(location) or _REMOTE_RE.search(description[:500]))


def _job(title, company, source, url, description="", salary="", location="") -> dict:
    return {
        "title": _clean(title),
        "company": _clean(company),
        "source": source,
        "url": url,
        "salary": salary or _extract_salary(f"{title} {description}"),
        "location": _clean(location),
        "description": _clean(description)[:3000],
        "score": None,
        "claude_compatibility": None,
        "category": None,
        "reason": None,
        "date_found": _today(),
        "status": "new",
    }


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------

def _scrape_greenhouse(slug: str) -> Iterator[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    data = _get(url, as_json=True)
    if not data:
        return
    for item in data.get("jobs", []):
        location = item.get("location", {}).get("name", "")
        if not _is_remote(location, item.get("content", "") or ""):
            continue
        job_url = item.get("absolute_url", "")
        title = item.get("title", "")
        description = _clean(item.get("content", "") or "")
        if not title or not job_url:
            continue
        yield _job(title, slug.replace("-", " ").title(), f"greenhouse/{slug}", job_url, description, "", location)


def _scrape_all_greenhouse() -> Iterator[dict]:
    for slug in GREENHOUSE_COMPANIES:
        try:
            yield from _scrape_greenhouse(slug)
        except Exception as exc:
            logger.warning("Greenhouse %s: %s", slug, exc)


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------

def _scrape_lever(slug: str) -> Iterator[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _get(url, as_json=True)
    if not data or not isinstance(data, list):
        return
    for item in data:
        categories = item.get("categories", {})
        location = categories.get("location", "") or item.get("workplaceType", "")
        commitment = categories.get("commitment", "")
        # Skip clearly non-remote unless it says remote
        if not _is_remote(location, commitment):
            # Check the text field for "remote"
            text = item.get("text", "") or ""
            if not _is_remote(text, ""):
                continue
        job_url = item.get("hostedUrl", "")
        title = item.get("text", "")
        description = _clean(
            (item.get("description", "") or "") +
            " ".join(s.get("content", "") for s in item.get("lists", []))
        )
        company_name = slug.replace("-", " ").title()
        if not title or not job_url:
            continue
        yield _job(title, company_name, f"lever/{slug}", job_url, description, "", location)


def _scrape_all_lever() -> Iterator[dict]:
    for slug in LEVER_COMPANIES:
        try:
            yield from _scrape_lever(slug)
        except Exception as exc:
            logger.warning("Lever %s: %s", slug, exc)


# ---------------------------------------------------------------------------
# Remote OK
# ---------------------------------------------------------------------------

def _scrape_remoteok() -> Iterator[dict]:
    data = _get("https://remoteok.com/api", as_json=True)
    if not data:
        return
    for item in data[1:]:
        if not isinstance(item, dict):
            continue
        url = item.get("url", "")
        if not url.startswith("http"):
            url = "https://remoteok.com" + url
        title = item.get("position", "")
        company = item.get("company", "")
        description = item.get("description", "") or ""
        location = item.get("location", "") or "Remote"
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
# Arbeitnow
# ---------------------------------------------------------------------------

def _scrape_arbeitnow() -> Iterator[dict]:
    for page in range(1, 4):
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
            location = item.get("location", "") or "Remote"
            if not title or not job_url:
                continue
            yield _job(title, company, "arbeitnow", job_url, description, _extract_salary(description), location)


# ---------------------------------------------------------------------------
# The Muse
# ---------------------------------------------------------------------------

def _scrape_themuse() -> Iterator[dict]:
    for page in range(1, 6):
        data = _get(
            f"https://www.themuse.com/api/public/jobs?page={page}&descending=true",
            as_json=True,
        )
        if not data:
            break
        results = data.get("results", [])
        if not results:
            break
        for item in results:
            locations = item.get("locations", [])
            loc_names = " | ".join(l.get("name", "") for l in locations)
            if not _is_remote(loc_names) and not _US_RE.search(loc_names):
                continue
            job_url = item.get("refs", {}).get("landing_page", "")
            title = item.get("name", "")
            company = item.get("company", {}).get("name", "")
            description = _clean(item.get("contents", "") or "")
            if not title or not job_url:
                continue
            yield _job(title, company, "themuse", job_url, description, "", loc_names)


# ---------------------------------------------------------------------------
# RSS feeds (shared parser w/ lxml fallback)
# ---------------------------------------------------------------------------

def _scrape_rss(source_name: str, feed_url: str) -> Iterator[dict]:
    resp = _get(feed_url)
    if resp is None:
        return

    root = None
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        try:
            soup = BeautifulSoup(resp.content, "lxml-xml")
            root = ET.fromstring(str(soup).encode())
        except Exception as exc:
            logger.warning("RSS parse failed %s: %s", feed_url, exc)
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

        company = ""
        if source_name == "wwr" and ": " in title:
            company, title = title.split(": ", 1)

        if not title or not url:
            continue
        yield _job(title, company, source_name, url, description)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape_all() -> list[dict]:
    jobs: list[dict] = []

    logger.info("ATS: Greenhouse (%d companies)", len(GREENHOUSE_COMPANIES))
    batch = list(_scrape_all_greenhouse())
    logger.info("  → %d remote listings", len(batch))
    jobs.extend(batch)

    logger.info("ATS: Lever (%d companies)", len(LEVER_COMPANIES))
    batch = list(_scrape_all_lever())
    logger.info("  → %d remote listings", len(batch))
    jobs.extend(batch)

    logger.info("API: Remote OK")
    batch = list(_scrape_remoteok())
    logger.info("  → %d listings", len(batch))
    jobs.extend(batch)

    logger.info("API: Arbeitnow (3 pages)")
    batch = list(_scrape_arbeitnow())
    logger.info("  → %d listings", len(batch))
    jobs.extend(batch)

    logger.info("API: The Muse (5 pages)")
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
