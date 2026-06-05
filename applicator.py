#!/usr/bin/env python3
"""
Auto-applicator — uses Playwright to fill and submit job applications.

Targets (in order of reliability):
  1. Greenhouse ATS  — standardized forms, no CAPTCHA
  2. Lever ATS       — standardized forms, no CAPTCHA
  3. Indeed Easy Apply — works when already logged in via browser profile
  4. Workable        — standardized forms

Usage:
  python applicator.py              # apply to top unapplied jobs
  python applicator.py --limit 20   # apply to up to 20 jobs
  python applicator.py --min-compat 70  # only jobs with compat >= 70%
  python applicator.py --dry-run    # open forms but don't submit
  python applicator.py --headless   # run without visible browser
"""

import argparse
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

import db
import tailor as tailor_mod

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

PROFILE_PATH = Path(__file__).parent / "profile.json"


def load_profile() -> dict:
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(
            f"profile.json not found. Copy profile.json and fill in your details."
        )
    return json.loads(PROFILE_PATH.read_text())


# ---------------------------------------------------------------------------
# Form-type detection
# ---------------------------------------------------------------------------

def detect_form_type(url: str) -> str:
    """Return the form type based on the URL."""
    hostname = urlparse(url).hostname or ""
    if "greenhouse.io" in hostname or "boards.greenhouse.io" in hostname:
        return "greenhouse"
    if "lever.co" in hostname:
        return "lever"
    if "indeed.com" in hostname:
        return "indeed"
    if "workable.com" in hostname or "apply.workable.com" in hostname:
        return "workable"
    if "ashbyhq.com" in hostname or "jobs.ashbyhq.com" in hostname:
        return "ashby"
    return "unknown"


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

def _fill(page: Page, selector: str, value: str, timeout: int = 3000) -> bool:
    """Fill a field if it exists. Returns True on success."""
    try:
        el = page.wait_for_selector(selector, timeout=timeout)
        if el:
            el.fill(value)
            return True
    except PWTimeout:
        pass
    return False


def _select(page: Page, selector: str, value: str, timeout: int = 3000) -> bool:
    try:
        el = page.wait_for_selector(selector, timeout=timeout)
        if el:
            el.select_option(label=value)
            return True
    except (PWTimeout, Exception):
        pass
    return False


def _click(page: Page, selector: str, timeout: int = 5000) -> bool:
    try:
        el = page.wait_for_selector(selector, timeout=timeout)
        if el:
            el.click()
            return True
    except PWTimeout:
        pass
    return False


def _upload(page: Page, selector: str, file_path: str, timeout: int = 3000) -> bool:
    try:
        el = page.wait_for_selector(selector, timeout=timeout)
        if el:
            el.set_input_files(file_path)
            return True
    except (PWTimeout, Exception):
        pass
    return False


def _human_delay(lo: float = 0.5, hi: float = 1.5):
    time.sleep(lo + (hi - lo) * __import__("random").random())


def _build_cover_letter(profile: dict, job: dict) -> str:
    # Use pre-generated tailored cover letter if available
    if profile.get("_cover_letter"):
        return profile["_cover_letter"]
    template = profile.get("cover_letter_template", "")
    return template.format(
        title=job.get("title", "this role"),
        company=job.get("company", "your company"),
    )


# ---------------------------------------------------------------------------
# Greenhouse applicator
# ---------------------------------------------------------------------------

