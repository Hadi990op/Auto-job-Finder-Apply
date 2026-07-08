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

# Captcha solver (optional — works without it)
try:
    from captcha_solver import solve_recaptcha_v2, get_recaptcha_site_key, apply_recaptcha_token, get_2captcha_balance
    HAS_CAPTCHA_SOLVER = True
except ImportError:
    HAS_CAPTCHA_SOLVER = False

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

        CREATE TABLE IF NOT EXISTS credentials (
            id INTEGER PRIMARY KEY DEFAULT 1,
            gmail_email TEXT DEFAULT '',
            gmail_password TEXT DEFAULT '',
            linkedin_email TEXT DEFAULT '',
            linkedin_password TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            twocaptcha_api_key TEXT DEFAULT '',
            use_headed_mode INTEGER DEFAULT 1,
            updated_at TEXT NOT NULL
        );
    """)
    db.execute("""
        INSERT OR IGNORE INTO agent_state (id, last_run, next_run, config)
        VALUES (1, '', '', '{}')
    """)
    db.execute("""
        INSERT OR IGNORE INTO credentials (id, gmail_email, gmail_password, linkedin_email, linkedin_password, updated_at)
        VALUES (1, '', '', '', '', '')
    """)
    db.execute("""
        INSERT OR IGNORE INTO settings (id, twocaptcha_api_key, use_headed_mode, updated_at)
        VALUES (1, '', 1, '')
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


# --- Credentials (Gmail, LinkedIn) ---

def get_credentials() -> dict:
    """Get stored login credentials for Gmail and LinkedIn."""
    db = get_db()
    row = db.execute("SELECT * FROM credentials WHERE id = 1").fetchone()
    db.close()
    if not row:
        return {"gmail_email": "", "gmail_password": "", "linkedin_email": "", "linkedin_password": ""}
    return {
        "gmail_email": row["gmail_email"] or "",
        "gmail_password": row["gmail_password"] or "",
        "linkedin_email": row["linkedin_email"] or "",
        "linkedin_password": row["linkedin_password"] or "",
    }


def save_credentials(gmail_email: str = "", gmail_password: str = "",
                     linkedin_email: str = "", linkedin_password: str = ""):
    """Save login credentials for Gmail and LinkedIn."""
    db = get_db()
    now = datetime.now().isoformat()
    db.execute(
        "INSERT INTO credentials (id, gmail_email, gmail_password, linkedin_email, linkedin_password, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET gmail_email = ?, gmail_password = ?, "
        "linkedin_email = ?, linkedin_password = ?, updated_at = ?",
        (gmail_email, gmail_password, linkedin_email, linkedin_password, now,
         gmail_email, gmail_password, linkedin_email, linkedin_password, now)
    )
    db.commit()
    db.close()


def get_settings() -> dict:
    """Get agent settings (2Captcha API key, headed mode, etc.)."""
    db = get_db()
    row = db.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    db.close()
    if not row:
        return {"twocaptcha_api_key": "", "use_headed_mode": True}
    return {
        "twocaptcha_api_key": row["twocaptcha_api_key"] or "",
        "use_headed_mode": bool(row["use_headed_mode"]),
    }


def save_settings(twocaptcha_api_key: str = "", use_headed_mode: bool = True):
    """Save agent settings."""
    db = get_db()
    now = datetime.now().isoformat()
    db.execute(
        "INSERT INTO settings (id, twocaptcha_api_key, use_headed_mode, updated_at) "
        "VALUES (1, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET twocaptcha_api_key = ?, use_headed_mode = ?, updated_at = ?",
        (twocaptcha_api_key, 1 if use_headed_mode else 0, now,
         twocaptcha_api_key, 1 if use_headed_mode else 0, now)
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
    """Capture a screenshot of the job page as proof of application.
    
    Uses a robust approach with short timeouts and error recovery.
    Even if the page is heavy or blocks automation, we still try to capture
    what we can — a partial screenshot is better than no proof.
    """
    if not job_url or not job_url.startswith("http"):
        return None

    proof_dir = DATA_DIR / "proofs"
    proof_dir.mkdir(exist_ok=True)
    screenshot_path = proof_dir / f"app_{app_id}.png"
    
    # Auto-cleanup: keep only the last 200 screenshots to save disk space
    try:
        existing = sorted(proof_dir.glob("app_*.png"))
        if len(existing) > 200:
            for old_file in existing[:-200]:
                old_file.unlink(missing_ok=True)
    except Exception:
        pass

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            
            # Short timeout — if page doesn't load in 15s, take what we have
            try:
                page.goto(job_url, timeout=15000, wait_until="domcontentloaded")
            except Exception:
                pass  # Page may have partially loaded — try screenshot anyway
            
            # Wait just 1 second for page to settle (not 2 — faster)
            try:
                page.wait_for_timeout(1000)
            except Exception:
                pass
            
            page_title = ""
            try:
                page_title = page.title()
            except Exception:
                page_title = "Unknown (page crashed)"
            
            try:
                page.screenshot(path=str(screenshot_path), full_page=False, timeout=10000)
            except Exception:
                # Try viewport-only screenshot as fallback
                try:
                    page.screenshot(path=str(screenshot_path), full_page=False, timeout=5000)
                except Exception:
                    browser.close()
                    return None
            
            browser.close()

        # Verify the screenshot was actually saved
        if not screenshot_path.exists():
            return None

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
      3. If apply_url is a web form URL → use Playwright to fill and submit
      4. If site requires account → create account using profile info, then apply
      5. Fallback: record the application with generated docs
      6. Send WhatsApp notification if connected

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
        # Try automated web application with Playwright
        try:
            web_result = _automated_web_apply(apply_url, profile, job, cv_text, cover_text)
            if web_result.get("success"):
                apply_method = web_result.get("method", "web_auto")
                apply_result = web_result.get("result", "Auto-applied via web")
            else:
                # HONEST STATUS: apply failed — mark as failed, not applied
                apply_method = "web_failed"
                apply_result = f"FAILED: {web_result.get('result', 'unknown outcome')}. Manual apply needed: {apply_url}"
        except Exception as e:
            print(f"  [AutoApply] Error: {e}")
            apply_method = "web_failed"
            apply_result = f"FAILED: Auto-apply error ({e}). Manual: {apply_url}"

    # Record the application
    # Determine if this was a real success or a failure
    is_real_success = apply_method in ("email", "web_auto", "web_auto_greenhouse", "web_auto_lever",
                                        "web_auto_workable", "web_auto_linkedin", "web_auto_mustakbil",
                                        "web_auto_techjobs_pk", "web_auto_form") or \
                      (apply_method == "recorded")

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

    # HONEST STATUS: only mark as "applied" if we actually applied, else "apply_failed"
    if is_real_success:
        mark_job_status(job_id, "applied")
        log_activity(
            event="auto_applied",
            details=f"Applied to {title} at {company} (score: {evaluation.overall_score}, method: {apply_method})",
            job_id=job_id,
            level="info"
        )
    else:
        mark_job_status(job_id, "apply_failed")
        log_activity(
            event="apply_failed",
            details=f"FAILED to auto-apply to {title} at {company}: {apply_result[:100]}",
            job_id=job_id,
            level="warning"
        )

    return {
        "method": apply_method,
        "result": apply_result,
        "app_id": app_id,
        "success": is_real_success,
    }


def _start_xvfb():
    """Start Xvfb virtual display for headed mode. Returns the process or None."""
    import subprocess
    try:
        # Check if Xvfb is available
        result = subprocess.run(["which", "Xvfb"], capture_output=True, text=True)
        if result.returncode != 0:
            return None
        
        # Start Xvfb on display :99
        proc = subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1280x900x24"],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
        os.environ["DISPLAY"] = ":99"
        time.sleep(2)
        return proc
    except Exception:
        return None


def _solve_captcha_if_needed(page, url: str) -> bool:
    """
    Check if page has a reCAPTCHA challenge and solve it using 2Captcha.
    Returns True if captcha was solved or no captcha was present.
    """
    if not HAS_CAPTCHA_SOLVER:
        return True  # No solver available, skip
    
    settings = get_settings()
    api_key = settings.get("twocaptcha_api_key", "")
    if not api_key:
        # No API key — check if we're on a captcha page
        url_lower = page.url.lower()
        if "checkpoint" in url_lower or "challenge" in url_lower or "captcha" in url_lower:
            print(f"  [Captcha] Captcha detected but no 2Captcha API key set!")
            return False
        return True  # No captcha on page
    
    url_lower = page.url.lower()
    if "checkpoint" not in url_lower and "challenge" not in url_lower and "captcha" not in url_lower:
        return True  # No captcha on page
    
    print(f"  [Captcha] Captcha challenge detected, solving with 2Captcha...")
    
    # Get reCAPTCHA site key
    site_key = get_recaptcha_site_key(page)
    if not site_key:
        print(f"  [Captcha] Could not find reCAPTCHA site key")
        return False
    
    # Solve reCAPTCHA
    token = solve_recaptcha_v2(api_key, site_key, url, timeout=120)
    if not token:
        print(f"  [Captcha] Failed to solve reCAPTCHA")
        return False
    
    # Apply token to page
    apply_recaptcha_token(page, token)
    page.wait_for_timeout(3000)
    
    # Try to click Continue/Submit button after captcha
    submit_selectors = [
        "button:has-text('Continue')", "button:has-text('Submit')", 
        "button:has-text('Verify')", "button[type='submit']",
        "button:has-text('Next')", "button:has-text('Sign in')",
    ]
    for sel in submit_selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=5000)
                page.wait_for_timeout(8000)
                break
        except Exception:
            continue
    
    # Check if we passed the captcha
    new_url = page.url.lower()
    if "checkpoint" not in new_url and "challenge" not in new_url:
        print(f"  [Captcha] ✅ Captcha solved! Now at: {page.url[:60]}")
        return True
    
    print(f"  [Captcha] Still on captcha page after solving")
    return False


