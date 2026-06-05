"""
Resume and cover letter tailor.

Uses Claude Haiku (cheap — ~$0.02/resume) to rewrite your master resume
bullets and summary to match each job's language and keywords.

Falls back to keyword-based selection if no API key is set.

Usage:
    python tailor.py --job-url https://boards.greenhouse.io/notion/jobs/123
    python tailor.py --job-id 42          # by DB row id
    python tailor.py --top 10             # tailor top 10 unprocessed jobs
"""

import argparse
import json
import logging
import os
import re
import copy
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

MASTER_RESUME_PATH = Path(__file__).parent / "master_resume.json"
OUTPUT_DIR = Path(__file__).parent / "tailored"

# ---------------------------------------------------------------------------
# Load master resume
# ---------------------------------------------------------------------------

def load_master() -> dict:
    if not MASTER_RESUME_PATH.exists():
        raise FileNotFoundError("master_resume.json not found — fill it in first.")
    return json.loads(MASTER_RESUME_PATH.read_text())


# ---------------------------------------------------------------------------
# Claude-powered tailoring (best quality, ~$0.02/resume)
# ---------------------------------------------------------------------------

TAILOR_PROMPT = """You are a professional resume writer. Given a job description and a candidate's master resume data, rewrite the resume to maximize fit.

Rules:
- Keep all facts truthful — only reorder, reword, and emphasize; never invent experience
- Rewrite bullet points to mirror the job's language and keywords
- Reorder bullets so the most relevant ones come first
- Update the summary to speak directly to this specific role
- Select only the most relevant skills (max 12) from the skill list
- Return ONLY valid JSON matching the exact input structure

Job Title: {title}
Company: {company}
Job Description:
{description}

Master Resume JSON:
{resume_json}

Return a modified version of the resume JSON with tailored summary, reordered/rewritten bullets, and filtered skills. Keep the same JSON structure exactly."""

COVER_PROMPT = """Write a concise, genuine cover letter (3 short paragraphs, under 200 words) for this job application.

Job: {title} at {company}
Candidate summary: {summary}
Key relevant skills: {skills}
Job description excerpt: {description_excerpt}

Tone: confident, specific, not sycophantic. No "I am excited to apply" opener.
Return only the cover letter text, no subject line or salutation."""


