import csv
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "jobs.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT,
                source TEXT,
                url TEXT UNIQUE NOT NULL,
                salary TEXT,
                description TEXT,
                score REAL,
                claude_compatibility REAL,
                category TEXT,
                reason TEXT,
                date_found TEXT,
                status TEXT DEFAULT 'new'
            )
        """)
        conn.commit()


def insert_job(job: dict) -> bool:
    """Insert a job, return True if new, False if duplicate."""
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO jobs
                    (title, company, source, url, salary, description,
                     score, claude_compatibility, category, reason, date_found, status)
                VALUES
                    (:title, :company, :source, :url, :salary, :description,
                     :score, :claude_compatibility, :category, :reason, :date_found, :status)
            """, job)
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def url_exists(url: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM jobs WHERE url = ?", (url,)).fetchone()
    return row is not None


def export_csv(path: Path | None = None) -> Path:
    """Export all scored jobs to data/jobs.csv for easy browsing."""
    if path is None:
        path = Path(__file__).parent / "data" / "jobs.csv"
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT date_found, title, company, source, salary,
                   score, claude_compatibility, category, reason, url
            FROM jobs
            WHERE score IS NOT NULL
            ORDER BY date_found DESC, claude_compatibility DESC
        """).fetchall()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date_found", "title", "company", "source", "salary",
                         "score", "claude_compatibility", "category", "reason", "url"])
        writer.writerows(rows)
    return path


def get_top_jobs(date: str, limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM jobs
            WHERE date_found = ?
            ORDER BY claude_compatibility DESC, score DESC
            LIMIT ?
        """, (date, limit)).fetchall()
    return [dict(r) for r in rows]
