"""Heuristic job scorer — no API key required.

Scores each job on two axes using keyword matching and salary parsing:
  - score (0-10): general fit for async/remote/output-based work at $50k+
  - claude_compatibility (0-100): % of daily tasks Claude Code could handle
"""

import re

# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

# Each entry: (pattern, weight)  — patterns are matched case-insensitively
# against the combined title + description text.

_COMPAT_HIGH: list[tuple[str, float]] = [
    # Writing & content
    (r"\btech(?:nical)?\s+writ", 22),
    (r"\bcopywrite?r\b", 20),
    (r"\bcopy\s*writ", 18),
    (r"\bcontent\s+writ", 18),
    (r"\bcontent\s+strat", 18),
    (r"\bcontent\s+manag", 16),
    (r"\bcontent\s+creat", 16),
    (r"\bghost\s*writ", 18),
    (r"\bblog\b", 10),
    (r"\barticle", 10),
    (r"\bedit(?:or|orial|ing)\b", 12),
    (r"\bproofreader?\b", 12),
    (r"\bseo\b", 12),
    (r"\bnewsletter\b", 10),
    (r"\bdocument(?:ation)?\b", 18),
    (r"\bknowledge\s+base\b", 12),
    (r"\bpublish\b", 8),
    (r"\bwrite\b", 8),
    (r"\bwriting\b", 8),
    # Development / code
    (r"\bdeveloper\b", 14),
    (r"\bsoftware\s+eng", 14),
    (r"\bfull.?stack\b", 14),
    (r"\bback.?end\b", 12),
    (r"\bfront.?end\b", 12),
    (r"\bweb\s+dev", 12),
    (r"\bpython\b", 10),
    (r"\bjavascript\b", 10),
    (r"\bapi\b", 8),
    (r"\bautomation\b", 14),
    (r"\bno.?code\b", 16),
    (r"\bwordpress\b", 10),
    # Research & data
    (r"\bresearch\s+analyst\b", 18),
    (r"\bdata\s+analyst\b", 16),
    (r"\bdata\s+entry\b", 14),
    (r"\bdata\s+process", 12),
    (r"\bspreadsheet\b", 10),
    (r"\breport(?:ing)?\b", 8),
    (r"\bqualitative\b", 8),
    (r"\bquantitative\b", 8),
    # Operations / VA
    (r"\bvirtual\s+assist", 14),
    (r"\bproject\s+coord", 12),
    (r"\boperations\s+coord", 12),
    (r"\badmin(?:istrative)?\s+assist", 12),
    (r"\bschedule(?:r|ing)?\b", 8),
    (r"\binbox\s+manage", 10),
    (r"\bemail\s+manage", 10),
    # Async signals
    (r"\basync", 10),
    (r"\bremote.first\b", 8),
    (r"\bown\s+your\s+(schedule|time|hours)", 8),
    (r"\bflexible\s+hours?\b", 8),
    (r"\bout?put.based\b", 10),
]

_COMPAT_LOW: list[tuple[str, float]] = [
    (r"\blive\s+(chat|support|session)", -20),
    (r"\bphone\s+support\b", -20),
    (r"\bcall\s+center\b", -25),
    (r"\bon.?site\b", -20),
    (r"\bin.?office\b", -20),
    (r"\btravel\s+required\b", -25),
    (r"\bphysical\b", -12),
    (r"\breal.?time\s+(monitor|supervis|over)", -15),
    (r"\bsupervis(e|or|ory)\b", -10),
    (r"\bmanage\s+a\s+team\b", -8),
    (r"\bcold\s+call", -18),
    (r"\bsales\s+call", -15),
    (r"\bappointment\s+set", -14),
    (r"\bdoor.to.door\b", -30),
]

_FIT_SIGNALS: list[tuple[str, float]] = [
    # Background match
    (r"\bproject\s+manag", 10),
    (r"\bweb\s+dev", 10),
    (r"\bit\s+serv", 8),
    (r"\bagency\b", 8),
    (r"\boperations\b", 8),
    (r"\bfreelance\b", 6),
    (r"\bcontract\b", 6),
    # Remote/async — these are the biggest fit signals
    (r"\bfully\s+remote\b", 10),
    (r"\b100.{0,5}remote\b", 10),
    (r"\bwork\s+from\s+anywhere\b", 10),
    (r"\basync(?:hronous|.first)?\b", 10),
    (r"\bno\s+meeting", 8),
    (r"\boutput.based\b", 10),
    (r"\bown\s+your\s+(schedule|hours)\b", 8),
    (r"\bflexible\s+hours?\b", 6),
    (r"\bflexible\b", 4),
    # Role fit for a web dev / PM / agency owner
    (r"\btechnical\s+writ", 8),
    (r"\bcontent\s+(writ|strat|manag)", 6),
    (r"\bno.?code\b", 8),
    (r"\bautomation\b", 6),
    (r"\bdocumentation\b", 6),
    # Salary (also handled numerically below)
    (r"\$\d{2,3}k", 4),
    (r"\$\d{2,3},000\b", 4),
]