def tailor_with_claude(master: dict, job: dict) -> tuple[dict, str]:
    """Returns (tailored_resume_dict, cover_letter_text)."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Tailor the resume
    resume_response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": TAILOR_PROMPT.format(
                title=job.get("title", ""),
                company=job.get("company", ""),
                description=(job.get("description", "") or "")[:3000],
                resume_json=json.dumps(master, indent=2),
            )
        }]
    )
    raw = resume_response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    tailored = json.loads(raw)

    # Generate cover letter
    cover_response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": COVER_PROMPT.format(
                title=job.get("title", ""),
                company=job.get("company", ""),
                summary=master.get("summary", ""),
                skills=", ".join(master.get("skills", [])[:10]),
                description_excerpt=(job.get("description", "") or "")[:1000],
            )
        }]
    )
    cover_letter = cover_response.content[0].text.strip()

    return tailored, cover_letter


# ---------------------------------------------------------------------------
# Keyword-based fallback (no API key needed)
# ---------------------------------------------------------------------------

def tailor_with_keywords(master: dict, job: dict) -> tuple[dict, str]:
    """Simple keyword-based tailoring — no API needed."""
    tailored = copy.deepcopy(master)
    jd = (job.get("title", "") + " " + job.get("description", "")).lower()

    # Filter skills to ones mentioned in the JD
    all_skills = master.get("skills", [])
    matching = [s for s in all_skills if s.lower() in jd]
    others = [s for s in all_skills if s not in matching]
    tailored["skills"] = (matching + others)[:14]

    # Score each bullet by keyword overlap with JD, reorder
    jd_words = set(re.findall(r"\b\w{4,}\b", jd))
    for exp in tailored.get("experience", []):
        scored = []
        for bullet in exp.get("bullets", []):
            words = set(re.findall(r"\b\w{4,}\b", bullet.lower()))
            score = len(words & jd_words)
            scored.append((score, bullet))
        exp["bullets"] = [b for _, b in sorted(scored, reverse=True)]

    # Build cover letter from template
    title = job.get("title", "this role")
    company = job.get("company", "your company")
    top_skills = ", ".join(tailored["skills"][:5])
    cover_letter = (
        f"Applying for the {title} position at {company}.\n\n"
        f"I bring {master.get('summary', 'a strong background in web development and operations')} "
        f"My most relevant skills for this role include {top_skills}.\n\n"
        f"I work best in async, output-based environments and have a track record of delivering "
        f"independently. I'd welcome the chance to discuss how I can contribute to {company}."
    )
    return tailored, cover_letter


# ---------------------------------------------------------------------------
# PDF generation via Playwright
# ---------------------------------------------------------------------------

RESUME_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Georgia', serif; font-size: 11px; color: #111;
          padding: 32px 40px; max-width: 780px; margin: 0 auto; }}
  h1 {{ font-size: 22px; font-weight: bold; letter-spacing: 0.5px; }}
  .contact {{ font-size: 10px; color: #444; margin: 4px 0 14px; }}
  .contact a {{ color: #444; text-decoration: none; }}
  h2 {{ font-size: 12px; font-weight: bold; text-transform: uppercase;
        letter-spacing: 1px; border-bottom: 1px solid #bbb;
        padding-bottom: 3px; margin: 14px 0 6px; }}
  .summary {{ line-height: 1.5; margin-bottom: 4px; }}
  .skills {{ line-height: 1.6; }}
  .job {{ margin-bottom: 10px; }}
  .job-header {{ display: flex; justify-content: space-between; }}
  .job-title {{ font-weight: bold; font-size: 11.5px; }}
  .job-company {{ color: #333; }}
  .job-dates {{ color: #555; font-size: 10px; }}
  ul {{ margin: 4px 0 0 16px; }}
  li {{ margin-bottom: 2px; line-height: 1.45; }}
  .project {{ margin-bottom: 6px; }}
  .project-name {{ font-weight: bold; }}
  .edu-line {{ display: flex; justify-content: space-between; }}
  .certs {{ margin-top: 4px; color: #333; }}
</style>
</head>
<body>
  <h1>{name}</h1>
  <div class="contact">
    {email} &nbsp;·&nbsp; {phone} &nbsp;·&nbsp; {location}
    {linkedin_part}
    {github_part}
    {website_part}
  </div>

  <h2>Summary</h2>
  <p class="summary">{summary}</p>

  <h2>Skills</h2>
  <p class="skills">{skills}</p>

  <h2>Experience</h2>
  {experience_html}

  {projects_html}

  <h2>Education</h2>
  {education_html}

  {certs_html}
</body>
</html>
"""


def _exp_html(exp_list: list) -> str:
    parts = []
    for e in exp_list:
        bullets = "\n".join(f"<li>{b}</li>" for b in e.get("bullets", []))
        dates = f"{e.get('start','')} – {e.get('end','')}"
        parts.append(f"""
        <div class="job">
          <div class="job-header">
            <span><span class="job-title">{e['title']}</span>
            &nbsp;·&nbsp; <span class="job-company">{e['company']}</span></span>
            <span class="job-dates">{dates}</span>
          </div>
          <ul>{bullets}</ul>
        </div>""")
    return "\n".join(parts)


def _projects_html(projects: list) -> str:
    if not projects:
        return ""
    parts = ['<h2>Projects</h2>']
    for p in projects:
        tech = ", ".join(p.get("tech", []))
        parts.append(f"""<div class="project">
          <span class="project-name">{p['name']}</span> — {p['description']}
          {"<br><small>" + tech + "</small>" if tech else ""}
        </div>""")
    return "\n".join(parts)


def _edu_html(edu_list: list) -> str:
    parts = []
    for e in edu_list:
        parts.append(f"""<div class="edu-line">
          <span><strong>{e['degree']}</strong> — {e['school']}</span>
          <span>{e.get('year','')}</span>
        </div>""")
    return "\n".join(parts)