def apply_greenhouse(page: Page, job: dict, profile: dict, dry_run: bool) -> str:
    """Fill a Greenhouse application. Returns 'applied', 'needs_review', or 'failed'."""
    page.goto(job["url"], wait_until="networkidle", timeout=15000)

    resume = profile.get("resume_path", "")

    # Basic fields
    _fill(page, "input#first_name", profile["first_name"])
    _fill(page, "input#last_name", profile["last_name"])
    _fill(page, "input#email", profile["email"])
    _fill(page, "input#phone", profile["phone"])

    # Location
    _fill(page, "input#job_application_location", profile["location"])

    # LinkedIn / website
    _fill(page, "input[name*='linkedin']", profile.get("linkedin", ""))
    _fill(page, "input[name*='website']", profile.get("website", ""))

    # Resume upload
    if resume:
        uploaded = _upload(page, "input[type='file']", resume)
        if not uploaded:
            logger.warning("  Resume upload failed for %s", job["url"])

    # Cover letter textarea
    cover = _build_cover_letter(profile, job)
    _fill(page, "textarea[name*='cover']", cover, timeout=2000)

    # Standard dropdowns — work authorization
    _select(page, "select[name*='authoriz']", "Yes", timeout=2000)
    _select(page, "select[name*='sponsor']", "No", timeout=2000)

    # Short text questions — fill generic answers
    for label_el in page.query_selector_all("label"):
        label_text = (label_el.inner_text() or "").lower()
        input_id = label_el.get_attribute("for")
        if not input_id:
            continue
        input_el = page.query_selector(f"#{input_id}")
        if not input_el:
            continue
        tag = input_el.evaluate("el => el.tagName").lower()
        if tag not in ("input", "textarea"):
            continue
        # Match common question patterns
        if "salary" in label_text or "compensation" in label_text:
            input_el.fill(profile["standard_answers"]["salary_expectation"])
        elif "start" in label_text and ("when" in label_text or "earliest" in label_text):
            input_el.fill(profile["standard_answers"]["earliest_start"])
        elif "why" in label_text and "interest" in label_text:
            input_el.fill(profile["short_answer_defaults"]["why_interested"])
        elif "strength" in label_text:
            input_el.fill(profile["short_answer_defaults"]["greatest_strength"])

    _human_delay()

    if dry_run:
        logger.info("  [DRY RUN] Would submit Greenhouse form for %s", job["title"])
        return "dry_run"

    submitted = _click(page, "input[type='submit'], button[type='submit']", timeout=5000)
    if not submitted:
        return "needs_review"

    page.wait_for_timeout(2000)
    # Success if URL changed or confirmation text present
    if "confirmation" in page.url or page.query_selector("text=Thank you"):
        return "applied"
    return "needs_review"


# ---------------------------------------------------------------------------
# Lever applicator
# ---------------------------------------------------------------------------

def apply_lever(page: Page, job: dict, profile: dict, dry_run: bool) -> str:
    page.goto(job["url"], wait_until="networkidle", timeout=15000)

    # Lever has an "Apply" button on the posting page
    _click(page, "a[href*='/apply'], button:has-text('Apply')", timeout=5000)
    page.wait_for_timeout(1500)

    _fill(page, "input[name='name']", f"{profile['first_name']} {profile['last_name']}")
    _fill(page, "input[name='email']", profile["email"])
    _fill(page, "input[name='phone']", profile["phone"])
    _fill(page, "input[name='urls[LinkedIn]']", profile.get("linkedin", ""))
    _fill(page, "input[name='urls[GitHub]']", profile.get("github", ""))
    _fill(page, "input[name='urls[Portfolio]']", profile.get("website", ""))

    cover = _build_cover_letter(profile, job)
    _fill(page, "textarea[name='comments']", cover)

    resume = profile.get("resume_path", "")
    if resume:
        _upload(page, "input[type='file']", resume)

    _human_delay()

    if dry_run:
        logger.info("  [DRY RUN] Would submit Lever form for %s", job["title"])
        return "dry_run"

    _click(page, "button[type='submit']:has-text('Submit'), button:has-text('Send Application')")
    page.wait_for_timeout(2000)

    if "thanks" in page.url.lower() or page.query_selector("text=Thank you"):
        return "applied"
    return "needs_review"


# ---------------------------------------------------------------------------
# Indeed Easy Apply
# ---------------------------------------------------------------------------

def apply_indeed(page: Page, job: dict, profile: dict, dry_run: bool) -> str:
    """
    Works when you are already logged into Indeed in the browser profile.
    Targets 'Easy Apply' jobs only — regular apply redirects externally.
    """
    page.goto(job["url"], wait_until="networkidle", timeout=15000)

    # Check for Easy Apply button
    easy_apply = page.query_selector("button:has-text('Apply now'), span:has-text('Easily apply')")
    if not easy_apply:
        return "needs_review"  # not an Easy Apply job

    easy_apply.click()
    page.wait_for_timeout(2000)

    # Indeed Easy Apply is a multi-step wizard
    # Step through up to 10 pages
    for step in range(10):
        _human_delay(1.0, 2.0)

        # Fill any visible fields
        _fill(page, "input[id*='applicant.name']", f"{profile['first_name']} {profile['last_name']}", timeout=1000)
        _fill(page, "input[id*='applicant.email']", profile["email"], timeout=1000)
        _fill(page, "input[id*='phoneNumber']", profile["phone"], timeout=1000)

        # Handle yes/no radio questions (work auth, etc.)
        for radio in page.query_selector_all("input[type='radio'][value='Yes'], input[type='radio'][value='yes']"):
            label = page.query_selector(f"label[for='{radio.get_attribute('id')}']")
            label_text = label.inner_text().lower() if label else ""
            if "authoriz" in label_text or "legally" in label_text:
                radio.click()

        # Check for "Continue", "Next", "Review", "Submit"
        next_btn = page.query_selector(
            "button:has-text('Continue'), button:has-text('Next'), "
            "button:has-text('Review your application'), button:has-text('Submit your application')"
        )

        if not next_btn:
            break

        btn_text = next_btn.inner_text().lower()

        if "submit" in btn_text:
            if dry_run:
                logger.info("  [DRY RUN] Would submit Indeed Easy Apply for %s", job["title"])
                return "dry_run"
            next_btn.click()
            page.wait_for_timeout(2000)
            return "applied"

        next_btn.click()

    return "needs_review"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

