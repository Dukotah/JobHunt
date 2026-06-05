# JobHunt — Remote Job Automation Pipeline

Finds remote jobs where Claude Code can handle most of the actual work.
Scrapes listings, scores them with AI, stores in SQLite, and generates a daily markdown digest.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key_here
```

## Usage

```bash
# Full pipeline: scrape → score → save → digest
python main.py run

# Individual steps
python main.py scrape       # fetch and store new listings (unscored)
python main.py score        # score any unscored jobs in the database
python main.py digest       # regenerate today's digest from DB
python main.py digest --date 2025-06-01   # digest for a specific date

# Run on a daily schedule (07:00 UTC)
python main.py schedule
```

## Sources

| Source | Method |
|--------|--------|
| We Work Remotely | RSS (8 category feeds) |
| Remotive | RSS |
| Remote.co | HTML scraper (6 categories) |
| Jobspresso | HTML scraper |

## Scoring

Each listing is evaluated by Claude with two scores:

**General Score (0–10):** Async-friendliness, output-based work, $50k+ pay, fit with web dev / PM / ops background.

**Claude Compatibility (0–100%):** What percentage of daily tasks could Claude Code realistically handle?

| Range | Meaning |
|-------|---------|
| 80–100% | Async writing, coding, research, data, docs, no-code automation |
| 50–79% | Mix of async + some live coordination |
| 20–49% | Significant human judgment or relationship management required |
| 0–19% | Physical presence, real-time calls, or live supervision required |

## Database

SQLite at `data/jobs.db`. Schema:

```
id, title, company, source, url, salary, description,
score, claude_compatibility, category, reason, date_found, status
```

## Reports

Markdown digests saved to `reports/digest-YYYY-MM-DD.md`.
Top 10 jobs ranked by `claude_compatibility DESC, score DESC`.

## Cron Example

```cron
0 7 * * * cd /path/to/JobHunt && python main.py run >> logs/cron.log 2>&1
```
