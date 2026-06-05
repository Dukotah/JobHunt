"""AI scorer — evaluates each job listing with Claude."""

import json
import logging
import os
import time

import anthropic

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


SYSTEM_PROMPT = """You are a remote job evaluator. Your job is to score job listings
on two axes for a candidate with this background: web developer, IT services,
project management, business operations, small agency owner.

Return ONLY a JSON object with these exact fields:
{
  "score": <float 0-10, general fit: async/output-based/flexible/$50k+>,
  "claude_compatibility": <float 0-100, % of daily tasks Claude Code could handle>,
  "category": <one of: "writing", "development", "research", "data", "documentation", "operations", "support", "other">,
  "reason": <single sentence explaining the claude_compatibility score>
}

Score rubric for claude_compatibility:
- 80-100: Mostly async writing, coding, research, data analysis, docs, no-code automation
- 50-79: Mix of async tasks with some real-time coordination
- 20-49: Significant human judgment, relationship management, or live oversight required
- 0-19: Requires physical presence, real-time calls, live supervision, or highly personal judgment

Score rubric for general score:
- 8-10: Clear remote, async-friendly, output-based, $50k+, matches candidate background
- 5-7: Likely remote/async but some uncertainty; pay unclear or moderate fit
- 0-4: Live calls required, pay too low, poor fit, or vague/suspicious listing"""

USER_TEMPLATE = """Evaluate this job listing:

Title: {title}
Company: {company}
Source: {source}
Salary: {salary}
Description: {description}"""


def score_job(job: dict, retries: int = 3) -> dict:
    """Return the job dict with score fields populated."""
    prompt = USER_TEMPLATE.format(
        title=job.get("title", ""),
        company=job.get("company", ""),
        source=job.get("source", ""),
        salary=job.get("salary", "unknown"),
        description=(job.get("description", "") or "")[:2000],
    )

    for attempt in range(retries):
        try:
            message = _get_client().messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw)
            return {
                **job,
                "score": float(data.get("score", 0)),
                "claude_compatibility": float(data.get("claude_compatibility", 0)),
                "category": str(data.get("category", "other")),
                "reason": str(data.get("reason", "")),
            }
        except (json.JSONDecodeError, KeyError, IndexError) as exc:
            logger.warning("Parse error on attempt %d for %s: %s", attempt + 1, job.get("url"), exc)
        except anthropic.RateLimitError:
            wait = 2 ** (attempt + 1)
            logger.warning("Rate limited, waiting %ds", wait)
            time.sleep(wait)
        except Exception as exc:
            logger.error("Scoring error for %s: %s", job.get("url"), exc)
            break

    # Return with null scores on failure
    return {**job, "score": 0.0, "claude_compatibility": 0.0, "category": "other", "reason": "scoring failed"}


def score_jobs(jobs: list[dict], delay: float = 0.3) -> list[dict]:
    """Score a list of jobs, respecting rate limits with a small delay between calls."""
    scored = []
    for i, job in enumerate(jobs):
        logger.info("Scoring %d/%d: %s", i + 1, len(jobs), job.get("title", ""))
        scored.append(score_job(job))
        if i < len(jobs) - 1:
            time.sleep(delay)
    return scored