HANDLERS = {
    "greenhouse": apply_greenhouse,
    "lever":      apply_lever,
    "indeed":     apply_indeed,
}


def apply_to_job(page: Page, job: dict, profile: dict, dry_run: bool) -> str:
    form_type = detect_form_type(job["url"])
    handler = HANDLERS.get(form_type)
    if not handler:
        logger.info("  Skipping %s (unsupported: %s)", job["title"], form_type)
        return "skipped"

    logger.info(
        "[%s] %s @ %s  (compat=%.0f%%)",
        form_type.upper(), job["title"], job["company"], job.get("claude_compatibility") or 0
    )

    # Generate a tailored resume + cover letter for this specific job
    try:
        tailored_files = tailor_mod.tailor_job(job)
        # Override resume path with the tailored PDF
        profile = {**profile, "resume_path": tailored_files["resume_pdf"]}
        # Pre-fill cover letter with tailored version
        profile["_cover_letter"] = tailored_files["cover_text"]
    except Exception as exc:
        logger.warning("  Tailoring failed, using default resume: %s", exc)

    try:
        result = handler(page, job, profile, dry_run)
    except Exception as exc:
        logger.error("  Error: %s", exc)
        result = "failed"

    logger.info("  → %s", result)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Auto-apply to remote jobs")
    parser.add_argument("--limit",      type=int,   default=30,   help="Max applications per run (default 30)")
    parser.add_argument("--min-compat", type=float, default=60.0, help="Min Claude compatibility %% (default 60)")
    parser.add_argument("--dry-run",    action="store_true",       help="Fill forms but don't submit")
    parser.add_argument("--headless",   action="store_true",       help="Run browser in headless mode")
    parser.add_argument("--source",     default=None,              help="Filter by source (e.g. greenhouse/notion)")
    args = parser.parse_args()

    profile = load_profile()
    db.init_db()

    # Fetch unapplied high-scoring jobs
    with db.get_conn() as conn:
        query = """
            SELECT * FROM jobs
            WHERE status NOT IN ('applied', 'skipped')
            AND claude_compatibility >= ?
            AND score IS NOT NULL
            ORDER BY claude_compatibility DESC, score DESC
            LIMIT ?
        """
        rows = conn.execute(query, (args.min_compat, args.limit)).fetchall()

    jobs = [dict(r) for r in rows]
    if args.source:
        jobs = [j for j in jobs if j.get("source", "").startswith(args.source)]

    if not jobs:
        logger.info("No eligible jobs found. Run 'python main.py run' first to populate the database.")
        return

    logger.info("Found %d jobs to apply to (min compat: %.0f%%)", len(jobs), args.min_compat)

    results = {"applied": 0, "needs_review": 0, "skipped": 0, "failed": 0, "dry_run": 0}

    with sync_playwright() as pw:
        # Launch with a persistent context so saved logins (Indeed etc.) are preserved
        browser_data_dir = Path(__file__).parent / "data" / "browser_profile"
        browser_data_dir.mkdir(parents=True, exist_ok=True)

        context = pw.chromium.launch_persistent_context(
            str(browser_data_dir),
            headless=args.headless,
            slow_mo=100,            # slight delay between actions — more human-like
            args=["--start-maximized"],
        )
        page = context.new_page()

        for job in jobs:
            result = apply_to_job(page, job, profile, args.dry_run)
            results[result] = results.get(result, 0) + 1

            # Update status in DB
            new_status = result if result in ("applied", "skipped") else result
            with db.get_conn() as conn:
                conn.execute("UPDATE jobs SET status = ? WHERE url = ?", (new_status, job["url"]))
                conn.commit()

            _human_delay(2.0, 4.0)   # pause between applications

        context.close()

    logger.info("\n=== RESULTS ===")
    for k, v in results.items():
        if v:
            logger.info("  %-14s %d", k + ":", v)

    # Print needs_review list for manual follow-up
    with db.get_conn() as conn:
        review = conn.execute(
            "SELECT title, company, url FROM jobs WHERE status = 'needs_review' ORDER BY claude_compatibility DESC LIMIT 20"
        ).fetchall()
    if review:
        logger.info("\n--- Needs manual review ---")
        for r in review:
            logger.info("  %s @ %s\n  %s", r["title"], r["company"], r["url"])


if __name__ == "__main__":
    main()
