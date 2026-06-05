"""Generate a daily markdown digest of top jobs."""

from datetime import datetime, timezone
from pathlib import Path

import db

REPORTS_DIR = Path(__file__).parent / "reports"
DIGEST_LIMIT = 50


def _compat_bar(score: float) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled) + f"  {score:.0f}%"


def generate(date: str | None = None) -> Path:
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    jobs = db.get_top_jobs(date, limit=DIGEST_LIMIT)

    REPORTS_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"digest-{date}.md"

    lines = [
        f"# Job Digest — {date}",
        "",
        f"Top {len(jobs)} remote jobs ranked by Claude Code compatibility.",
        "",
        "---",
        "",
    ]

    if not jobs:
        lines.append("_No jobs found for this date._")
    else:
        for i, job in enumerate(jobs, 1):
            meta = [f"_{job['source']}_"]
            if job.get("location"):
                meta.append(job["location"])
            if job.get("salary"):
                meta.append(job["salary"])
            meta_str = " · ".join(meta)

            lines += [
                f"## {i}. {job['title']}",
                f"**{job['company'] or 'Unknown'}** · {meta_str}",
                "",
                f"**Claude Compatibility:** {_compat_bar(job['claude_compatibility'] or 0)}",
                f"**General Score:** {job['score'] or 0:.1f}/10",
                f"**Category:** {job['category'] or '—'}",
                "",
                f"> {job['reason'] or '—'}",
                "",
                f"[Apply →]({job['url']})",
                "",
                "---",
                "",
            ]

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