def build_html(resume: dict) -> str:
    p = resume.get("personal", {})
    linkedin = f" &nbsp;·&nbsp; <a href='https://{p.get('linkedin','')}'>{p.get('linkedin','')}</a>" if p.get("linkedin") else ""
    github = f" &nbsp;·&nbsp; <a href='https://{p.get('github','')}'>{p.get('github','')}</a>" if p.get("github") else ""
    website = f" &nbsp;·&nbsp; <a href='{p.get('website','')}'>{p.get('website','')}</a>" if p.get("website") else ""

    certs = resume.get("certifications", [])
    certs_html = ('<h2>Certifications</h2><p class="certs">' + " &nbsp;·&nbsp; ".join(certs) + '</p>') if certs else ""

    return RESUME_HTML.format(
        name=p.get("name", ""),
        email=p.get("email", ""),
        phone=p.get("phone", ""),
        location=p.get("location", ""),
        linkedin_part=linkedin,
        github_part=github,
        website_part=website,
        summary=resume.get("summary", ""),
        skills=", ".join(resume.get("skills", [])),
        experience_html=_exp_html(resume.get("experience", [])),
        projects_html=_projects_html(resume.get("projects", [])),
        education_html=_edu_html(resume.get("education", [])),
        certs_html=certs_html,
    )


def generate_pdf(resume: dict, output_path: Path) -> Path:
    """Render resume HTML to PDF using Playwright."""
    from playwright.sync_api import sync_playwright
    html = build_html(resume)
    html_path = output_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(f"file://{html_path.resolve()}")
        page.pdf(path=str(output_path), format="Letter", margin={
            "top": "0.4in", "bottom": "0.4in",
            "left": "0.4in", "right": "0.4in"
        })
        browser.close()

    html_path.unlink()  # clean up temp HTML
    return output_path


# ---------------------------------------------------------------------------
# Main tailoring flow
# ---------------------------------------------------------------------------

def tailor_job(job: dict) -> dict:
    """Tailor resume + cover letter for one job. Returns paths dict."""
    master = load_master()

    use_claude = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if use_claude:
        logger.info("  Tailoring with Claude Haiku...")
        tailored, cover = tailor_with_claude(master, job)
    else:
        logger.info("  Tailoring with keyword matching (set ANTHROPIC_API_KEY for better results)...")
        tailored, cover = tailor_with_keywords(master, job)

    # Safe filename from job title + company
    slug = re.sub(r"[^\w]+", "_", f"{job.get('title','')}_{job.get('company','')}").strip("_").lower()[:60]
    OUTPUT_DIR.mkdir(exist_ok=True)

    pdf_path = generate_pdf(tailored, OUTPUT_DIR / f"{slug}_resume.pdf")
    cover_path = OUTPUT_DIR / f"{slug}_cover.txt"
    cover_path.write_text(cover, encoding="utf-8")

    logger.info("  Resume → %s", pdf_path)
    logger.info("  Cover  → %s", cover_path)

    return {"resume_pdf": str(pdf_path), "cover_letter": str(cover_path), "cover_text": cover}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Tailor resume + cover letter for jobs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--top",     type=int, help="Tailor top N unprocessed high-scoring jobs")
    group.add_argument("--job-url", help="Tailor for a specific job URL")
    group.add_argument("--job-id",  type=int, help="Tailor for a job by DB id")
    parser.add_argument("--min-compat", type=float, default=65.0)
    args = parser.parse_args()

    import db
    db.init_db()

    if args.job_url:
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE url = ?", (args.job_url,)).fetchone()
        jobs = [dict(row)] if row else []
    elif args.job_id:
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (args.job_id,)).fetchone()
        jobs = [dict(row)] if row else []
    else:
        with db.get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM jobs
                WHERE claude_compatibility >= ? AND score IS NOT NULL
                AND status NOT IN ('applied')
                ORDER BY claude_compatibility DESC, score DESC
                LIMIT ?
            """, (args.min_compat, args.top)).fetchall()
        jobs = [dict(r) for r in rows]

    if not jobs:
        logger.info("No matching jobs found.")
        return

    logger.info("Tailoring %d job(s)...", len(jobs))
    for job in jobs:
        logger.info("[%s] %s @ %s", job.get("source",""), job.get("title",""), job.get("company",""))
        tailor_job(job)

    logger.info("\nAll files saved to tailored/")
    logger.info("Now run:  python applicator.py  to apply with the tailored resumes")


if __name__ == "__main__":
    main()
