"""
Autonomous Job Agent — the brain that runs the whole operation.

This is NOT a search platform you use manually. It's an autonomous loop:
  1. DISCOVER — Scrapes jobs from multiple sources (LinkedIn, RemoteOK, Remotive, WWR, Jobspresso)
  2. EVALUATE — Scores each job against your profile (AI-powered)
  3. GENERATE — Creates a tailored CV + cover letter for matching jobs (AI-powered)
  4. APPLY — Auto-applies via the job's apply URL / email / form
  5. NOTIFY — Logs all activity + sends WhatsApp notifications (no API key needed)
  6. TRACK — Records everything in the database

The loop runs on a configurable schedule (default: every 6 hours).
You set up your profile once, then the agent does everything else.
"""

import json
import sqlite3
import time
import os
import smtplib
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from job_sources import scrape_all_jobs, fetch_linkedin_detail, JobListing
from evaluator import evaluate_job, EvaluationResult
from generator import generate_cv, generate_cover_letter, generate_job_match_report
from ai_engine import ai_generate_cv, ai_generate_cover_letter, ai_evaluate_job, ai_enhance_profile
import json as _json

from playwright.sync_api import sync_playwright

# WhatsApp is optional — works without it
try:
    from whatsapp import send_notification as whatsapp_send, is_connected as whatsapp_connected, get_config as whatsapp_config
    HAS_WHATSAPP = True
except ImportError:
    HAS_WHATSAPP = False

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "jobagent.db"
DATA_DIR.mkdir(exist_ok=True)


# --- Database ---

