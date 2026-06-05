# JobHunt — Remote Job Automation Pipeline

Finds remote jobs where Claude Code can handle most of the actual work.
Scrapes 4 sources, scores with Claude AI on two axes, stores in SQLite,
and generates a daily markdown digest + CSV.

---

## Quickstart (GitHub Actions — recommended)

The pipeline runs automatically via GitHub Actions. No local server needed.

### 1. Add your API key as a repository secret

Go to **Settings → Secrets and variables → Actions → New repository secret**:

- Name: `ANTHROPIC_API_KEY`
- Value: your Anthropic API key

### 2. Trigger a run

- **Automatic:** runs every day at 07:00 UTC
- **Manual:** go to **Actions → Daily Job Hunt → Run workflow**

### 3. View results

After each run, the bot commits back to the repo:
- `reports/digest-YYYY-MM-DD.md` — top 10 jobs of the day
- `data/jobs.csv` — all scored jobs, spreadsheet-friendly
- `data/jobs.db` — full SQLite database

---

## Local Usage

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key_here

# Full pipeline: scrape → score → save → digest + CSV
python main.py run

# Individual steps
python main.py scrape       # fetch and store new listings (unscored)
python main.py score        # score any unscored jobs in the database
python main.py digest       # regenerate today's digest from DB
python main.py digest --date 2025-06-01

# Daemon mode (runs daily at 07:00 UTC, Ctrl+C to stop)
python main.py schedule
```

---

## Sources

| Source | Method | Categories |
|--------|--------|------------|
| We Work Remotely | RSS | Programming, Copywriting, Marketing, Support, Management, Product, DevOps, Design |
| Remotive | RSS | All remote |
| Remote.co | HTML | Writing, Developer, PM, VA, Data Entry, Analyst |
| Jobspresso | HTML | All remote |

---

## Scoring

Each listing is evaluated by **Claude Haiku** (~$0.001/listing) on two axes:

**General Score (0–10):** Async-friendliness, output-based work, $50k+ pay, fit with web dev / PM / ops background.

**Claude Compatibility (0–100%):** Estimated % of daily tasks Claude Code could realistically handle.

| Range | Meaning |
|-------|---------|
| 80–100% | Async writing, coding, research, data, docs, no-code automation |
| 50–79% | Mix of async + some real-time coordination |
| 20–49% | Significant human judgment or relationship management required |
| 0–19% | Physical presence, real-time calls, or live supervision required |

---

## Database Schema

`data/jobs.db` — SQLite, committed by CI after each run.

```
id, title, company, source, url, salary, description,
score, claude_compatibility, category, reason, date_found, status
```

`data/jobs.csv` — same data minus description, spreadsheet-friendly.

---

## File Structure

```
JobHunt/
├── .github/workflows/daily_hunt.yml   # GitHub Actions pipeline
├── data/
│   ├── jobs.db                        # SQLite database
│   └── jobs.csv                       # CSV export
├── reports/
│   └── digest-YYYY-MM-DD.md          # Daily top-10 digests
├── scraper.py     # RSS + HTML scrapers
├── scorer.py      # Claude AI scoring
├── db.py          # SQLite helpers
├── digest.py      # Markdown report generator
├── main.py        # CLI entrypoint
└── requirements.txt
```
