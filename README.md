# JobHunt — Remote Job Automation Pipeline

Finds remote jobs, scores them by Claude Code compatibility, and **automatically applies** using browser automation. No API keys required.

---

## How it works

```
GitHub Actions (daily, free)          Your machine (on demand)
─────────────────────────────         ──────────────────────────────
Scrape 800-1800 job listings    →     Read top-scored jobs from DB
Score by Claude compatibility         Open real browser (Playwright)
Save to SQLite + CSV            →     Auto-fill Greenhouse/Lever/Indeed
Commit digest back to repo            Submit applications
                                      Mark applied in DB
```

---

## Part 1 — Scraping (GitHub Actions, runs daily)

### Setup

1. Push this repo to GitHub
2. Go to **Actions → Daily Job Hunt → Run workflow** to trigger the first run
3. After ~5 min, check your repo for `reports/digest-YYYY-MM-DD.md` and `data/jobs.csv`

The scraper runs automatically every day at 07:00 UTC. No secrets or API keys needed.

---

## Part 2 — Auto-applying (your local machine)

### Setup

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Pull the latest job data from GitHub
git pull

# Fill in your details
cp profile.json my_profile.json   # edit with your name, email, resume path, etc.
```

Edit `profile.json` with your:
- Name, email, phone
- Path to your resume PDF
- LinkedIn / GitHub URLs
- Cover letter template
- Standard answers to common questions

### First run — log into job sites

```bash
python applicator.py --dry-run
```

This opens a real Chrome window. **Log into Indeed** (and any other sites you want) while it's open. Your session is saved to `data/browser_profile/` and reused on every future run.

### Apply to jobs

```bash
# Apply to top 30 jobs (compat >= 60%) — shows browser, asks before submitting
python applicator.py

# Apply to more jobs
python applicator.py --limit 50 --min-compat 50

# Greenhouse and Lever only (most reliable, no CAPTCHA)
python applicator.py --source greenhouse
python applicator.py --source lever

# Preview without submitting
python applicator.py --dry-run

# Run in background (no browser window)
python applicator.py --headless
```

After each run, check `needs_review` jobs — these are ones where the form had unusual questions or the submit button wasn't found. Open them manually.

---

## Sources (all free, no API keys)

| Source | Type | Volume |
|--------|------|--------|
| **Greenhouse ATS** | JSON API | 80+ companies: Notion, Figma, Stripe, GitLab, Zapier, Buffer… |
| **Lever ATS** | JSON API | 60+ companies: Anthropic, Deel, Toptal, Postman, Retool… |
| **Remote OK** | JSON API | ~300 listings/day with salary data |
| **Arbeitnow** | JSON API | ~300 remote listings/day |
| **The Muse** | JSON API | ~500 US/remote listings/day |
| **Remotive** | RSS | Large curated remote board |
| **We Work Remotely** | RSS | Popular remote-only board |
| **Himalayas** | RSS | Curated remote-only |
| **Jobicy** | RSS | Remote-only |
| **Working Nomads** | RSS | Curated remote listings |

**Expected total: 800–1,800 unique remote listings per daily run.**

---

## Scoring

Each listing is scored on two axes by a keyword heuristic engine:

**Claude Compatibility (0–100%):** How much of the daily work could Claude Code handle?

| Range | Meaning |
|-------|---------|
| 80–100% | Async writing, coding, research, data, docs, no-code automation |
| 50–79% | Mix of async + some live coordination |
| 20–49% | Significant human judgment or relationship management |
| 0–19% | Live calls, physical presence, real-time supervision |

**General Score (0–10):** Remote/async signals, salary ($50k+), background fit (web dev, PM, ops, agency).

---

## File structure

```
JobHunt/
├── .github/workflows/daily_hunt.yml  # GitHub Actions pipeline
├── data/
│   ├── jobs.db                        # SQLite — all jobs + status
│   ├── jobs.csv                       # CSV export for spreadsheet browsing
│   └── browser_profile/               # Saved browser login sessions (gitignored)
├── reports/
│   └── digest-YYYY-MM-DD.md          # Daily top-50 digest
├── scraper.py      # All scrapers (Greenhouse, Lever, RSS, JSON APIs)
├── scorer.py       # Heuristic scoring engine
├── applicator.py   # Playwright auto-applicator
├── db.py           # SQLite helpers
├── digest.py       # Markdown report generator
├── main.py         # CLI: run / scrape / score / digest
├── profile.json    # Your personal info + cover letter template
└── requirements.txt
```

---

## Adding more companies

To add a company to the Greenhouse or Lever scrapers, find their slug (the part of their job board URL after `greenhouse.io/` or `lever.co/`) and add it to the list in `scraper.py`:

```python
GREENHOUSE_COMPANIES = [
    ...,
    "notion",        # boards.greenhouse.io/notion
    "yourcompany",   # boards.greenhouse.io/yourcompany
]
```

---

## Cron (optional local schedule)

```cron
# Pull fresh data from GitHub and apply every morning at 8am
0 8 * * * cd /path/to/JobHunt && git pull && python applicator.py --limit 30
```