_FIT_NEGATIVE: list[tuple[str, float]] = [
    (r"\bentry.level\b", -4),
    (r"\binternship\b", -10),
    (r"\bunpaid\b", -20),
    (r"\bpart.time\b", -3),
    (r"\bon.?site\b", -10),
    (r"\bin.?office\b", -10),
    (r"\bcommission.?only\b", -12),
]

# Category inference: first match wins
_CATEGORIES: list[tuple[str, str]] = [
    (r"\btech(?:nical)?\s+writ|documentation\b|docs?\b", "documentation"),
    (r"\bcopywrite?r|content\s+(writ|strat|manag|creat)|blog|seo|newsletter|ghost.writ|editorial", "writing"),
    (r"\bdeveloper|engineer|programmer|full.?stack|back.?end|front.?end", "development"),
    (r"\bresearch", "research"),
    (r"\bdata\s+(analyst|entry|process|scien)", "data"),
    (r"\bvirtual\s+assist|admin|executive\s+assist", "operations"),
    (r"\bsupport|customer\s+success|helpdesk", "support"),
    (r"\bproject\s+manag|product\s+manag|program\s+manag", "operations"),
    (r"\bno.?code|automation|zapier|make\.com|airtable", "development"),
]

# ---------------------------------------------------------------------------
# Salary parser
# ---------------------------------------------------------------------------

_SALARY_NUM_RE = re.compile(
    r"\$\s*([\d,]+)\s*(?:k\b)?", re.IGNORECASE
)


def _parse_salary_usd(text: str) -> float | None:
    """Return the lower bound of the first salary range found, annualised."""
    nums = []
    for m in _SALARY_NUM_RE.finditer(text):
        raw = m.group(1).replace(",", "")
        val = float(raw)
        if val < 1000:          # e.g. "$75k" → 75 → 75000
            val *= 1000
        if 10_000 <= val <= 600_000:
            nums.append(val)
    return min(nums) if nums else None


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

def _match_score(text: str, rules: list[tuple[str, float]]) -> float:
    total = 0.0
    for pattern, weight in rules:
        if re.search(pattern, text, re.IGNORECASE):
            total += weight
    return total


def score_job(job: dict) -> dict:
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    title = job.get("title", "").lower()
    desc = (job.get("description", "") or "").lower()

    # --- Claude compatibility ---
    # Title is the strongest signal — weight it 2× over description
    raw_compat = (
        _match_score(title, _COMPAT_HIGH) * 2.0 +
        _match_score(desc,  _COMPAT_HIGH) * 1.0 +
        _match_score(text,  _COMPAT_LOW)
    )
    # Async/remote in the description boosts compat directly
    if re.search(r"\basync(?:hronous|.first)?\b", text, re.IGNORECASE):
        raw_compat += 12
    if re.search(r"\bno\s+meeting|\bfully\s+remote\b|\b100.{0,5}remote\b", text, re.IGNORECASE):
        raw_compat += 8
    if re.search(r"\boutput.based\b|\bown\s+your\s+(schedule|hours)\b", text, re.IGNORECASE):
        raw_compat += 10
    compat = max(0.0, min(100.0, raw_compat))

    # --- General fit ---
    raw_fit = _match_score(text, _FIT_SIGNALS) + _match_score(text, _FIT_NEGATIVE)

    salary = _parse_salary_usd(text + " " + job.get("salary", ""))
    if salary is not None:
        if salary >= 80_000:
            raw_fit += 12
        elif salary >= 50_000:
            raw_fit += 6
        elif salary < 30_000:
            raw_fit -= 10

    # Normalise to 0-10
    score = max(0.0, min(10.0, raw_fit / 8))

    # --- Category ---
    category = "other"
    for pattern, cat in _CATEGORIES:
        if re.search(pattern, text, re.IGNORECASE):
            category = cat
            break

    # --- Reason ---
    reason = _build_reason(text, compat, score, salary)

    return {
        **job,
        "score": round(score, 1),
        "claude_compatibility": round(compat, 1),
        "category": category,
        "reason": reason,
    }


def _build_reason(text: str, compat: float, score: float, salary: float | None) -> str:
    parts = []
    if compat >= 80:
        parts.append("High async/writing/coding signal")
    elif compat >= 50:
        parts.append("Mixed async and real-time tasks")
    else:
        parts.append("Low async signal or live-work required")

    if salary:
        parts.append(f"${salary/1000:.0f}k salary")
    elif re.search(r"\$|salary|compensation|pay\b", text, re.IGNORECASE):
        parts.append("salary mentioned but unclear")
    else:
        parts.append("no salary listed")

    if re.search(r"\bon.?site|in.?office|travel\s+required", text, re.IGNORECASE):
        parts.append("on-site/travel flags present")

    return "; ".join(parts) + "."


# ---------------------------------------------------------------------------
# Batch API (same interface as old scorer.py)
# ---------------------------------------------------------------------------

def score_jobs(jobs: list[dict], delay: float = 0) -> list[dict]:
    return [score_job(j) for j in jobs]