def _connect_persistent_browser():
    """Try to connect to the persistent browser via CDP. Returns (browser, page, is_persistent) or (None, None, False)."""
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:9222/json/version")
        urllib.request.urlopen(req, timeout=3)
    except:
        return None, None, False
    
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        
        # Verify LinkedIn is logged in
        cookies = ctx.cookies()
        has_li_at = any(c["name"] == "li_at" for c in cookies if "linkedin" in c.get("domain", ""))
        if has_li_at:
            print(f"  [AutoApply] ✅ Using persistent browser (LinkedIn logged in)")
            return browser, page, True
        else:
            print(f"  [AutoApply] Persistent browser running but LinkedIn not logged in")
            browser.close()
            pw.stop()
            return None, None, False
    except Exception as e:
        print(f"  [AutoApply] Persistent browser connect error: {e}")
        return None, None, False


def _automated_web_apply(url: str, profile: dict, job: dict, cv_text: str, cover_text: str) -> dict:
    """
    Use browser to navigate to a job application URL and fill forms.
    
    Strategy:
    1. Try persistent browser (always running, LinkedIn logged in) — best approach
    2. Fall back to fresh Chromium + CDP with cookie injection
    3. Fall back to headless with cookies
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "method": "no_playwright", "result": "Playwright not available"}

    creds = get_credentials()
    settings = get_settings()
    ats_type = _detect_ats(url)
    print(f"  [AutoApply] URL: {url[:60]}... ATS: {ats_type or 'unknown'}")

    use_headed = settings.get("use_headed_mode", True)
    xvfb_proc = None
    _pw_instance = None
    browser = None
    page = None
    is_persistent = False

    # Strategy 1: Try persistent browser (LinkedIn already logged in)
    browser, page, is_persistent = _connect_persistent_browser()
    
    if browser and page:
        _pw_instance = None  # CDP connection manages its own playwright
        # Navigate to the job page in the persistent browser
        print(f"  [AutoApply] Navigating to job page in persistent browser...")
        try:
            page.goto(url, timeout=25000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
        except Exception as nav_e:
            err_str = str(nav_e)
            if "429" in err_str or "TOO_MANY_REDIRECTS" in err_str or "ERR_HTTP_RESPONSE_CODE_FAILURE" in err_str:
                try: browser.close()
                except: pass
                return {
                    "success": False,
                    "method": "rate_limited",
                    "result": "LinkedIn rate limit (HTTP 429). Try again later."
                }
            pass
        
        current_url = page.url
        print(f"  [AutoApply] Page loaded: {current_url[:80]}")
        
        # Check for 429 error
        try:
            if "chrome-error" in current_url:
                body = page.inner_text("body")[:200]
                if "429" in body or "isn't working" in body:
                    try: browser.close()
                    except: pass
                    return {
                        "success": False,
                        "method": "rate_limited",
                        "result": "LinkedIn rate limit (HTTP 429). Try again later."
                    }
        except:
            pass
    
    else:
        # Strategy 2: Fresh Chromium + CDP with cookie injection
        print(f"  [AutoApply] Persistent browser not available, using fresh Chromium...")
        
        import subprocess as _subprocess
        import tempfile

        chromium_bin = os.environ.get("CHROMIUM_BIN", "")
        if not chromium_bin:
            try:
                from playwright.sync_api import sync_playwright as _sp
                _pw = _sp().start()
                chromium_bin = _pw.chromium.executable_path
                _pw.stop()
            except Exception:
                pass
        if not chromium_bin or not os.path.exists(chromium_bin):
            chromium_bin = "chromium"

        _debug_port = 9555
        _chromium_proc = None
        _temp_profile = None

        if use_headed:
            xvfb_proc = _start_xvfb()
            if not xvfb_proc:
                use_headed = False

        _temp_profile = tempfile.mkdtemp(prefix="chrome_apply_")

        if use_headed:
            _chromium_args = [
                chromium_bin,
                "--no-sandbox", "--disable-gpu",
                "--user-data-dir=" + _temp_profile,
                "--window-size=1280,900",
                "--disable-blink-features=AutomationControlled",
                "--lang=en-US", "--no-first-run", "--no-default-browser-check",
                f"--remote-debugging-port={_debug_port}",
                url,  # Launch with URL directly — page loads before CDP connects
            ]
            _chromium_proc = _subprocess.Popen(
                _chromium_args,
                stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":99")},
            )
            time.sleep(5)

        _pw_instance = sync_playwright().start()
        
        if use_headed and _chromium_proc:
            # Wait for page to load before connecting CDP (avoids CDP detection)
            time.sleep(3)
            browser = _pw_instance.chromium.connect_over_cdp(f"http://127.0.0.1:{_debug_port}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            # Page should already be loaded (launched with URL)
            time.sleep(2)
        else:
            browser = _pw_instance.chromium.launch_persistent_context(
                _temp_profile,
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-blink-features=AutomationControlled"],
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            ctx = browser
            page = ctx.new_page()
            try:
                page.goto(url, timeout=25000, wait_until="domcontentloaded")
            except Exception:
                pass
            page.wait_for_timeout(3000)

        # Check for 429 error
        try:
            if "chrome-error" in page.url:
                body = page.inner_text("body")[:200]
                if "429" in body or "isn't working" in body:
                    if not is_persistent:
                        try: browser.close()
                        except: pass
                        if _pw_instance: _pw_instance.stop()
                        if _chromium_proc:
                            _chromium_proc.terminate()
                            try: _chromium_proc.wait(timeout=5)
                            except: _chromium_proc.kill()
                        if _temp_profile:
                            import shutil; shutil.rmtree(_temp_profile, ignore_errors=True)
                    return {
                        "success": False,
                        "method": "rate_limited",
                        "result": "LinkedIn rate limit (HTTP 429). Try again later."
                    }
        except:
            pass

        print(f"  [AutoApply] Page loaded: {page.url[:80]}")

        # Inject saved cookies
        cookies_file = BASE_DIR / "data" / "saved_cookies.json"
        if cookies_file.exists():
            try:
                import json as _json
                with open(cookies_file) as f:
                    saved_cookies = _json.load(f)
                valid_cookies = []
                for c in saved_cookies:
                    cookie = {
                        "name": c["name"], "value": c["value"],
                        "domain": c["domain"], "path": c.get("path", "/"),
                    }
                    if cookie["domain"] == ".www.linkedin.com":
                        cookie["domain"] = ".linkedin.com"
                    elif cookie["domain"].endswith(".linkedin.com") and cookie["domain"] != ".linkedin.com":
                        cookie["domain"] = ".linkedin.com"
                    if c.get("expires", -1) > 0:
                        cookie["expires"] = c["expires"]
                    if c.get("httpOnly") is not None:
                        cookie["httpOnly"] = c["httpOnly"]
                    if c.get("secure") is not None:
                        cookie["secure"] = c["secure"]
                    if c.get("sameSite") and c["sameSite"] != "None":
                        cookie["sameSite"] = c["sameSite"]
                    valid_cookies.append(cookie)
                if valid_cookies:
                    ctx.add_cookies(valid_cookies)
                    has_li_at = any(c["name"] == "li_at" for c in valid_cookies)
                    print(f"  [AutoApply] Injected {len(valid_cookies)} cookies (li_at: {has_li_at})")
                    
                    # Reload page with session cookies
                    print(f"  [AutoApply] Reloading with session cookies...")
                    try:
                        page.reload(timeout=20000, wait_until="domcontentloaded")
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass  # Non-fatal — may get redirect loop, continue with anonymous page
            except Exception as e:
                print(f"  [AutoApply] Cookie injection error: {e}")

    try:
        # For LinkedIn: force English locale and check session
        if ats_type == "linkedin" or "linkedin" in url.lower():
            current_url = page.url
            if "login" in current_url.lower() or "authwall" in current_url.lower():
                print(f"  [AutoApply] Login redirect — session may have expired")
            else:
                print(f"  [AutoApply] Session active (page loaded)")

            # Click Apply button
            print(f"  [AutoApply] Looking for Apply button...")
            apply_clicked = _click_apply_button(page, ats_type)
            if apply_clicked:
                print(f"  [AutoApply] Clicked Apply button")
                page.wait_for_timeout(3000)
            else:
                print(f"  [AutoApply] No Apply button found")

        # Check if login is needed
        if _needs_login(page):
            print(f"  [AutoApply] Login required, attempting...")
            login_result = _handle_site_login(page, profile, creds, ats_type, url)

            if not login_result.get("success"):
                if HAS_CAPTCHA_SOLVER:
                    captcha_solved = _solve_captcha_if_needed(page, url)
                    if captcha_solved and "checkpoint" not in page.url.lower():
                        print(f"  [AutoApply] Captcha solved!")
                        try:
                            page.goto(url, timeout=20000, wait_until="domcontentloaded")
                            page.wait_for_timeout(3000)
                        except Exception:
                            pass
                    else:
                        if not is_persistent:
                            try: browser.close()
                            except: pass
                            if _pw_instance: _pw_instance.stop()
                        return {
                            "success": False,
                            "method": "login_failed",
                            "result": f"Login failed: {login_result.get('result', 'unknown')}. Set 2Captcha API key."
                        }
                else:
                    if not is_persistent:
                        try: browser.close()
                        except: pass
                        if _pw_instance: _pw_instance.stop()
                    return {
                        "success": False,
                        "method": "login_failed",
                        "result": f"Login failed: {login_result.get('result', 'unknown')}"
                    }
            else:
                print(f"  [AutoApply] Login successful!")
                page.wait_for_timeout(2000)
                if HAS_CAPTCHA_SOLVER:
                    _solve_captcha_if_needed(page, url)
                try:
                    page.goto(url, timeout=20000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)
                except Exception:
                    pass

        if HAS_CAPTCHA_SOLVER:
            _solve_captcha_if_needed(page, url)

        # Fill application form
        form_result = _fill_application_form(page, profile, job, cv_text, cover_text, ats_type)

        # Screenshot proof
        screenshot_dir = BASE_DIR / "data" / "proofs"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(screenshot_dir / f"apply_{job.get('id', 'unknown')}.png"), full_page=False, timeout=10000)
        except Exception:
            pass

        # Cleanup (only for non-persistent browser)
        if not is_persistent:
            try: browser.close()
            except: pass
            if _pw_instance:
                _pw_instance.stop()

        if form_result.get("success"):
            return {
                "success": True,
                "method": f"web_auto_{ats_type or 'form'}",
                "result": f"Auto-applied: {form_result.get('result', 'form submitted')}"
            }
        else:
            return {
                "success": False,
                "method": f"web_partial_{ats_type or 'form'}",
                "result": f"Form fill failed: {form_result.get('result', 'no fields found')}"
            }

    except Exception as e:
        if not is_persistent:
            try: browser.close()
            except: pass
            if _pw_instance:
                _pw_instance.stop()
        return {"success": False, "method": "web_error", "result": str(e)}
    finally:
        # Clean up temp profile and chromium proc (non-persistent only)
        if not is_persistent:
            if '_chromium_proc' in dir() or '_chromium_proc' in locals():
                try:
                    _chromium_proc.terminate()
                    _chromium_proc.wait(timeout=5)
                except:
                    try: _chromium_proc.kill()
                    except: pass
            if xvfb_proc:
                try:
                    xvfb_proc.terminate()
                    xvfb_proc.wait()
                except: pass
            if '_temp_profile' in dir() or '_temp_profile' in locals():
                try:
                    import shutil
                    shutil.rmtree(_temp_profile, ignore_errors=True)
                except: pass


def _detect_ats(url: str) -> str:
    """Detect which ATS system a URL uses."""
    url_lower = url.lower()
    if "greenhouse.io" in url_lower or "greenhouse" in url_lower:
        return "greenhouse"
    if "lever.co" in url_lower:
        return "lever"
    if "workable.com" in url_lower:
        return "workable"
    if "bamboohr" in url_lower:
        return "bamboohr"
    if "smartrecruiters" in url_lower:
        return "smartrecruiters"
    if "jobvite" in url_lower:
        return "jobvite"
    if "icims" in url_lower:
        return "icims"
    if "indeed" in url_lower:
        return "indeed"
    if "glassdoor" in url_lower:
        return "glassdoor"
    if "ziprecruiter" in url_lower:
        return "ziprecruiter"
    if "mustakbil" in url_lower:
        return "mustakbil"
    if "techjobs.pk" in url_lower:
        return "techjobs_pk"
    if "linkedin" in url_lower:
        return "linkedin"
    return ""


def _needs_login(page) -> bool:
    """Check if the current page requires login."""
    try:
        url_lower = page.url.lower()
        
        # For LinkedIn: check URL pattern first
        if "linkedin" in url_lower:
            # These URLs mean we're definitely on login page
            if "/uas/login" in url_lower or "/authwall" in url_lower:
                return True
            if "/login" in url_lower and "/jobs/" not in url_lower:
                return True
            # If we're on a job page, we're likely logged in (even if nav shows "Sign in")
            if "/jobs/view" in url_lower or "/jobs/search" in url_lower:
                return False
            # Check for join/signup pages
            if "join" in url_lower or "signup" in url_lower:
                return True
            # For feed/home — check URL didn't redirect to login
            if "/feed" in url_lower and "/uas/login" not in url_lower:
                return False
            if "/home" in url_lower and "/uas/login" not in url_lower:
                return False
        
        # General URL check
        if "/login" in url_lower or "/signin" in url_lower or "/authwall" in url_lower:
            return True
        
        # Check page content — but only use STRONG indicators, not nav bar text
        text = page.inner_text("body").lower()
        # Only first 300 chars — nav bar is usually at top and has "Sign in"
        text_top = text[:300]
        # Strong login indicators (forms, not nav links)
        strong_indicators = [
            "email or phone", "password", "forgot password",
            "please log in", "you need to log in",
            "continue with google", "sign in with email",
            "agree & join", "create account",
            # Hebrew
            "התחברות", "הצטרפות",
        ]
        # Only count as "needs login" if MULTIPLE strong indicators present
        matches = sum(1 for ind in strong_indicators if ind in text_top)
        return matches >= 2
    except Exception:
        return False


def _click_apply_button(page, ats_type: str) -> bool:
    """
    Try to find and click the 'Apply' button on a job page.
    LinkedIn job pages show an Apply button that triggers a login modal.
    Returns True if clicked.
    """
    # Language-agnostic selectors — match by common patterns
    apply_selectors = [
        # LinkedIn English
        "button:has-text('Apply')", "button:has-text('Apply Now')",
        "button:has-text('Apply now')", "button:has-text('Easy Apply')",
        "a:has-text('Apply')", "a:has-text('Apply Now')",
        "a:has-text('Easy Apply')",
        # Hebrew
        "button:has-text('להגיש מועמדות')", "a:has-text('להגיש מועמדות')",
        # General
        "button:has-text('Apply for this job')",
        "button:has-text('Submit application')",
        "a[href*='apply']", "button[class*='apply']",
    ]
    for sel in apply_selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            for i in range(count):
                if loc.nth(i).is_visible():
                    try:
                        loc.nth(i).click(timeout=5000)
                    except Exception:
                        # Modal overlay may intercept — try force click
                        loc.nth(i).click(timeout=5000, force=True)
                    page.wait_for_timeout(3000)
                    return True
        except Exception:
            continue
    return False


def _fill_visible_input(page, selectors: list, value: str) -> bool:
    """Fill the first VISIBLE input matching any of the selectors. Returns True if filled."""
    for sel in selectors:
        try:
            # Use locator to find visible elements
            loc = page.locator(sel)
            count = loc.count()
            for i in range(count):
                if loc.nth(i).is_visible():
                    loc.nth(i).fill(value, timeout=5000)
                    return True
        except Exception:
            continue
    return False


def _click_visible_button(page, selectors: list) -> bool:
    """Click the first VISIBLE button matching any of the selectors. Returns True if clicked."""
    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            for i in range(count):
                if loc.nth(i).is_visible():
                    loc.nth(i).click(timeout=5000)
                    return True
        except Exception:
            continue
    return False


def _handle_site_login(page, profile: dict, creds: dict, ats_type: str, url: str) -> dict:
    """
    Handle login on a job site using stored credentials.
    Strategy:
      1. If LinkedIn URL → login with LinkedIn email+password
      2. If site has Google/Gmail login button → login with Google
      3. If site has email+password login → use Gmail email+password
      4. If site has signup form → create account with Gmail email
    """
    try:
        gmail_email = creds.get("gmail_email", "") or profile.get("email", "")
        gmail_password = creds.get("gmail_password", "")
        linkedin_email = creds.get("linkedin_email", "")
        linkedin_password = creds.get("linkedin_password", "")

        # Check what login options are available on the page
        page_text = page.inner_text("body").lower()

        # --- Strategy 1: LinkedIn direct login ---
        if ats_type == "linkedin" or "linkedin" in url.lower():
            if not linkedin_email or not linkedin_password:
                return {"success": False, "result": "LinkedIn credentials not set. Add them in dashboard → Credentials."}
            
            # LinkedIn often shows an "authwall" (Join/Signup page) instead of login.
            # Look for "Sign in" link/button to switch to login form.
            print(f"  [LinkedIn] Current URL: {page.url[:80]}")
            
            # Check if we're on the authwall (signup page)
            page_text = page.inner_text("body").lower()
            if "join linkedin" in page_text or "agree & join" in page_text or "already on linkedin" in page_text:
                print(f"  [LinkedIn] Authwall detected, clicking Sign in link...")
                # Click "Sign in" link/button to switch to login form
                signin_selectors = [
                    "a:has-text('Sign in')", "button:has-text('Sign in')",
                    "a:has-text('sign in')", "button:has-text('sign in')",
                    ".authwall-join-form__form-toggle--bottom",
                    ".form-toggle--bottom",
                ]
                for sel in signin_selectors:
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0 and loc.first.is_visible():
                            loc.first.click(timeout=5000)
                            print(f"  [LinkedIn] Clicked Sign in toggle")
                            page.wait_for_timeout(3000)
                            break
                    except Exception:
                        continue
            
            # Also try clicking "Sign in" from top nav
            try:
                nav_signin = page.locator("a:has-text('Sign in')").first
                if nav_signin.is_visible():
                    nav_signin.click(timeout=5000)
                    page.wait_for_timeout(3000)
            except Exception:
                pass
            
            # Now try to login
            return _login_linkedin(page, linkedin_email, linkedin_password)

        # --- Strategy 2: Google/Gmail login button ---
        google_btn_selectors = [
            "button:has-text('Continue with Google')",
            "button:has-text('Sign in with Google')",
            "button:has-text('Google')",
            "a:has-text('Continue with Google')",
            "a:has-text('Sign in with Google')",
            "a:has-text('Google')",
            "[data-provider='google']",
            "button[aria-label*='Google']",
            # LinkedIn modal uses specific selectors
            "button:has-text('Continue with Google')",
            ".btn-primary:has-text('Google')",
        ]
        for sel in google_btn_selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    print(f"  [AutoApply] Found Google login button, clicking...")
                    loc.first.click(timeout=5000)
                    page.wait_for_timeout(4000)
                    # Handle Google OAuth — could be popup or redirect
                    return _login_google(page, gmail_email, gmail_password)
            except Exception:
                continue

        # --- Strategy 3: LinkedIn login button ---
        linkedin_btn_selectors = [
            "button:has-text('Continue with LinkedIn')",
            "button:has-text('Sign in with LinkedIn')",
            "button:has-text('LinkedIn')",
            "a:has-text('Continue with LinkedIn')",
            "a:has-text('Sign in with LinkedIn')",
            "a:has-text('LinkedIn')",
            "[data-provider='linkedin']",
            "button[aria-label*='LinkedIn']",
        ]
        for sel in linkedin_btn_selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    if not linkedin_email or not linkedin_password:
                        return {"success": False, "result": "LinkedIn credentials not set. Add them in dashboard → Credentials."}
                    print(f"  [AutoApply] Found LinkedIn login button, clicking...")
                    loc.first.click(timeout=5000)
                    page.wait_for_timeout(4000)
                    return _login_linkedin(page, linkedin_email, linkedin_password)
            except Exception:
                continue

        # --- Strategy 4: Email + password login form ---
        if gmail_email and gmail_password:
            # Try to find visible email and password fields
            email_selectors = ["input[type='email']", "input[name='email']", "input[name='username']",
                             "input[id*='email']", "input[id*='user']", "input[placeholder*='email']",
                             "input[placeholder*='Email']", "input[autocomplete='username']"]
            email_filled = _fill_visible_input(page, email_selectors, gmail_email)

            password_selectors = ["input[type='password']", "input[name='password']",
                                 "input[id*='password']", "input[placeholder*='password']",
                                 "input[placeholder*='Password']", "input[autocomplete='current-password']"]
            password_filled = _fill_visible_input(page, password_selectors, gmail_password)

            if email_filled and password_filled:
                print(f"  [AutoApply] Filled email+password, submitting...")
                # Submit login form
                submit_selectors = [
                    "button:has-text('Sign in')", "button:has-text('Log in')", "button:has-text('Login')",
                    "button[type='submit']", "input[type='submit']",
                    "button:has-text('Continue')", "button:has-text('Sign In')",
                ]
                _click_visible_button(page, submit_selectors)
                page.wait_for_timeout(5000)

                # Check if login succeeded
                if not _needs_login(page):
                    return {"success": True, "result": "Logged in with email+password"}
                return {"success": False, "result": "Email+password login failed (wrong credentials or captcha)"}
            elif email_filled or password_filled:
                print(f"  [AutoApply] Partial form fill (email={email_filled}, pwd={password_filled})")

        # --- Strategy 5: Signup form ---
        if gmail_email:
            return _handle_signup(page, profile, creds)

        return {"success": False, "result": "No credentials set. Add Gmail/LinkedIn credentials in dashboard → Credentials."}

    except Exception as e:
        return {"success": False, "result": str(e)}


def _login_google(page, email: str, password: str) -> dict:
    """Handle Google OAuth login flow (email → password → submit)."""
    try:
        if not email or not password:
            return {"success": False, "result": "Gmail credentials not set. Add them in dashboard → Credentials."}

        # Wait for Google login page to load
        page.wait_for_timeout(4000)

        # Check if we're on Google's login page or in a popup
        current_page = page
        google_page = None
        try:
            pages = page.context.pages
            for p in pages:
                if "google" in p.url.lower() or "accounts.google" in p.url.lower():
                    google_page = p
                    break
        except Exception:
            pass

        if google_page:
            current_page = google_page
            print(f"  [Google] Using popup page: {google_page.url[:60]}")

        # Step 1: Enter email — use wait_for_selector for reliability
        email_selectors = [
            "input[type='email']", "input[name='identifier']", "input[id='identifierId']",
            "input[name='email']", "input[autocomplete='username']",
        ]
        email_entered = False
        for sel in email_selectors:
            try:
                loc = current_page.locator(sel)
                count = loc.count()
                for i in range(count):
                    if loc.nth(i).is_visible():
                        loc.nth(i).wait_for(state="visible", timeout=5000)
                        loc.nth(i).fill(email, timeout=5000)
                        email_entered = True
                        break
                if email_entered:
                    break
            except Exception:
                continue

        if not email_entered:
            return {"success": False, "result": "Could not find Google email input field"}

        # Click Next
        next_selectors = [
            "#identifierNext", "div[id='identifierNext']",
            "button:has-text('Next')", "button:has-text('Continue')",
            "button[type='button']:has-text('Next')",
        ]
        _click_visible_button(current_page, next_selectors)
        current_page.wait_for_timeout(5000)

        # Step 2: Enter password — wait for it to appear
        password_selectors = [
            "input[type='password']", "input[name='password']",
            "input[name='Passwd']", "input[autocomplete='current-password']",
        ]
        password_entered = False
        for sel in password_selectors:
            try:
                loc = current_page.locator(sel)
                count = loc.count()
                for i in range(count):
                    if loc.nth(i).is_visible():
                        loc.nth(i).wait_for(state="visible", timeout=5000)
                        loc.nth(i).fill(password, timeout=5000)
                        password_entered = True
                        break
                if password_entered:
                    break
            except Exception:
                continue

        if not password_entered:
            return {"success": False, "result": "Could not find Google password field (may need 2FA or captcha)"}

        # Click Next/Sign in
        for sel in ["#passwordNext", "div[id='passwordNext']", "button:has-text('Sign in')", "button:has-text('Next')"]:
            try:
                loc = current_page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=5000)
                    current_page.wait_for_timeout(6000)
                    break
            except Exception:
                continue

        # Check for consent/allow access page
        try:
            for sel in ["button:has-text('Allow')", "button:has-text('Accept')", "button:has-text('Continue')", "button:has-text('I agree')"]:
                loc = current_page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=5000)
                    current_page.wait_for_timeout(4000)
                    break
        except Exception:
            pass

        # Check if login succeeded
        current_page.wait_for_timeout(3000)
        current_url = current_page.url
        if "google" not in current_url.lower() or "myaccount" in current_url.lower() or "consent" in current_url.lower():
            return {"success": True, "result": "Google login successful"}

        # Check for 2FA
        page_text = current_page.inner_text("body").lower()
        if "2-step" in page_text or "verification" in page_text or "authenticator" in page_text:
            return {"success": False, "result": "Google requires 2FA verification — cannot auto-login"}

        return {"success": False, "result": "Google login may have failed (2FA, captcha, or wrong password)"}

    except Exception as e:
        return {"success": False, "result": f"Google login error: {e}"}


def _login_linkedin(page, email: str, password: str) -> dict:
    """Handle LinkedIn login flow — handles LinkedIn's new UI with hidden duplicate inputs."""
    try:
        if not email or not password:
            return {"success": False, "result": "LinkedIn credentials not set. Add them in dashboard → Credentials."}

        # Wait for page to be ready
        page.wait_for_timeout(3000)

        # Check if we're on a login page. If not, navigate to LinkedIn login directly.
        current_url = page.url.lower()
        page_text = page.inner_text("body").lower()
        
        if "/login" not in current_url and "sign in" not in page_text[:200] and "email" not in page_text[:200]:
            # We're not on a login page — navigate to login
            print(f"  [LinkedIn] Not on login page ({current_url[:60]}), navigating to login...")
            page.goto("https://www.linkedin.com/login", timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

        # LinkedIn has TWO sets of email/password inputs — first set is hidden, second is visible.
        # Use locator with .last or filter for visible ones.
        
        # Step 1: Enter email — find the VISIBLE email input
        email_selectors = [
            "input[name='session_key']", "input[id='username']",
            "input[type='email']", "input[name='email']",
            "input[autocomplete='username']",
        ]
        email_entered = False
        for sel in email_selectors:
            try:
                loc = page.locator(sel)
                count = loc.count()
                for i in range(count):
                    if loc.nth(i).is_visible():
                        print(f"  [LinkedIn] Filling email in visible input #{i} (selector: {sel})")
                        loc.nth(i).fill(email, timeout=8000)
                        email_entered = True
                        break
                if email_entered:
                    break
            except Exception:
                continue

        if not email_entered:
            # Last resort: try typing into the last input
            try:
                loc = page.locator("input[type='email']").last
                loc.wait_for(state="visible", timeout=8000)
                loc.fill(email, timeout=8000)
                email_entered = True
                print(f"  [LinkedIn] Filled email via .last selector")
            except Exception:
                return {"success": False, "result": "Could not find LinkedIn email input"}

        # Step 2: Enter password — find the VISIBLE password input
        password_selectors = [
            "input[name='session_password']", "input[id='password']",
            "input[type='password']", "input[autocomplete='current-password']",
        ]
        password_entered = False
        for sel in password_selectors:
            try:
                loc = page.locator(sel)
                count = loc.count()
                for i in range(count):
                    if loc.nth(i).is_visible():
                        print(f"  [LinkedIn] Filling password in visible input #{i}")
                        loc.nth(i).fill(password, timeout=8000)
                        password_entered = True
                        break
                if password_entered:
                    break
            except Exception:
                continue

        if not password_entered:
            try:
                loc = page.locator("input[type='password']").last
                loc.wait_for(state="visible", timeout=8000)
                loc.fill(password, timeout=8000)
                password_entered = True
                print(f"  [LinkedIn] Filled password via .last selector")
            except Exception:
                return {"success": False, "result": "Could not find LinkedIn password input"}

        # Step 3: Click Sign in
        print(f"  [LinkedIn] Submitting login form...")
        sign_in_selectors = [
            "button[data-id='sign-in-form__form-submit-button']",
            "button:has-text('Sign in')",
            "button[type='submit']",
            "button:has-text('Log in')",
        ]
        clicked = False
        for sel in sign_in_selectors:
            try:
                loc = page.locator(sel)
                count = loc.count()
                for i in range(count):
                    if loc.nth(i).is_visible():
                        loc.nth(i).click(timeout=8000)
                        clicked = True
                        break
                if clicked:
                    break
            except Exception:
                continue

        if not clicked:
            return {"success": False, "result": "Could not find LinkedIn Sign in button"}

        # Wait for navigation
        page.wait_for_timeout(8000)

        # Check if login succeeded
        current_url = page.url.lower()
        print(f"  [LinkedIn] After login URL: {page.url[:80]}")

        # LinkedIn redirects to feed or dashboard on success
        if "/login" not in current_url and "/signin" not in current_url and "/checkpoint" not in current_url:
            # Double check — are we still on a login form?
            page_text = page.inner_text("body").lower()
            if "sign in" in page_text and "email or phone" in page_text:
                # Still on login page
                pass
            else:
                return {"success": True, "result": "LinkedIn login successful"}

        # Check for 2FA / security challenge
        page_text = page.inner_text("body").lower()
        if "verification" in page_text or "two-factor" in page_text or "authenticator" in page_text:
            return {"success": False, "result": "LinkedIn requires 2FA verification — cannot auto-login"}
        if "checkpoint" in current_url or "challenge" in current_url:
            return {"success": False, "result": "LinkedIn security challenge (captcha/verification) — cannot auto-login"}

        # Check if we see "Enter your password" — means email was accepted but password page is separate
        if "enter your password" in page_text or "password" in page_text and "email" not in page_text:
            # Maybe password page is separate — try filling password again
            print(f"  [LinkedIn] Seems like separate password page, retrying...")
            try:
                pwd_loc = page.locator("input[type='password']").last
                if pwd_loc.is_visible():
                    pwd_loc.fill(password, timeout=5000)
                    _click_visible_button(page, sign_in_selectors)
                    page.wait_for_timeout(8000)
                    if "/login" not in page.url.lower() and "/signin" not in page.url.lower():
                        return {"success": True, "result": "LinkedIn login successful (2-step)"}
            except Exception:
                pass

        return {"success": False, "result": "LinkedIn login failed (wrong credentials, captcha, or new page format)"}

    except Exception as e:
        return {"success": False, "result": f"LinkedIn login error: {e}"}


def _handle_signup(page, profile: dict, creds: dict) -> dict:
    """Handle signup/registration on a job site."""
    try:
        email = creds.get("gmail_email", "") or profile.get("email", "")
        password = creds.get("gmail_password", "")
        name = profile.get("name", "")
        phone = profile.get("phone", "")
        name_parts = name.split()
        first_name = name_parts[0] if name_parts else ""
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        # Try to find signup/register link
        signup_selectors = [
            "a:has-text('Sign up')", "a:has-text('Register')", "a:has-text('Create account')",
            "a:has-text('Sign Up')", "a:has-text('sign up')", "a:has-text('Join now')",
            "a:has-text('Join Now')",
            "a[href*='register']", "a[href*='signup']", "a[href*='sign-up']",
            "a[href*='join']",
        ]
        signup_clicked = False
        for sel in signup_selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=5000)
                    page.wait_for_timeout(4000)
                    signup_clicked = True
                    break
            except Exception:
                continue

        # Fill signup form fields using _fill_visible_input
        field_mappings = [
            (["input[name='email']", "input[type='email']", "input[placeholder*='email']", "input[placeholder*='Email']"], email),
            (["input[name='first_name']", "input[name='firstName']", "input[id*='first']", "input[placeholder*='First']"], first_name),
            (["input[name='last_name']", "input[name='lastName']", "input[id*='last']", "input[placeholder*='Last']"], last_name),
            (["input[name='name']", "input[name='full_name']", "input[id*='name']", "input[placeholder*='Name']", "input[placeholder*='Full']"], name),
            (["input[name='phone']", "input[name='phone_number']", "input[name='mobile']", "input[type='tel']", "input[placeholder*='phone']", "input[placeholder*='Phone']"], phone),
            (["input[name='password']", "input[type='password']", "input[placeholder*='password']", "input[placeholder*='Password']"], password),
            (["input[name='confirm_password']", "input[name='password_confirmation']", "input[id*='confirm']", "input[placeholder*='confirm']"], password),
        ]

        filled_count = 0
        for selectors, value in field_mappings:
            if value and _fill_visible_input(page, selectors, value):
                filled_count += 1

        if filled_count == 0:
            return {"success": False, "result": "No signup form fields found to fill"}

        # Submit the form
        submit_selectors = [
            "button:has-text('Sign up')", "button:has-text('Register')",
            "button:has-text('Create')", "button:has-text('Continue')",
            "button:has-text('Join')", "button[type='submit']", "input[type='submit']",
        ]
        _click_visible_button(page, submit_selectors)
        page.wait_for_timeout(5000)

        # Check if signup succeeded
        if not _needs_login(page):
            return {"success": True, "result": "Account created and logged in"}

        return {"success": False, "result": "Signup may have failed (captcha, email verification needed, or form changed)"}

    except Exception as e:
        return {"success": False, "result": str(e)}