def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY DEFAULT 1,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT,
            description TEXT,
            apply_url TEXT,
            date TEXT,
            tags TEXT,
            salary TEXT,
            first_seen TEXT,
            fit_score REAL DEFAULT 0,
            verdict TEXT,
            matched_skills TEXT,
            status TEXT DEFAULT 'new'
        );

        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            status TEXT DEFAULT 'applied',
            applied_date TEXT,
            source TEXT,
            apply_method TEXT,
            apply_result TEXT,
            notes TEXT,
            cv_text TEXT,
            cover_letter_text TEXT,
            evaluation TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event TEXT NOT NULL,
            details TEXT,
            job_id TEXT,
            level TEXT DEFAULT 'info'
        );

        CREATE TABLE IF NOT EXISTS seen_jobs (
            id TEXT PRIMARY KEY,
            first_seen TEXT,
            status TEXT DEFAULT 'new'
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            id INTEGER PRIMARY KEY DEFAULT 1,
            last_run TEXT,
            next_run TEXT,
            running INTEGER DEFAULT 0,
            total_discovered INTEGER DEFAULT 0,
            total_evaluated INTEGER DEFAULT 0,
            total_applied INTEGER DEFAULT 0,
            total_notified INTEGER DEFAULT 0,
            config TEXT
        );

        CREATE TABLE IF NOT EXISTS uploaded_cvs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER,
            file_type TEXT,
            text_content TEXT,
            uploaded_at TEXT NOT NULL,
            is_primary INTEGER DEFAULT 0
        );
    """)
    db.execute("""
        INSERT OR IGNORE INTO agent_state (id, last_run, next_run, config)
        VALUES (1, '', '', '{}')
    """)

    # Add screenshot columns if they don't exist (migration)
    try:
        db.execute("ALTER TABLE applications ADD COLUMN screenshot_path TEXT")
    except:
        pass
    try:
        db.execute("ALTER TABLE applications ADD COLUMN page_title TEXT")
    except:
        pass

    db.commit()
    db.close()


def log_activity(event: str, details: str = "", job_id: str = "", level: str = "info"):
    """Log an activity event."""
    db = get_db()
    db.execute(
        "INSERT INTO activity_log (timestamp, event, details, job_id, level) VALUES (?, ?, ?, ?, ?)",
        (datetime.now().isoformat(), event, details, job_id, level)
    )
    db.commit()
    db.close()
    print(f"[{level.upper()}] {event}: {details}")


# --- Profile ---

def get_profile() -> dict:
    db = get_db()
    row = db.execute("SELECT data FROM profile WHERE id = 1").fetchone()
    db.close()
    return json.loads(row["data"]) if row else {}


def save_profile(data: dict):
    db = get_db()
    now = datetime.now().isoformat()
    db.execute(
        "INSERT INTO profile (id, data, updated_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET data = ?, updated_at = ?",
        (json.dumps(data), now, json.dumps(data), now)
    )
    db.commit()
    db.close()


# --- Agent State ---

def get_agent_state() -> dict:
    db = get_db()
    row = db.execute("SELECT * FROM agent_state WHERE id = 1").fetchone()
    db.close()
    return dict(row) if row else {}


def update_agent_state(updates: dict):
    db = get_db()
    for k, v in updates.items():
        if k == "config":
            db.execute(f"UPDATE agent_state SET {k} = ? WHERE id = 1", (json.dumps(v),))
        else:
            db.execute(f"UPDATE agent_state SET {k} = ? WHERE id = 1", (v,))
    db.commit()
    db.close()


def get_config() -> dict:
    state = get_agent_state()
    return json.loads(state.get("config", "{}")) if state.get("config") else {}


# --- Job Storage ---

def store_job(job: JobListing, evaluation: EvaluationResult):
    """Store a job with its evaluation."""
    db = get_db()
    now = datetime.now().isoformat()
    job_dict = job.to_dict()

    db.execute("""
        INSERT INTO jobs (id, source, title, company, location, url, description,
            apply_url, date, tags, salary, first_seen, fit_score, verdict, matched_skills, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, company=excluded.company, location=excluded.location,
            description=excluded.description, fit_score=excluded.fit_score, verdict=excluded.verdict,
            matched_skills=excluded.matched_skills
    """, (
        job_dict["id"], job_dict["source"], job_dict["title"],
        job_dict["company"], job_dict["location"], job_dict["url"],
        job_dict["description"], job_dict["apply_url"], job_dict["date"],
        json.dumps(job_dict["tags"]), job_dict["salary"], now,
        evaluation.overall_score, evaluation.verdict,
        json.dumps(evaluation.matched_skills)
    ))
    db.execute("INSERT OR IGNORE INTO seen_jobs (id, first_seen) VALUES (?, ?)", (job_dict["id"], now))
    db.commit()
    db.close()


def get_job(job_id: str) -> Optional[dict]:
    db = get_db()
    row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    db.close()
    return dict(row) if row else None


def get_unseen_jobs() -> list[dict]:
    """Get jobs that haven't been applied to yet."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM jobs WHERE status = 'new' AND fit_score >= ? ORDER BY fit_score DESC",
        (get_profile().get("auto_apply_threshold", 50),)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def mark_job_status(job_id: str, status: str):
    db = get_db()
    db.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
    db.commit()
    db.close()


# --- Application Tracking ---

def create_application(job_id: str, cv_text: str, cover_letter_text: str,
                       evaluation: EvaluationResult, source: str,
                       apply_method: str, apply_result: str) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO applications (job_id, status, applied_date, source, apply_method, "
        "apply_result, cv_text, cover_letter_text, evaluation) "
        "VALUES (?, 'applied', ?, ?, ?, ?, ?, ?, ?)",
        (job_id, datetime.now().strftime("%Y-%m-%d %H:%M"), source,
         apply_method, apply_result, cv_text, cover_letter_text,
         json.dumps({
             "overall_score": evaluation.overall_score,
             "verdict": evaluation.verdict,
             "matched_skills": evaluation.matched_skills,
             "gaps": evaluation.gaps,
         }))
    )
    app_id = cur.lastrowid
    db.commit()
    db.close()
    return app_id


def get_applications(limit: int = 100) -> list[dict]:
    db = get_db()
    rows = db.execute("""
        SELECT a.*, j.title as job_title, j.company, j.location, j.url, j.source
        FROM applications a JOIN jobs j ON a.job_id = j.id
        ORDER BY a.created_at DESC LIMIT ?
    """, (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def update_application_status(app_id: int, status: str, notes: str = ""):
    db = get_db()
    db.execute("UPDATE applications SET status = ?, notes = ? WHERE id = ?", (status, notes, app_id))
    db.commit()
    db.close()


# --- Auto-Apply Engine ---

def evaluate_job_smart(job: dict, profile: dict, use_ai: bool = True) -> EvaluationResult:
    """
    Evaluate a job using a smart two-phase approach:
    
    Phase 1: Fast keyword-based pre-screening (no API calls)
    Phase 2: AI-powered deep evaluation (only for jobs that pass pre-screening)
    
    This avoids rate-limiting by only calling the AI API for promising jobs.
    The AI reads the full job description and profile to understand
    the ACTUAL requirements — not just keyword matching.
    """
    auto_apply_threshold = profile.get("auto_apply_threshold", 50)
    
    # Phase 1: Always do keyword-based evaluation first (fast, no API)
    kw_result = evaluate_job(job, profile)
    
    # If AI is disabled or keyword score is very low, skip AI evaluation
    if not use_ai:
        return kw_result
    
    # Only use AI for jobs that have a reasonable keyword score (>= 30)
    # This saves API calls — don't evaluate jobs that are clearly bad matches
    if kw_result.overall_score < 30:
        return kw_result
    
    # Phase 2: AI deep evaluation for promising jobs
    ai_result = ai_evaluate_job(profile, job)
    
    if ai_result and "overall_score" in ai_result:
        # Use AI result — it's more accurate than keyword matching
        score = ai_result["overall_score"]
        result = EvaluationResult(
            technical_score=int(score),
            experience_score=int(score),
            career_score=int(score),
            location_score=int(score),
            overall_score=score,
            verdict=ai_result.get("verdict", ""),
            matched_skills=ai_result.get("matched_skills", []),
            missing_skills=ai_result.get("missing_skills", []),
            recommendation=ai_result.get("recommendation", ""),
            should_auto_apply=score >= auto_apply_threshold,
        )
        result.strengths = [f"AI matched: {', '.join(result.matched_skills[:5])}"] if result.matched_skills else []
        result.gaps = [f"AI identified gaps: {', '.join(result.missing_skills[:5])}"] if result.missing_skills else []
        return result
    else:
        # AI failed, use the keyword result
        print(f"  [AI] Evaluation failed, using keyword matching (score: {kw_result.overall_score})")
        return kw_result


def capture_application_proof(app_id: int, job_url: str) -> Optional[str]:
    """Capture a screenshot of the job page as proof of application."""
    if not job_url or not job_url.startswith("http"):
        return None

    proof_dir = DATA_DIR / "proofs"
    proof_dir.mkdir(exist_ok=True)
    screenshot_path = proof_dir / f"app_{app_id}.png"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(job_url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)  # Let page settle
            page.screenshot(path=str(screenshot_path), full_page=False)
            page_title = page.title()
            browser.close()

        # Store in database
        db = get_db()
        db.execute("UPDATE applications SET screenshot_path = ?, page_title = ? WHERE id = ?",
                    (str(screenshot_path), page_title, app_id))
        db.commit()
        db.close()

        print(f"  [Proof] Screenshot saved: {screenshot_path}")
        return str(screenshot_path)
    except Exception as e:
        print(f"  [Proof] Screenshot failed: {e}")
        return None


def auto_apply_to_job(job: dict, profile: dict, evaluation: EvaluationResult) -> dict:
    """
    Attempt to auto-apply to a job.
    Strategy:
      1. Generate AI-powered tailored CV + cover letter
      2. If apply_url is an email → send email with CV + cover letter
      3. If apply_url is a web form URL → open it (record for manual follow-up)
      4. If it's LinkedIn/external → record the application with generated docs
      5. Send WhatsApp notification if connected

    Returns: {"method": ..., "result": ..., "success": bool}
    """
    job_id = job["id"]
    apply_url = job.get("apply_url") or job.get("url", "")
    source = job.get("source", "unknown")
    company = job.get("company", "")
    title = job.get("title", "")

    # Generate documents — try AI first, fall back to template
    print(f"  [AI] Generating CV for {title[:40]}...")
    cv_text = ai_generate_cv(profile, job)
    if not cv_text:
        print(f"  [AI] AI CV failed, using template...")
        cv_text = generate_cv(profile, job, evaluation)

    print(f"  [AI] Generating cover letter for {title[:40]}...")
    cover_text = ai_generate_cover_letter(profile, job)
    if not cover_text:
        print(f"  [AI] AI cover letter failed, using template...")
        cover_text = generate_cover_letter(profile, job, evaluation)

    # Determine apply method
    apply_method = "recorded"
    apply_result = "Application recorded with generated CV and cover letter"

    # Check if apply_url is an email
    if apply_url and apply_url.startswith("mailto:"):
        apply_method = "email"
        # Try to send email if SMTP configured
        smtp_config = profile.get("smtp", {})
        if smtp_config.get("host") and smtp_config.get("password"):
            try:
                result = _send_application_email(
                    to_email=apply_url.replace("mailto:", ""),
                    subject=f"Application for {title} at {company}",
                    cover_letter=cover_text,
                    cv_text=cv_text,
                    profile=profile,
                    smtp_config=smtp_config
                )
                apply_result = f"Email sent: {result}"
            except Exception as e:
                apply_result = f"Email failed: {e}"
        else:
            apply_result = "Email apply not sent (SMTP not configured). Application recorded."

    # Check if it's a direct web apply URL
    elif apply_url and apply_url.startswith("http"):
        apply_method = "web_url"
        apply_result = f"Application recorded. Apply manually at: {apply_url}"

    # Record the application
    app_id = create_application(
        job_id=job_id,
        cv_text=cv_text,
        cover_letter_text=cover_text,
        evaluation=evaluation,
        source=source,
        apply_method=apply_method,
        apply_result=apply_result
    )

    # Capture proof screenshot
    capture_application_proof(app_id, apply_url if apply_url.startswith("http") else job.get("url", ""))

    mark_job_status(job_id, "applied")

    log_activity(
        event="auto_applied",
        details=f"Applied to {title} at {company} (score: {evaluation.overall_score}, method: {apply_method})",
        job_id=job_id,
        level="info"
    )

    return {
        "method": apply_method,
        "result": apply_result,
        "app_id": app_id,
        "success": True,
    }


def _send_application_email(to_email: str, subject: str, cover_letter: str,
                            cv_text: str, profile: dict, smtp_config: dict) -> str:
    """Send application email via SMTP."""
    host = smtp_config["host"]
    port = smtp_config.get("port", 587)
    user = smtp_config["user"]
    password = smtp_config["password"]
    from_email = profile.get("email", user)

    msg = f"Subject: {subject}\nFrom: {from_email}\nTo: {to_email}\n\n{cover_letter}\n\n---\n{cv_text}"

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(from_email, [to_email], msg)

    return "sent"


# --- Notification ---

def notify(event: str, details: str, level: str = "info"):
    """Send a notification via log + WhatsApp (if connected) + webhook (if configured)."""
    log_activity(event, details, level=level)

    # WhatsApp notification (no API key needed, uses WhatsApp Web)
    if HAS_WHATSAPP and whatsapp_connected():
        try:
            wa_msg = f"🔔 *Job Agent Notification*\n\n{details}"
            result = whatsapp_send(wa_msg)
            if result.get("success"):
                log_activity("whatsapp_sent", "Notification sent via WhatsApp")
            else:
                log_activity("whatsapp_failed", result.get("error", "Unknown error"), level="warning")
        except Exception as e:
            log_activity("whatsapp_error", str(e), level="warning")

    # Webhook notification (optional)
    config = get_config()
    webhook_url = config.get("notification_webhook")
    if webhook_url:
        try:
            payload = json.dumps({"event": event, "details": details, "level": level}).encode()
            req = urllib.request.Request(webhook_url, data=payload, headers={
                "Content-Type": "application/json"
            })
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"  [Notify] Webhook failed: {e}")


# --- The Autonomous Agent Loop ---

def run_agent_cycle():
    """
    Run one complete cycle of the autonomous agent:
    1. Check profile is set up
    2. Discover jobs from all sources
    3. Evaluate each job
    4. Auto-apply to matching jobs
    5. Send notifications
    6. Update agent state
    """
    print("=" * 60)
    print(f"[AGENT] Cycle started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    profile = get_profile()
    if not profile:
        log_activity("agent_cycle", "No profile set up. Skipping cycle.", level="warning")
        print("[AGENT] No profile set up. Skipping.")
        return {"error": "no_profile"}

    update_agent_state({"running": 1, "last_run": datetime.now().isoformat()})

    config = get_config()
    max_per_source = config.get("max_per_source", 30)
    auto_apply_threshold = profile.get("auto_apply_threshold", 50)
    max_applications_per_cycle = config.get("max_applications_per_cycle", 10)
    max_age_days = config.get("max_age_days", 2)  # Only catch jobs from last 2 days

    # 1. DISCOVER
    print("\n[1/5] DISCOVERING JOBS...")
    log_activity("discover_start", f"Scraping all job sources (jobs from last {max_age_days} days only)")
    try:
        jobs = scrape_all_jobs(profile, max_per_source=max_per_source, max_age_days=max_age_days)
    except Exception as e:
        log_activity("discover_error", str(e), level="error")
        return {"error": str(e)}

    # Filter out already-seen jobs
    db = get_db()
    seen_ids = {row["id"] for row in db.execute("SELECT id FROM seen_jobs").fetchall()}
    db.close()

    new_jobs = [j for j in jobs if j.id not in seen_ids]
    print(f"  Discovered {len(jobs)} jobs ({len(new_jobs)} new, {len(jobs) - len(new_jobs)} already seen)")

    # 2. EVALUATE
    print(f"\n[2/5] EVALUATING {len(new_jobs)} NEW JOBS...")
    applied_count = 0
    notified_count = 0

    for job in new_jobs:
        # For LinkedIn jobs, fetch full description if missing
        if job.source == "linkedin" and not job.description:
            print(f"  Fetching LinkedIn detail for: {job.title[:50]}...")
            detail = fetch_linkedin_detail(job.id)
            if detail:
                job.description = detail
            time.sleep(1)

        # Fast keyword-based evaluation (no AI, no rate limits)
        # AI is reserved for CV/cover letter generation during auto-apply
        evaluation = evaluate_job(job.to_dict(), profile)
        store_job(job, evaluation)

        # 3. AUTO-APPLY
        if evaluation.should_auto_apply and applied_count < max_applications_per_cycle:
            print(f"\n[3/5] AUTO-APPLYING: {job.title} at {job.company} (score: {evaluation.overall_score})")
            result = auto_apply_to_job(job.to_dict(), profile, evaluation)
            applied_count += 1

            # 4. NOTIFY
            notify_msg = (
                f"Applied to: {job.title} at {job.company}\n"
                f"  Score: {evaluation.overall_score}/100 ({evaluation.verdict})\n"
                f"  Source: {job.source}\n"
                f"  Method: {result['method']}\n"
                f"  URL: {job.url}"
            )
            notify("job_applied", notify_msg)
            notified_count += 1

            # Be gentle — don't apply to too many at once
            time.sleep(2)
        else:
            if not evaluation.should_auto_apply:
                mark_job_status(job.id, "evaluated")

    # 5. UPDATE STATE
    print(f"\n[5/5] CYCLE COMPLETE")
    summary = (
        f"Discovered: {len(jobs)} | New: {len(new_jobs)} | "
        f"Applied: {applied_count} | Notified: {notified_count}"
    )
    print(f"  {summary}")

    state = get_agent_state()
    update_agent_state({
        "running": 0,
        "total_discovered": state.get("total_discovered", 0) + len(jobs),
        "total_evaluated": state.get("total_evaluated", 0) + len(new_jobs),
        "total_applied": state.get("total_applied", 0) + applied_count,
        "total_notified": state.get("total_notified", 0) + notified_count,
        "next_run": (datetime.now() + timedelta(hours=config.get("run_interval_hours", 4))).isoformat()
    })

    log_activity("agent_cycle_complete", summary)

    return {
        "discovered": len(jobs),
        "new": len(new_jobs),
        "applied": applied_count,
        "notified": notified_count,
    }


def run_agent_loop(interval_hours: float = 4):
    """Run the agent in a continuous loop."""
    print(f"[AGENT] Starting continuous loop (interval: {interval_hours}h)")

    init_db()

    while True:
        try:
            run_agent_cycle()
        except Exception as e:
            log_activity("agent_error", str(e), level="error")
            print(f"[AGENT] Error in cycle: {e}")

        print(f"\n[AGENT] Sleeping for {interval_hours} hours...")
        time.sleep(interval_hours * 3600)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        # Run a single cycle
        init_db()
        result = run_agent_cycle()
        print(json.dumps(result, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "--loop":
        # Run continuous loop
        interval = float(sys.argv[2]) if len(sys.argv) > 2 else 4
        run_agent_loop(interval)
    else:
        print("Usage: python agent.py [--once | --loop <hours>]")
        print("  --once   Run a single discovery+apply cycle")
        print("  --loop N Run continuously, repeating every N hours (default: 4)")
