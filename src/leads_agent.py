"""
Autonomous Leads Agent — discovers startup founders, entrepreneurs, and
freelancer clients, evaluates them, generates personalized proposals, and
sends cold outreach (emails + DMs).

This is the LEADS counterpart to the jobs agent. It runs on its own loop:
  1. DISCOVER — Find leads from multiple sources (YC, ProductHunt, GitHub, etc.)
  2. ENRICH — Fetch lead pages, extract emails, LinkedIn, Twitter
  3. EVALUATE — AI scores lead fit (0-100) based on user's profile/services
  4. GENERATE — Create personalized cold email / DM / proposal
  5. OUTREACH — Send email (SMTP or Gmail browser) or LinkedIn DM
  6. TRACK — Record everything in database

Usage:
  python leads_agent.py --once          Run a single discovery + outreach cycle
  python leads_agent.py --loop 30       Run loop, checking every 30 minutes
"""

import json
import sqlite3
import time
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import asdict

from leads_sources import discover_all_leads, enrich_lead, Lead
from proposal_generator import (
    generate_cold_email, generate_linkedin_dm, generate_twitter_dm,
    generate_full_proposal, evaluate_lead_fit
)
from outreach_engine import send_outreach, get_outreach_capability
import ai_engine  # noqa — ensures ai_engine is importable

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "jobagent.db"
DATA_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Database (adds leads tables to the existing jobagent.db)
# ---------------------------------------------------------------------------