def _fill_application_form(page, profile: dict, job: dict, cv_text: str, cover_text: str, ats_type: str = "") -> dict:
    """
    Fill out a job application form using Playwright.
    Handles common form fields and ATS-specific layouts.
    """
    try:
        email = profile.get("email", "")
        name = profile.get("name", "")
        phone = profile.get("phone", "")
        name_parts = name.split()
        first_name = name_parts[0] if name_parts else ""
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
        location = profile.get("location", "")
        linkedin = profile.get("linkedin", "")
        github = profile.get("github", "")

        filled = 0

        # Common form field selectors across ATS systems
        field_mappings = {
            # Email
            "input[name='email']": email, "input[type='email']": email,
            "input[name='email_address']": email, "input[id*='email']": email,
            # Name fields
            "input[name='first_name']": first_name, "input[name='last_name']": last_name,
            "input[name='firstName']": first_name, "input[name='lastName']": last_name,
            "input[name='first']": first_name, "input[name='last']": last_name,
            "input[name='full_name']": name, "input[name='name']": name,
            "input[name='applicant_name']": name,
            "input[id*='first']": first_name, "input[id*='last']": last_name,
            # Phone
            "input[name='phone']": phone, "input[name='phone_number']": phone,
            "input[name='mobile']": phone, "input[type='tel']": phone,
            "input[id*='phone']": phone,
            # Location
            "input[name='location']": location, "input[name='city']": location,
            "input[name='address']": location, "input[id*='location']": location,
            # LinkedIn
            "input[name='linkedin']": linkedin, "input[name='linkedin_url']": linkedin,
            "input[id*='linkedin']": linkedin, "input[placeholder*='LinkedIn']": linkedin,
            # GitHub
            "input[name='github']": github, "input[name='github_url']": github,
            "input[id*='github']": github, "input[placeholder*='GitHub']": github,
            # Cover letter
            "textarea[name='cover_letter']": cover_text, "textarea[name='message']": cover_text,
            "textarea[name='notes']": cover_text, "textarea[id*='cover']": cover_text,
            "textarea[placeholder*='cover']": cover_text, "textarea[placeholder*='message']": cover_text,
            # Resume/CV text
            "textarea[name='resume']": cv_text[:3000], "textarea[name='cv']": cv_text[:3000],
        }

        for selector, value in field_mappings.items():
            try:
                elem = page.query_selector(selector)
                if elem and value and elem.is_visible():
                    current = elem.input_value() if elem.get_attribute("type") != "file" else ""
                    if not current:
                        elem.fill(value)
                        filled += 1
            except Exception:
                continue

        # Handle file upload for CV (if we have a primary CV file)
        try:
            from cv_manager import get_primary_cv
            primary_cv = get_primary_cv()
            if primary_cv and primary_cv.get("file_path"):
                file_inputs = page.query_selector_all("input[type='file']")
                for fi in file_inputs:
                    try:
                        if fi.is_visible():
                            fi.set_input_files(primary_cv["file_path"])
                            filled += 1
                            print(f"  [AutoApply] Uploaded CV file: {primary_cv['original_filename']}")
                            break
                    except Exception:
                        continue
        except Exception:
            pass

        # Handle dropdown selections (experience, job type, etc.)
        try:
            # Look for select elements
            selects = page.query_selector_all("select")
            for sel in selects:
                try:
                    select_name = (sel.get_attribute("name") or "").lower()
                    options = sel.query_selector_all("option")
                    option_texts = [o.inner_text().strip().lower() for o in options]

                    if any(k in select_name for k in ["experience", "years"]):
                        # Try to select "3-5 years" or similar
                        for opt_text in ["3-5 years", "3+ years", "2-4 years", "1-3 years"]:
                            for i, ot in enumerate(option_texts):
                                if opt_text in ot:
                                    sel.select_option(index=i)
                                    filled += 1
                                    break
                            else:
                                continue
                            break

                    elif any(k in select_name for k in ["country", "location", "region"]):
                        for opt_text in ["pakistan", "asia"]:
                            for i, ot in enumerate(option_texts):
                                if opt_text in ot:
                                    sel.select_option(index=i)
                                    filled += 1
                                    break
                            else:
                                continue
                            break

                except Exception:
                    continue
        except Exception:
            pass

        # Handle checkbox for consent/terms
        try:
            checkboxes = page.query_selector_all("input[type='checkbox']")
            for cb in checkboxes:
                try:
                    if not cb.is_checked() and cb.is_visible():
                        cb.check()
                        filled += 1
                except Exception:
                    continue
        except Exception:
            pass

        print(f"  [AutoApply] Filled {filled} form fields")

        if filled == 0:
            return {"success": False, "result": "No form fields found — may need manual apply"}

        # Try to submit
        submit_selectors = [
            "button:has-text('Submit')", "button:has-text('Apply')", "button:has-text('Send')",
            "button:has-text('Submit Application')", "button[type='submit']",
            "input[type='submit']", "button:has-text('Continue')",
        ]
        submitted = False
        for sel in submit_selectors:
            try:
                elem = page.query_selector(sel)
                if elem and elem.is_visible():
                    elem.click()
                    page.wait_for_timeout(5000)
                    submitted = True
                    break
            except Exception:
                continue

        if submitted:
            return {"success": True, "result": f"Form submitted ({filled} fields filled)"}
        else:
            return {"success": False, "result": f"Form filled but no submit button found ({filled} fields)"}

    except Exception as e:
        return {"success": False, "result": str(e)}


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
    Run one monitoring cycle — check all sources for NEW jobs.
    If a new matching job is found, apply IMMEDIATELY (don't wait).
    
    This is a real-time monitor, not a batch job. It runs every 2-3 minutes
    so the agent is among the FIRST to apply to new postings.
    """
    profile = get_profile()
    if not profile:
        log_activity("agent_cycle", "No profile set up. Skipping cycle.", level="warning")
        return {"error": "no_profile"}

    update_agent_state({"running": 1, "last_run": datetime.now().isoformat()})

    config = get_config()
    max_per_source = config.get("max_per_source", 30)
    auto_apply_threshold = profile.get("auto_apply_threshold", 50)
    max_applications_per_cycle = config.get("max_applications_per_cycle", 10)
    max_age_days = config.get("max_age_days", 1)  # Default: only last 1 day (real-time!)

    # 1. DISCOVER — check all sources for fresh jobs
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scanning all sources...")
    log_activity("scan_start", f"Scanning all job sources (jobs from last {max_age_days} day(s) only)")
    try:
        jobs = scrape_all_jobs(profile, max_per_source=max_per_source, max_age_days=max_age_days)
    except Exception as e:
        log_activity("discover_error", str(e), level="error")
        update_agent_state({"running": 0})
        return {"error": str(e)}

    # Filter out already-seen jobs — only NEW postings matter
    db = get_db()
    seen_ids = {row["id"] for row in db.execute("SELECT id FROM seen_jobs").fetchall()}
    db.close()

    new_jobs = [j for j in jobs if j.id not in seen_ids]

    if not new_jobs:
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] No new jobs ({len(jobs)} already seen)")
        update_agent_state({
            "running": 0,
            "next_run": (datetime.now() + timedelta(minutes=config.get("run_interval_hours", 0.05) * 60)).isoformat()
        })
        return {"discovered": len(jobs), "new": 0, "applied": 0, "notified": 0}

    print(f"  [{datetime.now().strftime('%H:%M:%S')}] Found {len(new_jobs)} NEW job(s)! Evaluating...")

    # 2. EVALUATE + APPLY — process each new job immediately
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

        # Fast keyword-based evaluation (no AI — saves API calls for CV generation)
        evaluation = evaluate_job(job.to_dict(), profile)
        store_job(job, evaluation)

        # 3. AUTO-APPLY IMMEDIATELY if it matches
        if evaluation.should_auto_apply and applied_count < max_applications_per_cycle:
            print(f"  >>> APPLYING NOW: {job.title} at {job.company} (score: {evaluation.overall_score})")
            result = auto_apply_to_job(job.to_dict(), profile, evaluation)
            applied_count += 1

            # 4. NOTIFY immediately
            notify_msg = (
                f"Applied to: {job.title} at {job.company}\n"
                f"  Score: {evaluation.overall_score}/100 ({evaluation.verdict})\n"
                f"  Source: {job.source}\n"
                f"  Method: {result['method']}\n"
                f"  URL: {job.url}"
            )
            notify("job_applied", notify_msg)
            notified_count += 1
        else:
            if not evaluation.should_auto_apply:
                mark_job_status(job.id, "evaluated")

    # 5. UPDATE STATE
    summary = (
        f"Scanned: {len(jobs)} | New: {len(new_jobs)} | "
        f"Applied: {applied_count} | Notified: {notified_count}"
    )
    print(f"  {summary}")

    state = get_agent_state()
    interval_min = config.get("run_interval_hours", 0.05) * 60  # convert hours to minutes
    update_agent_state({
        "running": 0,
        "total_discovered": state.get("total_discovered", 0) + len(jobs),
        "total_evaluated": state.get("total_evaluated", 0) + len(new_jobs),
        "total_applied": state.get("total_applied", 0) + applied_count,
        "total_notified": state.get("total_notified", 0) + notified_count,
        "next_run": (datetime.now() + timedelta(minutes=interval_min)).isoformat()
    })

    log_activity("agent_cycle_complete", summary)

    return {
        "discovered": len(jobs),
        "new": len(new_jobs),
        "applied": applied_count,
        "notified": notified_count,
    }


def run_agent_loop(interval_minutes: float = 2):
    """
    Run the agent as a REAL-TIME MONITOR.
    Checks all job sources every `interval_minutes` minutes (default: 2).
    When a new matching job is found, it applies IMMEDIATELY — no waiting.
    
    This ensures the agent is among the FIRST to apply to new postings,
    giving a huge advantage over candidates who apply hours later.
    """
    print(f"[AGENT] Starting REAL-TIME MONITOR (checking every {interval_minutes} min)")
    print(f"[AGENT] New matching jobs will be applied to INSTANTLY")

    init_db()

    while True:
        try:
            run_agent_cycle()
        except Exception as e:
            log_activity("agent_error", str(e), level="error")
            print(f"[AGENT] Error: {e}")

        print(f"  Next scan in {interval_minutes} minutes...")
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        # Run a single scan cycle
        init_db()
        result = run_agent_cycle()
        print(json.dumps(result, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "--loop":
        # Run real-time monitor (interval in minutes, default 2)
        interval = float(sys.argv[2]) if len(sys.argv) > 2 else 2
        run_agent_loop(interval)
    else:
        print("Usage: python agent.py [--once | --loop <minutes>]")
        print("  --once         Run a single scan cycle")
        print("  --loop N       Run real-time monitor, checking every N minutes (default: 2)")
        print("")
        print("The agent checks all job sources every N minutes.")
        print("When a new matching job is found, it applies IMMEDIATELY.")
