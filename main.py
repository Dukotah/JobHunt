#!/usr/bin/env python3
"""
JobHunt — remote job automation pipeline.

Usage:
    python main.py run          # scrape → score → save → digest (full pipeline)
    python main.py scrape       # scrape only (no scoring)
    python main.py score        # score unscored jobs already in DB
    python main.py digest       # regenerate today's digest from DB
    python main.py schedule     # run full pipeline daily at 07:00 UTC
"""

import argparse
import logging
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

import db
import scraper
import scorer
import digest


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def cmd_run(_args):
    db.init_db()
    logger.info("=== SCRAPE ===")
    jobs = scraper.scrape_all()

    new_jobs = [j for j in jobs if not db.url_exists(j["url"])]
    logger.info("%d new (of %d scraped)", len(new_jobs), len(jobs))

    if new_jobs:
        logger.info("=== SCORE ===")
        scored = scorer.score_jobs(new_jobs)

        saved = sum(db.insert_job(j) for j in scored)
        logger.info("Saved %d jobs to database", saved)
    else:
        logger.info("No new jobs to score.")

    logger.info("=== DIGEST ===")
    path = digest.generate(_today())
    logger.info("Digest written to %s", path)

    csv_path = db.export_csv()
    logger.info("CSV updated at %s", csv_path)


def cmd_scrape(_args):
    db.init_db()
    jobs = scraper.scrape_all()
    new_jobs = [j for j in jobs if not db.url_exists(j["url"])]
    logger.info("%d new jobs found", len(new_jobs))
    for j in new_jobs:
        db.insert_job(j)
    logger.info("Saved (unscored) to database")


def cmd_score(_args):
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE score IS NULL ORDER BY date_found DESC"
        ).fetchall()
    jobs = [dict(r) for r in rows]
    logger.info("Scoring %d unscored jobs", len(jobs))
    if not jobs:
        return
    scored = scorer.score_jobs(jobs)
    with db.get_conn() as conn:
        for j in scored:
            conn.execute(
                """UPDATE jobs SET score=?, claude_compatibility=?, category=?, reason=?
                   WHERE url=?""",
                (j["score"], j["claude_compatibility"], j["category"], j["reason"], j["url"]),
            )
        conn.commit()
    logger.info("Done scoring")


def cmd_digest(args):
    db.init_db()
    date = getattr(args, "date", None) or _today()
    path = digest.generate(date)
    logger.info("Digest written to %s", path)
    print(path.read_text())


def cmd_schedule(_args):
    import schedule
    import time

    def job():
        logger.info("Scheduled run starting")
        cmd_run(None)

    schedule.every().day.at("07:00").do(job)
    logger.info("Scheduler started — will run daily at 07:00 UTC. Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="Remote job hunting pipeline")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("run", help="Full pipeline: scrape → score → digest")
    sub.add_parser("scrape", help="Scrape only (no scoring)")
    sub.add_parser("score", help="Score unscored jobs in DB")

    p_digest = sub.add_parser("digest", help="Generate digest (optional: --date YYYY-MM-DD)")
    p_digest.add_argument("--date", default=None)

    sub.add_parser("schedule", help="Run daily at 07:00 UTC")

    args = parser.parse_args()

    dispatch = {
        "run": cmd_run,
        "scrape": cmd_scrape,
        "score": cmd_score,
        "digest": cmd_digest,
        "schedule": cmd_schedule,
    }

    if args.cmd not in dispatch:
        parser.print_help()
        sys.exit(1)

    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