def get_db():
    db = sqlite3.connect(str(DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA busy_timeout = 30000")
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_leads_db():
    """Initialize leads tables in the shared database."""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id TEXT PRIMARY KEY,
            name TEXT,
            title TEXT,
            company TEXT,
            company_url TEXT,
            company_description TEXT,
            industry TEXT,
            stage TEXT,
            email TEXT,
            linkedin TEXT,
            twitter TEXT,
            website TEXT,
            source TEXT,
            source_url TEXT,
            lead_type TEXT,
            review_text TEXT,
            review_rating REAL DEFAULT 0,
            review_count INTEGER DEFAULT 0,
            freelancer_platform TEXT,
            description TEXT,
            location TEXT,
            first_seen TEXT,
            fit_score REAL DEFAULT 0,
            status TEXT DEFAULT 'new',
            contact_method TEXT,
            enriched INTEGER DEFAULT 0,
            evaluated INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS outreach_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id TEXT NOT NULL,
            message_type TEXT,
            subject TEXT,
            content TEXT,
            proposal_text TEXT,
            status TEXT DEFAULT 'draft',
            sent_at TEXT,
            result TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );

        CREATE TABLE IF NOT EXISTS leads_state (
            id INTEGER PRIMARY KEY DEFAULT 1,
            last_run TEXT,
            next_run TEXT,
            running INTEGER DEFAULT 0,
            total_discovered INTEGER DEFAULT 0,
            total_enriched INTEGER DEFAULT 0,
            total_evaluated INTEGER DEFAULT 0,
            total_outreached INTEGER DEFAULT 0,
            total_replied INTEGER DEFAULT 0,
            config TEXT
        );

        CREATE TABLE IF NOT EXISTS seen_leads (
            id TEXT PRIMARY KEY,
            first_seen TEXT
        );
    """)

    db.execute("""
        INSERT OR IGNORE INTO leads_state (id, last_run, next_run, config)
        VALUES (1, '', '', '{}')
    """)
    db.commit()
    db.close()


def get_profile():
    db = get_db()
    row = db.execute("SELECT data FROM profile WHERE id = 1").fetchone()
    db.close()
    return json.loads(row["data"]) if row else {}


def get_leads_config() -> dict:
    db = get_db()
    row = db.execute("SELECT config FROM leads_state WHERE id = 1").fetchone()
    db.close()
    return json.loads(row["config"]) if row and row["config"] else {}


def update_leads_config(config: dict):
    db = get_db()
    db.execute("UPDATE leads_state SET config = ? WHERE id = 1", (json.dumps(config),))
    db.commit()
    db.close()


def get_leads_state() -> dict:
    db = get_db()
    row = db.execute("SELECT * FROM leads_state WHERE id = 1").fetchone()
    db.close()
    return dict(row) if row else {}


def update_leads_state(updates: dict):
    db = get_db()
    sets = ", ".join(f"{k} = ?" for k in updates.keys())
    vals = list(updates.values())
    db.execute(f"UPDATE leads_state SET {sets} WHERE id = 1", vals)
    db.commit()
    db.close()


def log_activity(event: str, details: str = "", lead_id: str = "", level: str = "info"):
    """Log to the same activity_log table used by the jobs agent."""
    db = get_db()
    db.execute(
        "INSERT INTO activity_log (timestamp, event, details, job_id, level) VALUES (?, ?, ?, ?, ?)",
        (datetime.now().isoformat(), event, details, lead_id, level)
    )
    db.commit()
    db.close()
    print(f"[LEADS {level.upper()}] {event}: {details}")


# ---------------------------------------------------------------------------
# Lead storage
# ---------------------------------------------------------------------------

def save_lead(lead: Lead):
    """Insert or replace a lead in the database."""
    db = get_db()
    d = asdict(lead)
    cols = ", ".join(d.keys())
    placeholders = ", ".join(["?"] * len(d))
    db.execute(
        f"INSERT OR REPLACE INTO leads ({cols}) VALUES ({placeholders})",
        list(d.values())
    )
    db.commit()
    db.close()


def get_lead(lead_id: str) -> dict:
    db = get_db()
    row = db.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    db.close()
    return dict(row) if row else None


def get_all_leads(limit: int = 100, status: str = None) -> list:
    db = get_db()
    if status:
        rows = db.execute(
            "SELECT * FROM leads WHERE status = ? ORDER BY fit_score DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM leads ORDER BY fit_score DESC LIMIT ?", (limit,)
        ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_new_leads(limit: int = 50) -> list:
    """Get leads that haven't been enriched/evaluated yet."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM leads WHERE enriched = 0 ORDER BY first_seen DESC LIMIT ?",
        (limit,)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_leads_for_outreach(limit: int = 20) -> list:
    """Get leads that have been evaluated with good fit score but not contacted."""
    db = get_db()
    rows = db.execute(
        """SELECT * FROM leads
           WHERE evaluated = 1 AND fit_score >= ? AND status = 'new'
           ORDER BY fit_score DESC LIMIT ?""",
        (get_leads_config().get("outreach_threshold", 50), limit)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def save_outreach_message(lead_id: str, message_type: str, subject: str,
                          content: str, proposal_text: str = "",
                          status: str = "draft", result: str = "") -> int:
    db = get_db()
    cursor = db.execute(
        """INSERT INTO outreach_messages
           (lead_id, message_type, subject, content, proposal_text, status, result, sent_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (lead_id, message_type, subject, content, proposal_text, status, result,
         datetime.now().isoformat() if status == "sent" else "")
    )
    db.commit()
    msg_id = cursor.lastrowid
    db.close()
    return msg_id


def update_outreach_message(msg_id: int, status: str, result: str = ""):
    db = get_db()
    db.execute(
        "UPDATE outreach_messages SET status = ?, result = ?, sent_at = ? WHERE id = ?",
        (status, result, datetime.now().isoformat() if status == "sent" else "", msg_id)
    )
    db.commit()
    db.close()


def get_outreach_messages(lead_id: str = None, limit: int = 50) -> list:
    db = get_db()
    if lead_id:
        rows = db.execute(
            "SELECT * FROM outreach_messages WHERE lead_id = ? ORDER BY id DESC LIMIT ?",
            (lead_id, limit)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM outreach_messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def update_lead_status(lead_id: str, status: str):
    db = get_db()
    db.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Agent cycle
# ---------------------------------------------------------------------------

def run_leads_cycle():
    """Run one complete leads discovery + outreach cycle."""
    profile = get_profile()
    if not profile:
        log_activity("leads_cycle", "No profile set up. Skipping leads cycle.", level="warning")
        return {"discovered": 0, "enriched": 0, "evaluated": 0, "outreached": 0}

    config = get_leads_config()
    max_per_source = config.get("max_per_source", 20)
    keywords = config.get("search_keywords", "startup saas platform app")
    do_outreach = config.get("auto_outreach", False)

    update_leads_state({"running": 1, "last_run": datetime.now().isoformat()})

    # --- Phase 1: Discover ---
    log_activity("leads_discovery_start", f"Discovering leads (keywords: {keywords})")
    leads = discover_all_leads(max_per_source=max_per_source, keywords=keywords)

    new_count = 0
    db = get_db()
    for lead in leads:
        # Check if already seen
        existing = db.execute("SELECT id FROM leads WHERE id = ?", (lead.id,)).fetchone()
        if existing:
            continue
        # Check seen_leads
        seen = db.execute("SELECT id FROM seen_leads WHERE id = ?", (lead.id,)).fetchone()
        if seen:
            continue
        db.execute("INSERT OR IGNORE INTO seen_leads (id, first_seen) VALUES (?, ?)",
                   (lead.id, lead.first_seen))
        lead.status = "new"
        # Save lead using same connection (avoid nested connection lock)
        d = asdict(lead)
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        db.execute(
            f"INSERT OR REPLACE INTO leads ({cols}) VALUES ({placeholders})",
            list(d.values())
        )
        new_count += 1
    db.commit()
    db.close()

    log_activity("leads_discovery", f"Discovered {len(leads)} leads ({new_count} new)")

    # --- Phase 2: Enrich ---
    log_activity("leads_enrichment_start", f"Enriching {new_count} new leads...")
    enriched_count = 0
    new_leads = get_new_leads(limit=50)
    for lead in new_leads:
        try:
            lead_obj = Lead(**{k: lead[k] for k in Lead.__dataclass_fields__ if k in lead})
            enriched = enrich_lead(lead_obj)
            db = get_db()
            db.execute("""
                UPDATE leads SET email=?, linkedin=?, twitter=?, website=?,
                company_description=?, contact_method=?, enriched=1 WHERE id=?
            """, (enriched.email, enriched.linkedin, enriched.twitter,
                  enriched.website, enriched.company_description,
                  enriched.contact_method, enriched.id))
            db.commit()
            db.close()
            enriched_count += 1
            time.sleep(1)  # Rate limit
        except Exception as e:
            log_activity("leads_enrichment_error", f"Error enriching {lead.get('name','')}: {e}", level="error")

    log_activity("leads_enrichment", f"Enriched {enriched_count} leads")

    # --- Phase 3: Evaluate ---
    log_activity("leads_evaluation_start", f"Evaluating {enriched_count} leads...")
    evaluated_count = 0
    db = get_db()
    unevaluated = db.execute(
        "SELECT * FROM leads WHERE enriched=1 AND evaluated=0 LIMIT 50"
    ).fetchall()
    db.close()

    for row in unevaluated:
        lead = dict(row)
        try:
            evaluation = evaluate_lead_fit(profile, lead)
            if evaluation:
                db = get_db()
                db.execute(
                    "UPDATE leads SET fit_score=?, evaluated=1 WHERE id=?",
                    (evaluation.get("fit_score", 0), lead["id"])
                )
                db.commit()
                db.close()
                evaluated_count += 1
                time.sleep(2)  # AI rate limit
            else:
                db = get_db()
                db.execute("UPDATE leads SET evaluated=1 WHERE id=?", (lead["id"],))
                db.commit()
                db.close()
        except Exception as e:
            log_activity("leads_evaluation_error", f"Error evaluating {lead.get('name','')}: {e}", level="error")

    log_activity("leads_evaluation", f"Evaluated {evaluated_count} leads")

    # --- Phase 4: Outreach ---
    # NO LinkedIn DMs — LinkedIn restricts automated DMs and can ban accounts.
    # Outreach channels: email (via Gmail browser), website forms, Twitter DMs
    outreached_count = 0
    if do_outreach:
        log_activity("leads_outreach_start", "Starting outreach to high-fit leads (email/website/twitter — NO LinkedIn DMs)...")
        outreach_leads = get_leads_for_outreach(limit=20)

        for lead in outreach_leads:
            try:
                contact_method = lead.get("contact_method", "email")

                if contact_method == "email" and lead.get("email"):
                    # Generate cold email and send via Gmail browser
                    email_content = generate_cold_email(profile, lead)
                    if email_content:
                        subject = "Quick question about your startup"
                        body = email_content
                        if email_content.startswith("Subject:"):
                            parts = email_content.split("\n", 1)
                            subject = parts[0].replace("Subject:", "").strip()
                            body = parts[1].strip() if len(parts) > 1 else email_content

                        msg_id = save_outreach_message(
                            lead["id"], "email", subject, body, status="draft"
                        )

                        result = send_outreach(lead, "email", body, email_subject=subject)
                        if result["success"]:
                            update_outreach_message(msg_id, "sent", result["message"])
                            update_lead_status(lead["id"], "contacted")
                            outreached_count += 1
                            log_activity("leads_outreach", f"Email sent to {lead.get('name','')} at {lead.get('email','')}")
                        else:
                            update_outreach_message(msg_id, "failed", result["message"])
                            log_activity("leads_outreach_failed", result["message"], level="warning")

                        time.sleep(5)  # Rate limit between emails

                elif contact_method == "website_form" and lead.get("website"):
                    # Try to fill out a contact form on the lead's website
                    email_content = generate_cold_email(profile, lead)
                    if email_content:
                        subject = "Quick question about your startup"
                        body = email_content
                        if email_content.startswith("Subject:"):
                            parts = email_content.split("\n", 1)
                            subject = parts[0].replace("Subject:", "").strip()
                            body = parts[1].strip() if len(parts) > 1 else email_content

                        msg_id = save_outreach_message(
                            lead["id"], "website_form", subject, body, status="draft"
                        )

                        # Try filling website contact form via browser
                        result = send_outreach(lead, "website_form", body, email_subject=subject)
                        if result["success"]:
                            update_outreach_message(msg_id, "sent", result["message"])
                            update_lead_status(lead["id"], "contacted")
                            outreached_count += 1
                            log_activity("leads_outreach", f"Website form submitted for {lead.get('name','')}")
                        else:
                            update_outreach_message(msg_id, "failed", result["message"])
                            log_activity("leads_outreach_failed", result["message"], level="warning")

                        time.sleep(8)

                elif contact_method == "twitter_dm" and lead.get("twitter"):
                    # Twitter DM outreach (if we had Twitter login)
                    dm_content = generate_twitter_dm(profile, lead)
                    if dm_content:
                        msg_id = save_outreach_message(
                            lead["id"], "twitter_dm", "", dm_content, status="draft"
                        )
                        # Twitter DM sending not implemented yet — save as draft
                        update_outreach_message(msg_id, "failed", "Twitter DM sending not available")
                        log_activity("leads_outreach_failed", f"Twitter DM not available for {lead.get('name','')}", level="warning")

                # If contact_method is "manual" but lead has a website, try scraping email
                elif contact_method == "manual" and lead.get("website"):
                    # Try to find an email on their website
                    log_activity("leads_outreach", f"Manual lead {lead.get('name','')} — trying website for email...")
                    # For now, skip — these need manual review
                    pass

                # Skip linkedin_dm entirely — LinkedIn restricts automated DMs
                # LinkedIn profiles are kept for reference but NOT contacted automatically

            except Exception as e:
                log_activity("leads_outreach_error", f"Error contacting {lead.get('name','')}: {e}", level="error")

        log_activity("leads_outreach", f"Outreached {outreached_count} leads (email/website/twitter only)")
    else:
        log_activity("leads_outreach", "Auto-outreach disabled. Leads ready for manual review.")

    # --- Update state ---
    state = get_leads_state()
    config = get_leads_config()
    interval_min = config.get("interval_minutes", 30)

    update_leads_state({
        "running": 0,
        "total_discovered": state.get("total_discovered", 0) + new_count,
        "total_enriched": state.get("total_enriched", 0) + enriched_count,
        "total_evaluated": state.get("total_evaluated", 0) + evaluated_count,
        "total_outreached": state.get("total_outreached", 0) + outreached_count,
        "next_run": (datetime.now() + timedelta(minutes=interval_min)).isoformat()
    })

    summary = f"Discovered: {new_count}, Enriched: {enriched_count}, Evaluated: {evaluated_count}, Outreached: {outreached_count}"
    log_activity("leads_cycle_complete", summary)

    return {
        "discovered": new_count,
        "enriched": enriched_count,
        "evaluated": evaluated_count,
        "outreached": outreached_count,
    }


def run_leads_loop(interval_minutes: float = 30):
    """Run the leads agent as a continuous loop."""
    print(f"[LEADS AGENT] Starting loop (checking every {interval_minutes} min)")
    init_leads_db()

    while True:
        try:
            run_leads_cycle()
        except Exception as e:
            log_activity("leads_agent_error", str(e), level="error")
            print(f"[LEADS AGENT] Error: {e}")

        print(f"  Next leads scan in {interval_minutes} minutes...")
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        init_leads_db()
        result = run_leads_cycle()
        print(json.dumps(result, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "--loop":
        interval = float(sys.argv[2]) if len(sys.argv) > 2 else 30
        run_leads_loop(interval)
    else:
        print("Usage: python leads_agent.py [--once | --loop <minutes>]")
