"""
AI Job Search Agent — Web Dashboard
This is NOT a search platform. It's a control panel for the autonomous agent.

The agent runs in the background, autonomously:
  - Discovers jobs from multiple sources
  - Evaluates them against your profile
  - Auto-applies to matching jobs
  - Generates tailored CV + cover letter for each
  - Notifies you of all activity

The web UI lets you:
  1. Set up your profile (once)
  2. Configure the agent (threshold, interval, sources)
  3. Watch the agent work (live activity log)
  4. Review applied jobs and generated documents
  5. Manage your applications
"""

import json
import sqlite3
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from agent import (
    init_db, get_profile, save_profile, get_agent_state, update_agent_state,
    get_config, get_applications, get_job, update_application_status,
    run_agent_cycle, log_activity, auto_apply_to_job, get_unseen_jobs
)
from evaluator import evaluate_job
from generator import generate_cv, generate_cover_letter, generate_job_match_report
from job_sources import scrape_all_jobs, fetch_linkedin_detail
from ai_engine import ai_generate_cv, ai_generate_cover_letter, ai_evaluate_job, ai_enhance_profile, test_ai

# CV upload management
from cv_manager import (
    save_uploaded_cv, get_uploaded_cvs, get_cv, get_primary_cv,
    set_primary_cv, delete_cv, get_cv_download, init_cv_table, ALLOWED_EXTENSIONS
)

# WhatsApp is optional
try:
    from whatsapp import (
        get_qr_code, check_connection, send_message as wa_send,
        send_notification as wa_notify, is_connected as wa_connected,
        get_config as wa_get_config, save_config as wa_save_config,
        disconnect as wa_disconnect, get_pairing_code as wa_get_pairing_code,
        HAS_PLAYWRIGHT
    )
    HAS_WHATSAPP = True
except ImportError:
    HAS_WHATSAPP = False
    HAS_PLAYWRIGHT = False

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "jobagent.db"

# The app is mounted under /jobs/ via Caddy handle_path.
# All internal links and API calls must be prefixed with this.
BASE = "/jobs"

init_db()
init_cv_table()
app = FastAPI(title="AI Job Agent")

# --- DB helper ---

def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db

def get_activity_log(limit: int = 50):
    db = get_db()
    rows = db.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]

def get_all_jobs(limit: int = 100, status: str = None):
    db = get_db()
    if status:
        rows = db.execute("SELECT * FROM jobs WHERE status = ? ORDER BY fit_score DESC LIMIT ?", (status, limit)).fetchall()
    else:
        rows = db.execute("SELECT * FROM jobs ORDER BY fit_score DESC LIMIT ?", (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]

# --- CSS (shared) ---

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.nav{display:flex;background:#1e293b;position:sticky;top:0;z-index:10;box-shadow:0 2px 8px rgba(0,0,0,.3)}
.nav a{color:#94a3b8;text-decoration:none;padding:16px 20px;font-size:14px;border-bottom:3px solid transparent}
.nav a:hover{color:#e2e8f0;background:#334155}
.nav a.active{color:#60a5fa;border-bottom-color:#3b82f6}
.nav .right{margin-left:auto;display:flex;align-items:center;padding:0 20px;gap:10px}
.nav .status-dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.dot-green{background:#10b981}.dot-yellow{background:#fcd34d}.dot-red{background:#fca5a5}
.container{max-width:1000px;margin:0 auto;padding:30px 20px}
h1{font-size:28px;margin-bottom:5px;color:#f1f5f9}
h2{font-size:22px;margin:20px 0 10px;color:#f1f5f9}
.subtitle{color:#64748b;margin-bottom:30px;font-size:14px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:15px;margin-bottom:30px}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px}
.card .label{color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:1px}
.card .value{color:#f1f5f9;font-size:28px;font-weight:bold;margin-top:5px}
.card .sub{color:#475569;font-size:12px;margin-top:3px}
.btn{display:inline-block;background:#3b82f6;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-size:14px;border:none;cursor:pointer;transition:.2s}
.btn:hover{background:#2563eb}
.btn-sec{background:#334155}.btn-sec:hover{background:#475569}
.btn-green{background:#10b981}.btn-green:hover{background:#059669}
.btn-red{background:#ef4444}.btn-red:hover{background:#dc2626}
.alert{background:#1e3a5f;border:1px solid #3b82f6;border-radius:8px;padding:15px;margin-bottom:20px;color:#bfdbfe}
.alert-warn{background:#78350f;border-color:#f59e0b;color:#fcd34d}
.alert-ok{background:#065f46;border-color:#10b981;color:#6ee7b7}
table{width:100%;border-collapse:collapse;margin-top:15px}
th{text-align:left;color:#64748b;font-size:12px;text-transform:uppercase;padding:10px;border-bottom:1px solid #334155}
td{padding:10px;border-bottom:1px solid #1e293b;font-size:14px}
td a{color:#60a5fa;text-decoration:none}
.badge{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:bold}
.fit-strong{background:#065f46;color:#6ee7b7}.fit-good{background:#1e3a5f;color:#93c5fd}
.fit-moderate{background:#78350f;color:#fcd34d}.fit-weak{background:#7f1d1d;color:#fca5a5}
.status-new{background:#334155;color:#cbd5e1}.status-applied{background:#1e3a5f;color:#93c5fd}
.status-evaluated{background:#475569;color:#cbd5e1}
.log-entry{padding:8px 12px;border-bottom:1px solid #1e293b;font-size:13px;font-family:monospace}
.log-time{color:#475569}.log-info{color:#cbd5e1}.log-warning{color:#fcd34d}.log-error{color:#fca5a5}
.log-event{color:#60a5fa;font-weight:bold}
input,textarea,select{width:100%;background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:8px;padding:10px;font-size:14px}
textarea{min-height:60px;resize:vertical;font-family:monospace}
label{display:block;color:#94a3b8;font-size:13px;margin:12px 0 4px;font-weight:600}
.section{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;margin-bottom:20px}
.section h2{font-size:18px;color:#f1f5f9;margin-bottom:10px}
pre{white-space:pre-wrap;font-family:monospace;font-size:13px;line-height:1.5}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid #334155;border-top:2px solid #3b82f6;border-radius:50%;animation:spin 1s linear infinite;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
"""

def nav(active: str = "") -> str:
    state = get_agent_state()
    running = state.get("running", 0) if state else 0
    dot_class = "dot-green" if running else "dot-yellow"
    status_text = "RUNNING" if running else "IDLE"
    return f"""<div class="nav">
<a href="{BASE}/" class="{'active' if active=='home' else ''}">Dashboard</a>
<a href="{BASE}/profile" class="{'active' if active=='profile' else ''}">Profile</a>
<a href="{BASE}/cv" class="{'active' if active=='cv' else ''}">My CVs</a>
<a href="{BASE}/jobs" class="{'active' if active=='jobs' else ''}">Jobs</a>
<a href="{BASE}/applications" class="{'active' if active=='apps' else ''}">Applications</a>
<a href="{BASE}/whatsapp" class="{'active' if active=='whatsapp' else ''}">WhatsApp</a>
<a href="{BASE}/activity" class="{'active' if active=='activity' else ''}">Activity Log</a>
<div class="right"><span class="status-dot {dot_class}"></span><span style="font-size:12px;color:#64748b">{status_text}</span></div>
</div>"""


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    profile = get_profile()
    state = get_agent_state()
    config = get_config()
    has_p = bool(profile)

    db = get_db()
    job_count = db.execute("SELECT COUNT(*) as c FROM jobs").fetchone()["c"]
    new_count = db.execute("SELECT COUNT(*) as c FROM jobs WHERE status='new'").fetchone()["c"]
    applied_count = db.execute("SELECT COUNT(*) as c FROM applications").fetchone()["c"]
    high_fit = db.execute("SELECT COUNT(*) as c FROM jobs WHERE fit_score >= 60").fetchone()["c"]
    db.close()

    next_run = state.get("next_run", "") if state else ""
    last_run = state.get("last_run", "") if state else ""
    total_applied = state.get("total_applied", 0) if state else 0
    total_disc = state.get("total_discovered", 0) if state else 0

    profile_name = profile.get("name", "Not set") if profile else "Not set"

    threshold = profile.get("auto_apply_threshold", 50) if profile else 50

    # Status indicators
    wa_connected_status = wa_connected() if HAS_WHATSAPP else False
    wa_badge = '<span style="color:#6ee7b7">●</span> WhatsApp Connected' if wa_connected_status else f'<span style="color:#fcd34d">●</span> <a href="{BASE}/whatsapp" style="color:#fcd34d">Connect WhatsApp</a>'
    ai_badge = '<span style="color:#6ee7b7">●</span> AI Active (Pollinations)'

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Job Agent</title>
<style>{CSS}</style></head><body>
{nav('home')}
<div class="container">
<h1>AI Job Agent</h1>
<p class="subtitle">Autonomous agent — finds jobs, evaluates with AI, generates CV/cover letters with AI, auto-applies, and notifies you on WhatsApp</p>

<div style="display:flex;gap:20px;margin-bottom:20px;font-size:13px">
<span>{ai_badge}</span>
<span>{wa_badge}</span>
</div>

{f"<div class='alert'>⚠️ Profile not set up. <a href='{BASE}/profile' class='btn' style='padding:5px 12px;font-size:12px'>Set up profile →</a></div>" if not has_p else ""}

<div class="cards">
<div class="card"><div class="label">Profile</div><div class="value" style="font-size:18px">{profile_name}</div><div class="sub">{'✓ Set up' if has_p else 'Not configured'}</div></div>
<div class="card"><div class="label">CV Uploaded</div><div class="value" style="font-size:18px">{'✓ Yes' if get_primary_cv() else '✕ No'}</div><div class="sub">{'<a href=\'' + BASE + '/cv\' style=\'color:#60a5fa\'>Manage CVs →</a>' if get_uploaded_cvs() else '<a href=\'' + BASE + '/cv\' style=\'color:#fcd34d\'>Upload CV →</a>'}</div></div>
<div class="card"><div class="label">Jobs Discovered</div><div class="value">{job_count}</div><div class="sub">{new_count} new, {high_fit} high fit</div></div>
<div class="card"><div class="label">Auto-Applied</div><div class="value">{applied_count}</div><div class="sub">Total: {total_applied}</div></div>
<div class="card"><div class="label">Auto-Apply Threshold</div><div class="value">{threshold}</div><div class="sub">Min score to apply</div></div>
</div>

<div class="section">
<h2>Agent Control</h2>
<p style="color:#64748b;margin-bottom:15px">Run the agent now to discover new jobs and auto-apply to matches.</p>
<div id="run-result"></div>
<button class="btn btn-green" onclick="runAgent()" id="run-btn">▶ Run Agent Now</button>
<a href="{BASE}/config" class="btn btn-sec">⚙ Configure</a>
<a href="{BASE}/activity" class="btn btn-sec">View Activity Log</a>
<script>
async function runAgent() {{
document.getElementById('run-btn').disabled = true;
document.getElementById('run-btn').innerHTML = '<span class="spinner"></span> Running...';
document.getElementById('run-result').innerHTML = '<div style="padding:20px;text-align:center"><span class="spinner"></span><p style="margin-top:10px;color:#64748b">Agent is running in background...<br>Check <a href="{BASE}/activity" style="color:#60a5fa">Activity Log</a> for live progress.<br>AI generation takes ~1 min per job.</p></div>';
try {{
const resp = await fetch('{BASE}/api/run');
const data = await resp.json();
if (data.status === 'started') {{
document.getElementById('run-result').innerHTML = '<div class="alert-ok" style="padding:15px;border-radius:8px;margin-bottom:15px">✓ Agent started! Check <a href="{BASE}/activity" style="color:#6ee7b7">Activity Log</a> for live progress.</div>';
document.getElementById('run-btn').disabled = false;
document.getElementById('run-btn').innerHTML = '▶ Run Agent Now';
// Auto-refresh activity log
setTimeout(() => location.href = '{BASE}/activity', 3000);
}} else if (data.status === 'already_running') {{
document.getElementById('run-result').innerHTML = '<div class="alert-warn" style="padding:15px;border-radius:8px">Agent is already running. <a href="{BASE}/activity" style="color:#fcd34d">View progress →</a></div>';
document.getElementById('run-btn').disabled = false;
document.getElementById('run-btn').innerHTML = '▶ Run Agent Now';
}} else if (data.error) {{
document.getElementById('run-result').innerHTML = '<div class="alert-warn">⚠ ' + data.error + '</div>';
document.getElementById('run-btn').disabled = false;
document.getElementById('run-btn').innerHTML = '▶ Run Agent Now';
}}
}} catch(e) {{
document.getElementById('run-result').innerHTML = '<div class="alert-warn">Error: ' + e.message + '</div>';
document.getElementById('run-btn').disabled = false;
document.getElementById('run-btn').innerHTML = '▶ Run Agent Now';
}}
}}
</script>
</div>

<div class="section">
<h2>Recent Activity</h2>
{'<table><tr><th>Time</th><th>Event</th><th>Details</th></tr>' + ''.join(
    f'<tr><td style="font-size:11px;color:#475569">{r["timestamp"][11:16]}</td><td><span class="log-event">{r["event"]}</span></td><td style="font-size:13px">{r["details"] or ""}</td></tr>'
    for r in get_activity_log(8)
) + '</table>' if get_activity_log(1) else '<p style="color:#475569">No activity yet.</p>'}
</div>

<div style="color:#475569;font-size:12px;margin-top:20px">
Last run: {last_run[:19] if last_run else 'Never'} | Next scheduled: {next_run[:19] if next_run else 'Not scheduled'} | Total discovered: {total_disc}
</div>
</div></body></html>"""


@app.get("/profile", response_class=HTMLResponse)
async def profile_page():
    profile = get_profile()

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Profile — Job Agent</title>
<style>{CSS}</style></head><body>
{nav('profile')}
<div class="container">
<h1>Your Profile</h1>
<p class="subtitle">The agent uses this to evaluate jobs and generate your CV & cover letters. Set up once, then everything is automatic.</p>

<div class="alert">💡 Fill in as much as you can. The agent will match your skills against job postings and auto-apply when the fit score is above your threshold. Also upload your CV at the <a href="{BASE}/cv" style="color:#60a5fa">My CVs</a> page — any format supported!</p>
</div>

<form method="POST" action="{BASE}/profile">
<div class="section">
<h2>Personal Info</h2>
<label>Name</label><input name="name" value="{profile.get('name','')}" placeholder="John Doe">
<label>Email</label><input name="email" type="email" value="{profile.get('email','')}" placeholder="john@email.com">
<label>Phone</label><input name="phone" value="{profile.get('phone','')}" placeholder="+1 234 567 890">
<label>Location</label><input name="location" value="{profile.get('location','')}" placeholder="Berlin, Germany">
<label>LinkedIn URL</label><input name="linkedin" value="{profile.get('linkedin','')}" placeholder="linkedin.com/in/johndoe">
<label>GitHub URL</label><input name="github" value="{profile.get('github','')}" placeholder="github.com/johndoe">
<label>Portfolio URL</label><input name="portfolio" value="{profile.get('portfolio','')}" placeholder="johndoe.dev">
<label>Years of Experience</label><input name="years_experience" value="{profile.get('years_experience','')}" placeholder="5">
<label>Current/Most Recent Role</label><input name="current_role" value="{profile.get('current_role','')}" placeholder="Senior Software Engineer">
<label>Professional Summary</label><textarea name="summary" placeholder="Brief summary of your professional background...">{profile.get('summary','')}</textarea>
</div>

<div class="section">
<h2>Skills & Career</h2>
<label>Core Skills (most important — matched first)</label>
<input name="core_skills" value="{', '.join(profile.get('core_skills',[]))}" placeholder="Python, React, AWS, SQL">
<label>All Skills</label>
<input name="skills" value="{', '.join(profile.get('skills',[]))}" placeholder="Python, JavaScript, React, Docker, Kubernetes, AWS...">
<label>Target Job Titles</label>
<input name="job_titles" value="{', '.join(profile.get('job_titles',[]))}" placeholder="Software Engineer, Backend Developer, Full Stack Developer">
<label>Experience Domains</label>
<input name="experience_domains" value="{', '.join(profile.get('experience_domains',[]))}" placeholder="web development, API design, cloud infrastructure">
<label>Career Goals (one per line)</label>
<textarea name="career_goals" placeholder="Work with modern web technologies&#10;Cloud infrastructure">{chr(10).join(profile.get('career_goals',[]))}</textarea>
<label>Preferred Locations (for job filtering)</label>
<input name="preferred_locations" value="{', '.join(profile.get('preferred_locations',[]))}" placeholder="Remote, Berlin, London">
</div>

<div class="section">
<h2>Agent Settings</h2>
<label>Auto-Apply Threshold (0-100)</label>
<input name="auto_apply_threshold" type="number" min="0" max="100" value="{profile.get('auto_apply_threshold',50)}" placeholder="50">
<div style="color:#475569;font-size:11px;margin-top:3px">Jobs with a fit score above this number will be auto-applied to. Lower = more applications. Recommended: 40-60.</div>
</div>

<div class="section">
<h2>Experience (JSON)</h2>
<label>Work Experience</label>
<textarea name="experience" style="min-height:150px" placeholder='[{{"role":"Senior Developer","company":"Tech Corp","period":"2021-2024","location":"Berlin","bullets":["Built microservices","Led team of 5"}}]'>{json.dumps(profile.get('experience',[]),indent=2) if profile.get('experience') else ''}</textarea>
<label>Education</label>
<textarea name="education" style="min-height:80px" placeholder='[{{"degree":"BSc Computer Science","institution":"University","year":"2018"}}]'>{json.dumps(profile.get('education',[]),indent=2) if profile.get('education') else ''}</textarea>
<label>Projects (optional)</label>
<textarea name="projects" style="min-height:80px" placeholder='[{{"name":"Project","description":"What it does","link":"github.com/..."}}]'>{json.dumps(profile.get('projects',[]),indent=2) if profile.get('projects') else ''}</textarea>
<label>Certifications (optional)</label>
<textarea name="certifications" style="min-height:60px" placeholder='[{{"name":"AWS Certified","issuer":"Amazon","year":"2023"}}]'>{json.dumps(profile.get('certifications',[]),indent=2) if profile.get('certifications') else ''}</textarea>
<label>Languages (comma-separated)</label>
<input name="languages" value="{', '.join(profile.get('languages',[]))}" placeholder="English (fluent), German (intermediate)">
</div>

<button type="submit" class="btn" style="width:100%;padding:15px">Save Profile & Activate Agent</button>
</form>
</div></body></html>"""


@app.post("/profile")
async def save_profile_form(request: Request):
    form = await request.form()
    profile = {}

    for key in ["name", "email", "phone", "location", "linkedin", "github",
                 "portfolio", "years_experience", "current_role", "summary"]:
        val = form.get(key, "")
        if val:
            profile[key] = val.strip()

    for key in ["core_skills", "skills", "job_titles", "experience_domains",
                "preferred_locations", "languages"]:
        val = form.get(key, "")
        if val:
            profile[key] = [x.strip() for x in val.split(",") if x.strip()]

    val = form.get("career_goals", "")
    if val:
        profile["career_goals"] = [x.strip() for x in val.strip().split("\n") if x.strip()]

    # Auto-apply threshold
    threshold = form.get("auto_apply_threshold", "50")
    try:
        profile["auto_apply_threshold"] = int(threshold)
    except (ValueError, TypeError):
        profile["auto_apply_threshold"] = 50

    for key in ["experience", "education", "projects", "certifications"]:
        val = form.get(key, "")
        if val and val.strip():
            try:
                profile[key] = json.loads(val)
            except json.JSONDecodeError:
                profile[key] = []

    save_profile(profile)
    log_activity("profile_updated", f"Profile updated for {profile.get('name','user')}")

    return HTMLResponse(f'<meta http-equiv="refresh" content="2;url={BASE}/"><body style="background:#0f172a;color:#e2e8f0;font-family:sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh"><div style="background:#065f46;padding:40px;border-radius:12px;text-align:center"><h2 style="color:#6ee7b7">✓ Profile Saved!</h2><p style="color:#94a3b8;margin-top:10px">Agent activated. Redirecting...</p></div></body>')


@app.get("/config", response_class=HTMLResponse)
async def config_page():
    config = get_config()
    state = get_agent_state()

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Config — Job Agent</title>
<style>{CSS}</style></head><body>
{nav()}
<div class="container">
<h1>Agent Configuration</h1>
<p class="subtitle">Control how the autonomous agent operates</p>

<div class="section">
<h2>Agent Settings</h2>
<form method="POST" action="{BASE}/config">
<label>Run Interval (hours)</label>
<input name="run_interval_hours" type="number" step="0.5" min="0.5" value="{config.get('run_interval_hours',4)}" placeholder="4">
<div style="color:#475569;font-size:11px;margin-top:3px">How often the agent runs automatically (when running as a service)</div>
<label>Max Jobs Per Source</label>
<input name="max_per_source" type="number" min="5" max="100" value="{config.get('max_per_source',30)}" placeholder="30">
<label>Max Applications Per Cycle</label>
<input name="max_applications_per_cycle" type="number" min="1" max="50" value="{config.get('max_applications_per_cycle',10)}" placeholder="10">
<div style="color:#475569;font-size:11px;margin-top:3px">Max jobs to auto-apply to in a single cycle (prevents spam)</div>
<label>Notification Webhook (optional, future WhatsApp)</label>
<input name="notification_webhook" value="{config.get('notification_webhook','')}" placeholder="https://hooks.whatsapp.com/...">
<button type="submit" class="btn" style="margin-top:15px">Save Configuration</button>
</form>
</div>

<div class="section">
<h2>Agent Stats</h2>
<table>
<tr><th>Total Jobs Discovered</th><td>{state.get('total_discovered',0) if state else 0}</td></tr>
<tr><th>Total Jobs Evaluated</th><td>{state.get('total_evaluated',0) if state else 0}</td></tr>
<tr><th>Total Auto-Applied</th><td>{state.get('total_applied',0) if state else 0}</td></tr>
<tr><th>Last Run</th><td>{(state.get('last_run','') or 'Never')[:19]}</td></tr>
<tr><th>Next Run</th><td>{(state.get('next_run','') or 'Not scheduled')[:19]}</td></tr>
</table>
</div>
</div></body></html>"""


@app.post("/config")
async def save_config_form(request: Request):
    form = await request.form()
    config = {}
    for key in ["run_interval_hours", "max_per_source", "max_applications_per_cycle"]:
        val = form.get(key, "")
        if val:
            try:
                config[key] = float(val) if key == "run_interval_hours" else int(val)
            except (ValueError, TypeError):
                pass
    webhook = form.get("notification_webhook", "")
    if webhook:
        config["notification_webhook"] = webhook

    # Merge with existing config
    existing = get_config()
    existing.update(config)
    update_agent_state({"config": existing})
    log_activity("config_updated", f"Config updated: {json.dumps(config)}")

    return HTMLResponse(f'<meta http-equiv="refresh" content="2;url={BASE}/config">')


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(status: str = ""):
    jobs = get_all_jobs(limit=100, status=status if status != "all" else None)

    rows_html = ""
    for j in jobs:
        verdict = j.get("verdict", "")
        badge_class = "fit-strong" if "Strong" in verdict else "fit-good" if "Good" in verdict else "fit-moderate" if "Moderate" in verdict else "fit-weak" if "Weak" in verdict else "fit-good"
        score = j.get("fit_score", 0)
        job_status = j.get("status", "new")
        status_class = f"status-{job_status}"

        tags_str = ""
        try:
            tags = json.loads(j.get("tags", "[]"))
            if tags:
                tags_str = " ".join(f"<span style='color:#475569;font-size:11px'>{t}</span>" for t in tags[:3])
        except:
            pass

        rows_html += f"""
        <tr>
        <td><a href="{BASE}/job/{j['id']}">{j.get('title','N/A')}</a></td>
        <td>{j.get('company','—')}</td>
        <td>{j.get('location','—')}</td>
        <td><span class="badge {badge_class}">{verdict} ({score:.0f})</span></td>
        <td><span class="badge {status_class}">{job_status}</span></td>
        <td style="font-size:11px;color:#475569">{j.get('source','')}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jobs — Job Agent</title>
<style>{CSS}</style></head><body>
{nav('jobs')}
<div class="container">
<h1>Discovered Jobs ({len(jobs)})</h1>
<p class="subtitle">All jobs the agent has found and evaluated</p>

<div style="margin-bottom:15px">
<a href="{BASE}/jobs" class="btn btn-sec" style="font-size:12px;padding:5px 12px">All</a>
<a href="{BASE}/jobs?status=new" class="btn btn-sec" style="font-size:12px;padding:5px 12px">New</a>
<a href="{BASE}/jobs?status=evaluated" class="btn btn-sec" style="font-size:12px;padding:5px 12px">Evaluated</a>
<a href="{BASE}/jobs?status=applied" class="btn btn-sec" style="font-size:12px;padding:5px 12px">Applied</a>
</div>

{"<table><tr><th>Title</th><th>Company</th><th>Location</th><th>Fit</th><th>Status</th><th>Source</th></tr>" + rows_html + "</table>" if jobs else "<div class='alert'>No jobs discovered yet. Run the agent from the dashboard.</div>"}
</div></body></html>"""


@app.get("/job/{job_id}", response_class=HTMLResponse)
async def job_detail_page(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    profile = get_profile()

    # Get evaluation
    eval_result = None
    if profile:
        job_dict = dict(job)
        try:
            job_dict["tags"] = json.loads(job.get("tags", "[]"))
        except:
            job_dict["tags"] = []
        try:
            job_dict["matched_skills"] = json.loads(job.get("matched_skills", "[]"))
        except:
            job_dict["matched_skills"] = []
        eval_result = evaluate_job(job_dict, profile)
        report = generate_job_match_report(job_dict, eval_result)
    else:
        report = "Profile not set up."

    verdict = job.get("verdict", "")
    badge_class = "fit-strong" if "Strong" in verdict else "fit-good" if "Good" in verdict else "fit-moderate" if "Moderate" in verdict else "fit-weak"
    score = job.get("fit_score", 0)

    # Check if already applied
    db = get_db()
    existing_app = db.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    db.close()

    apply_html = ""
    if existing_app:
        apply_html = f'<div class="alert-ok" style="margin-bottom:15px">✓ Already applied (Application #{existing_app["id"]}) — <a href="{BASE}/application/{existing_app["id"]}" style="color:#6ee7b7">View →</a></div>'
    elif profile and eval_result and eval_result.should_auto_apply:
        apply_html = f"""
        <div class="section">
        <h2>Auto-Apply</h2>
        <p style="color:#64748b;margin-bottom:10px">This job has a fit score of {score} (above your threshold of {profile.get('auto_apply_threshold',50)}). Apply now?</p>
        <button class="btn btn-green" onclick="autoApply()">🎯 Auto-Apply Now</button>
        <div id="apply-result"></div>
        <script>
        async function autoApply() {{
        document.getElementById('apply-result').innerHTML = '<span class="spinner"></span> Generating CV & cover letter...';
        try {{
        const resp = await fetch('{BASE}/api/apply/{job_id}', {{method:'POST'}});
        const data = await resp.json();
        if (data.success) {{
        document.getElementById('apply-result').innerHTML = '<div class="alert-ok" style="margin-top:10px">✓ Applied! <a href="{BASE}/application/'+data.app_id+'" style="color:#6ee7b7">View application →</a></div>';
        }} else {{
        document.getElementById('apply-result').innerHTML = '<div class="alert-warn">Error: '+data.error+'</div>';
        }}
        }} catch(e) {{ document.getElementById('apply-result').innerHTML = 'Error: '+e.message; }}
        }}
        </script>
        </div>"""
    elif profile:
        apply_html = f'<div class="alert-warn">Fit score {score} is below your auto-apply threshold ({profile.get("auto_apply_threshold",50)}). <a href="{BASE}/profile">Lower threshold</a> or apply manually.</div>'

    # Description
    desc = job.get("description", "") or "No description available."
    if len(desc) > 5000:
        desc = desc[:5000] + "..."

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{job.get('title','Job')} — Job Agent</title>
<style>{CSS}</style></head><body>
{nav()}
<div class="container">
<h1 style="font-size:22px">{job.get('title','N/A')}</h1>
<div style="margin:10px 0">
<span class="badge {badge_class}">{verdict} ({score:.0f}/100)</span>
<span class="badge status-{job.get('status','new')}">{job.get('status','new')}</span>
</div>
<div style="color:#94a3b8;margin-bottom:10px;font-size:14px">
{job.get('company','—')} · {job.get('location','—')} · Source: {job.get('source','')}<br>
<a href="{job.get('url','')}" target="_blank" style="color:#60a5fa">View Original ↗</a>
</div>

{apply_html}

<div class="section"><h2>Job Fit Evaluation</h2><pre style="white-space:pre-wrap;font-size:13px">{report}</pre></div>

<div class="section"><h2>Job Description</h2><pre style="white-space:pre-wrap;max-height:400px;overflow-y:auto;font-size:13px;color:#cbd5e1">{desc}</pre></div>

<div class="section">
<h2>Generate Documents</h2>
<a href="{BASE}/job/{job_id}/cv" class="btn">📄 View CV</a>
<a href="{BASE}/job/{job_id}/cover" class="btn">✉️ View Cover Letter</a>
</div>
</div></body></html>"""


@app.get("/job/{job_id}/cv", response_class=PlainTextResponse)
async def view_cv(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    profile = get_profile()
    if not profile:
        raise HTTPException(400, "Profile not set up")
    job_dict = dict(job)
    try: job_dict["tags"] = json.loads(job.get("tags", "[]"))
    except: job_dict["tags"] = []
    evaluation = evaluate_job(job_dict, profile)
    cv = generate_cv(profile, job_dict, evaluation)
    return PlainTextResponse(cv, media_type="text/plain")


@app.get("/job/{job_id}/cover", response_class=PlainTextResponse)
async def view_cover(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    profile = get_profile()
    if not profile:
        raise HTTPException(400, "Profile not set up")
    job_dict = dict(job)
    try: job_dict["tags"] = json.loads(job.get("tags", "[]"))
    except: job_dict["tags"] = []
    evaluation = evaluate_job(job_dict, profile)
    cover = generate_cover_letter(profile, job_dict, evaluation)
    return PlainTextResponse(cover, media_type="text/plain")


@app.get("/applications", response_class=HTMLResponse)
async def applications_page():
    apps = get_applications()

    rows_html = ""
    for a in apps:
        status_colors = {"applied":"#93c5fd","interview":"#fcd34d","offer":"#6ee7b7","rejected":"#fca5a5","interested":"#94a3b8"}
        sc = status_colors.get(a["status"], "#94a3b8")
        rows_html += f"""
        <tr>
        <td><a href="{BASE}/application/{a['id']}">{a.get('job_title','N/A')}</a></td>
        <td>{a.get('company','—')}</td>
        <td>{a.get('location','—')}</td>
        <td>{a.get('source','')}</td>
        <td><span style="color:{sc};font-weight:bold">{a['status']}</span></td>
        <td>{a.get('applied_date','—')[:10]}</td>
        <td><a href="{BASE}/application/{a['id']}" style="color:#60a5fa">View →</a></td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Applications — Job Agent</title>
<style>{CSS}</style></head><body>
{nav('apps')}
<div class="container">
<h1>Applications ({len(apps)})</h1>
<p class="subtitle">Jobs the agent has auto-applied to</p>
{"<table><tr><th>Title</th><th>Company</th><th>Location</th><th>Source</th><th>Status</th><th>Date</th><th></th></tr>" + rows_html + "</table>" if apps else '<div class="alert">No applications yet. The agent will auto-apply when you run it.</div>'}
</div></body></html>"""


@app.get("/application/{app_id}", response_class=HTMLResponse)
async def application_detail(app_id: int):
    db = get_db()
    app_row = db.execute("""
        SELECT a.*, j.title as job_title, j.company, j.location, j.url, j.source
        FROM applications a JOIN jobs j ON a.job_id = j.id WHERE a.id = ?
    """, (app_id,)).fetchone()
    db.close()
    if not app_row:
        raise HTTPException(404, "Application not found")

    a = dict(app_row)
    statuses = ["applied", "interview", "offer", "rejected"]
    eval_data = ""
    try:
        ev = json.loads(a.get("evaluation", "{}"))
        eval_data = f"<p><strong>Score:</strong> {ev.get('overall_score','?')}/100 | <strong>Verdict:</strong> {ev.get('verdict','?')}</p>"
        if ev.get("matched_skills"):
            eval_data += f"<p><strong>Matched skills:</strong> {', '.join(ev['matched_skills'])}</p>"
    except:
        pass

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Application #{app_id} — Job Agent</title>
<style>{CSS}</style></head><body>
{nav()}
<div class="container">
<h1 style="font-size:22px">{a.get('job_title','N/A')}</h1>
<div style="color:#94a3b8;margin-bottom:20px">{a.get('company','—')} · {a.get('location','—')} · <a href="{a.get('url','')}" target="_blank" style="color:#60a5fa">Original ↗</a></div>

<div class="section">
<h2>Application Details</h2>
{eval_data}
<p><strong>Source:</strong> {a.get('source','')} | <strong>Method:</strong> {a.get('apply_method','')} | <strong>Date:</strong> {a.get('applied_date','')}</p>
<p><strong>Result:</strong> {a.get('apply_result','')}</p>
</div>

<form method="POST" action="{BASE}/application/{app_id}">
<div class="section">
<h2>Update Status</h2>
<label>Status</label>
<select name="status">
{"".join(f'<option value="{s}" {"selected" if a["status"]==s else ""}>{s.capitalize()}</option>' for s in statuses)}
</select>
<label>Notes</label>
<textarea name="notes" rows="3">{a.get('notes','') or ''}</textarea>
<button type="submit" class="btn" style="margin-top:10px">Update</button>
</div>
</form>

<div class="section"><h2>Generated CV</h2><pre style="max-height:300px;overflow-y:auto">{a.get('cv_text','') or ''}</pre></div>
<div class="section"><h2>Generated Cover Letter</h2><pre style="max-height:300px;overflow-y:auto">{a.get('cover_letter_text','') or ''}</pre></div>
{"<div class='section'><h2>Uploaded CV</h2><p style='color:#94a3b8'>Your uploaded CV (primary): <strong>" + get_primary_cv().get('original_filename','') + f"</strong></p><a href='{BASE}/api/cv/" + str(get_primary_cv().get('id','')) + "/download' class='btn btn-sec'>⬇ Download Your CV</a></div>" if get_primary_cv() else '<div class="alert-warn">No CV uploaded. <a href=\'' + BASE + '/cv\' class=\'btn\' style=\'padding:5px 12px;font-size:12px\'>Upload your CV →</a></div>'}
<a href="{BASE}/applications" class="btn btn-sec">← Back</a>
</div></body></html>"""


@app.post("/application/{app_id}")
async def update_app(app_id: int, status: str = Form(...), notes: str = Form("")):
    update_application_status(app_id, status, notes)
    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url={BASE}/application/{app_id}">')


@app.get("/activity", response_class=HTMLResponse)
async def activity_page():
    logs = get_activity_log(100)

    log_html = ""
    for r in logs:
        level_class = f"log-{r['level']}"
        log_html += f"""<div class="log-entry">
<span class="log-time">{r['timestamp'][11:19]}</span>
<span class="log-event">{r['event']}</span>
<span class="{level_class}">{r['details'] or ''}</span>
</div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Activity Log — Job Agent</title>
<style>{CSS}</style></head><body>
{nav('activity')}
<div class="container">
<h1>Activity Log</h1>
<p class="subtitle">Real-time log of everything the agent does</p>
<div class="section">
<div style="margin-bottom:10px"><button class="btn btn-sec" style="font-size:12px" onclick="location.reload()">🔄 Refresh</button></div>
{log_html if logs else '<p style="color:#475569">No activity yet.</p>'}
</div>
</div></body></html>"""


# --- API ---

import threading

_agent_running = False

@app.get("/api/run")
async def api_run_agent():
    """Trigger an agent cycle in the background (non-blocking)."""
    global _agent_running
    if _agent_running:
        return JSONResponse({"status": "already_running", "message": "Agent is already running. Check activity log for progress."})
    
    def run_in_background():
        global _agent_running
        _agent_running = True
        try:
            run_agent_cycle()
        except Exception as e:
            log_activity("api_error", str(e), level="error")
        finally:
            _agent_running = False
    
    thread = threading.Thread(target=run_in_background, daemon=True)
    thread.start()
    
    return JSONResponse({"status": "started", "message": "Agent cycle started. Check activity log for progress."})


@app.post("/api/apply/{job_id}")
async def api_apply_job(job_id: str):
    """Manually trigger auto-apply for a specific job."""
    job = get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    profile = get_profile()
    if not profile:
        return JSONResponse({"error": "Profile not set up"}, status_code=400)

    job_dict = dict(job)
    try: job_dict["tags"] = json.loads(job.get("tags", "[]"))
    except: job_dict["tags"] = []
    evaluation = evaluate_job(job_dict, profile)
    result = auto_apply_to_job(job_dict, profile, evaluation)
    return JSONResponse(result)


@app.get("/api/profile")
async def api_get_profile():
    return JSONResponse(get_profile())


@app.post("/api/profile")
async def api_save_profile(data: dict):
    save_profile(data)
    return JSONResponse({"status": "ok"})


@app.get("/api/jobs")
async def api_get_jobs(limit: int = 100):
    return JSONResponse(get_all_jobs(limit))


@app.get("/api/applications")
async def api_get_apps():
    return JSONResponse(get_applications())


@app.get("/api/activity")
async def api_get_activity(limit: int = 50):
    return JSONResponse(get_activity_log(limit))


@app.get("/api/state")
async def api_get_state():
    return JSONResponse(get_agent_state())


@app.get("/whatsapp", response_class=HTMLResponse)
async def whatsapp_page():
    """WhatsApp setup and status page."""
    connected = wa_connected() if HAS_WHATSAPP else False
    wa_cfg = wa_get_config() if HAS_WHATSAPP else {}
    phone = wa_cfg.get("phone", "")

    if not HAS_PLAYWRIGHT:
        status_html = '<div class="alert-warn">⚠️ Playwright not installed. WhatsApp integration unavailable.</div>'
    elif connected:
        status_html = f"""
        <div class="alert-ok">✓ WhatsApp Connected! Phone: {phone}</div>
        <div class="section">
        <h2>Send Test Message</h2>
        <p style="color:#64748b;margin-bottom:10px">Send a test message to verify the connection works.</p>
        <button class="btn" onclick="sendTest()">📤 Send Test Message</button>
        <div id="test-result"></div>
        <script>
        async function sendTest() {{
        document.getElementById('test-result').innerHTML = '<span class="spinner"></span> Sending...';
        try {{
        const resp = await fetch('{BASE}/api/whatsapp/test', {{method:'POST'}});
        const data = await resp.json();
        if (data.success) {{
        document.getElementById('test-result').innerHTML = '<div class="alert-ok" style="margin-top:10px">✓ Test message sent!</div>';
        }} else {{
        document.getElementById('test-result').innerHTML = '<div class="alert-warn" style="margin-top:10px">⚠️ ' + data.error + '</div>';
        }}
        }} catch(e) {{ document.getElementById('test-result').innerHTML = 'Error: ' + e.message; }}
        }}
        </script>
        </div>
        <div class="section">
        <h2>Disconnect</h2>
        <p style="color:#64748b;margin-bottom:10px">Remove the WhatsApp session. You'll need to scan the QR code again.</p>
        <a href="{BASE}/api/whatsapp/disconnect" class="btn btn-red" onclick="return confirm('Disconnect WhatsApp?')">Disconnect</a>
        </div>
        """
    else:
        status_html = f"""
        <div class="alert">📱 Connect WhatsApp — no API key needed! Use pairing code (no QR scanning needed)</div>
        <div class="section">
        <h2>How it works</h2>
        <p style="color:#94a3b8;margin-bottom:10px">
        <strong>Method 1: Pairing Code (recommended)</strong><br>
        1. Enter your WhatsApp phone number below<br>
        2. Click "Get Pairing Code" — you'll get an 8-digit code<br>
        3. On your phone: WhatsApp → Settings → Linked Devices → Link a Device → Link with phone number<br>
        4. Enter the 8-digit code on your phone<br>
        5. Done! The agent will send you WhatsApp notifications.
        </p>
        <p style="color:#64748b;margin-top:15px">
        <strong>Method 2: QR Code</strong><br>
        Click "Get QR Code" below and scan it with your phone camera.
        </p>
        </div>
        <div class="section">
        <h2>Connect with Pairing Code</h2>
        <label>Your WhatsApp Phone Number (with country code)</label>
        <input id="phone" placeholder="+92 322 5490551" value="{phone}">
        <button class="btn btn-green" style="margin-top:15px" onclick="getPairingCode()">📱 Get Pairing Code</button>
        <div id="pairing-result" style="margin-top:15px"></div>
        <script>
        async function getPairingCode() {{
        const phone = document.getElementById('phone').value;
        if (!phone) {{ alert('Enter your phone number first'); return; }}
        const result = document.getElementById('pairing-result');
        result.innerHTML = '<span class="spinner"></span> Getting pairing code... (this takes 20-30 seconds)';
        try {{
        const resp = await fetch('{BASE}/api/whatsapp/pair', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{phone: phone}})
        }});
        const data = await resp.json();
        if (data.success && data.code) {{
        result.innerHTML = `
        <div class="alert-ok" style="text-align:center;padding:30px">
        <h2 style="font-size:32px;letter-spacing:8px;color:#6ee7b7;margin:10px 0">{{${{data.code}}}}</h2>
        <p style="color:#94a3b8;margin:15px 0">Enter this code on your phone:</p>
        <p style="color:#e2e8f0">WhatsApp → Settings → Linked Devices → Link a Device → Link with phone number</p>
        <p style="color:#fcd34d;margin-top:10px">⏰ You have 60 seconds to enter it!</p>
        ${{data.connected ? '<div class="alert-ok" style="margin-top:15px">✓ Connected successfully!</div>' : '<div id="wait-conn" style="margin-top:15px"><span class="spinner"></span> Waiting for you to enter the code...</div>'}}
        </div>
        `;
        if (!data.connected) {{
        // Poll for connection
        let polls = 0;
        const interval = setInterval(async () => {{
        polls++;
        if (polls > 60) {{ clearInterval(interval); document.getElementById('wait-conn').innerHTML = '<div class="alert-warn">Timed out. Try again or use QR code method.</div>'; return; }}
        try {{
        const check = await fetch('{BASE}/api/whatsapp/check');
        const conn = await check.json();
        if (conn.connected) {{
        clearInterval(interval);
        document.getElementById('wait-conn').innerHTML = '<div class="alert-ok">✓ WhatsApp connected successfully! Redirecting...</div>';
        setTimeout(() => location.reload(), 2000);
        }}
        }} catch(e) {{}}
        }}, 3000);
        }} else {{
        setTimeout(() => location.reload(), 2000);
        }}
        }} else {{
        result.innerHTML = '<div class="alert-warn">⚠️ ' + (data.error || 'Could not get pairing code.') + '</div>';
        }}
        }} catch(e) {{
        result.innerHTML = '<div class="alert-warn">Error: ' + e.message + '. Try again.</div>';
        }}
        }}
        </script>
        </div>
        <div class="section">
        <h2>Or Connect with QR Code</h2>
        <button class="btn btn-sec" onclick="getQR()">📷 Get QR Code</button>
        <div id="qr-result" style="margin-top:15px"></div>
        <script>
        async function getQR() {{
        const result = document.getElementById('qr-result');
        result.innerHTML = '<span class="spinner"></span> Getting QR code... (takes 15-20 seconds)';
        try {{
        const resp = await fetch('{BASE}/api/whatsapp/qr');
        const data = await resp.json();
        if (data.qr) {{
        result.innerHTML = '<div style="text-align:center;margin-top:20px"><h3>Scan this QR code with your phone</h3><img src="data:image/png;base64,' + data.qr + '" style="border:4px solid #1e293b;border-radius:12px;max-width:300px;margin:15px auto"/><p style="color:#64748b">WhatsApp → Settings → Linked Devices → Link a Device</p><button class="btn" style="margin-top:10px" onclick="checkConn()">✓ I scanned it — Check Connection</button><div id="conn-check"></div></div>';
        }} else if (data.connected) {{
        result.innerHTML = '<div class="alert-ok">✓ Already connected!</div>';
        setTimeout(() => location.reload(), 2000);
        }} else {{
        result.innerHTML = '<div class="alert-warn">Could not get QR code. ' + (data.error || '') + '</div>';
        }}
        }} catch(e) {{ result.innerHTML = '<div class="alert-warn">Error: ' + e.message + '</div>'; }}
        }}
        async function checkConn() {{
        document.getElementById('conn-check').innerHTML = '<span class="spinner"></span> Checking...';
        try {{
        const resp = await fetch('{BASE}/api/whatsapp/check');
        const data = await resp.json();
        if (data.connected) {{
        document.getElementById('conn-check').innerHTML = '<div class="alert-ok">✓ Connected! Redirecting...</div>';
        setTimeout(() => location.reload(), 2000);
        }} else {{
        document.getElementById('conn-check').innerHTML = '<div class="alert-warn">Not connected yet. <button class="btn btn-sec" onclick="checkConn()">Check Again</button></div>';
        }}
        }} catch(e) {{ document.getElementById('conn-check').innerHTML = 'Error: ' + e.message; }}
        }}
        </script>
        </div>
        """

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WhatsApp — Job Agent</title>
<style>{CSS}</style></head><body>
{nav('whatsapp')}
<div class="container">
<h1>WhatsApp Integration</h1>
<p class="subtitle">Get notified on your phone when the agent applies to jobs — no API key needed</p>
{status_html}
</div></body></html>"""


@app.get("/health")
async def health():
    ai_ok = test_ai() if False else "unknown"  # Don't test on every health check
    return {
        "status": "ok",
        "profile_set": bool(get_profile()),
        "whatsapp_available": HAS_WHATSAPP,
        "whatsapp_connected": wa_connected() if HAS_WHATSAPP else False,
        "ai_available": True,
    }


# --- CV Upload & Management ---

@app.get("/cv", response_class=HTMLResponse)
async def cv_page():
    """CV upload and management page."""
    cvs = get_uploaded_cvs()
    primary = get_primary_cv()

    # List of uploaded CVs
    if cvs:
        rows_html = ""
        for c in cvs:
            size_str = f"{c['file_size']/1024:.0f} KB" if c.get('file_size') else "—"
            is_prim = c.get('is_primary', 0)
            prim_badge = '<span style="color:#6ee7b7;font-weight:bold">⭐ PRIMARY</span>' if is_prim else ''
            prim_btn = f'<span style="color:#475569">⭐ Primary</span>' if is_prim else f'<a href="{BASE}/api/cv/{c["id"]}/primary" class="btn btn-sec" style="font-size:11px;padding:4px 10px">Set Primary</a>'

            rows_html += f"""
            <tr>
            <td><strong>{c.get('original_filename','—')}</strong> {prim_badge}</td>
            <td>{c.get('file_type','—')}</td>
            <td>{size_str}</td>
            <td>{c.get('uploaded_at','')[:16]}</td>
            <td>
            <a href="{BASE}/api/cv/{c['id']}/download" class="btn btn-sec" style="font-size:11px;padding:4px 10px">⬇ Download</a>
            <a href="{BASE}/api/cv/{c['id']}/text" class="btn btn-sec" style="font-size:11px;padding:4px 10px">👁 View Text</a>
            {prim_btn}
            <a href="{BASE}/api/cv/{c['id']}/delete" class="btn btn-red" style="font-size:11px;padding:4px 10px" onclick="return confirm('Delete this CV?')">✕ Delete</a>
            </td>
            </tr>"""
        cvs_table = f"""<table>
        <tr><th>File</th><th>Type</th><th>Size</th><th>Uploaded</th><th>Actions</th></tr>
        {rows_html}
        </table>"""
    else:
        cvs_table = '<div class="alert" style="margin-top:15px">No CVs uploaded yet. Upload your CV/resume below — any format supported!</div>'

    supported = ", ".join(sorted(ALLOWED_EXTENSIONS))

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>My CVs — Job Agent</title>
<style>{CSS}</style></head><body>
{nav('cv')}
<div class="container">
<h1>My CVs / Resumes</h1>
<p class="subtitle">Upload your CV in any format — it's saved with your profile and used for applications</p>

<div class="alert">💡 Upload your CV/resume here. Supported formats: <strong>{supported}</strong>. The primary CV (⭐) will be attached to all job applications and used as context for AI-generated documents. Max file size: 10 MB.</p>

<div class="section">
<h2>Upload New CV</h2>
<form id="upload-form" enctype="multipart/form-data">
<label>Choose file (PDF, DOCX, TXT, MD, RTF, HTML, ODT)</label>
<input type="file" name="file" id="file-input" accept=".pdf,.docx,.doc,.txt,.md,.rtf,.html,.htm,.odt" required>
<div style="margin-top:15px;display:flex;gap:10px;align-items:center">
<button type="submit" class="btn btn-green" id="upload-btn">📤 Upload CV</button>
<span id="upload-status" style="color:#64748b;font-size:13px"></span>
</div>
<div id="upload-result" style="margin-top:15px"></div>
</form>
<script>
document.getElementById('upload-form').addEventListener('submit', async function(e) {{
e.preventDefault();
const btn = document.getElementById('upload-btn');
const status = document.getElementById('upload-status');
const result = document.getElementById('upload-result');
const fileInput = document.getElementById('file-input');
const file = fileInput.files[0];
if (!file) return;
if (file.size > 10*1024*1024) {{
result.innerHTML = '<div class="alert-warn">File too large. Max 10 MB.</div>';
return;
}}
btn.disabled = true;
btn.innerHTML = '<span class="spinner"></span> Uploading...';
status.textContent = `Uploading ${{file.name}} (${{(file.size/1024).toFixed(0)}} KB)...`;
const formData = new FormData();
formData.append('file', file);
try {{
const resp = await fetch('{BASE}/api/cv/upload', {{method:'POST',body:formData}});
const data = await resp.json();
if (data.success) {{
result.innerHTML = `<div class="alert-ok" style="margin-bottom:10px">✓ Uploaded! ${{data.filename}} (${{data.file_type}}, ${{(data.file_size/1024).toFixed(0)}} KB)${{data.is_primary ? ' — set as primary' : ''}}</div>`;
setTimeout(() => location.reload(), 1500);
}} else {{
result.innerHTML = `<div class="alert-warn">⚠️ ${{data.error}}</div>`;
btn.disabled = false;
btn.innerHTML = '📤 Upload CV';
}}
}} catch(err) {{
result.innerHTML = `<div class="alert-warn">Error: ${{err.message}}</div>`;
btn.disabled = false;
btn.innerHTML = '📤 Upload CV';
}}
}});
</script>
</div>

<div class="section">
<h2>Uploaded CVs ({len(cvs)})</h2>
{cvs_table}
</div>

{"<div class='section'><h2>Primary CV</h2><p style='color:#6ee7b7'>⭐ " + primary.get('original_filename','') + " is your primary CV — it will be used for all applications.</p></div>" if primary else ""}
</div></body></html>"""


@app.post("/api/cv/upload")
async def api_cv_upload(file: UploadFile = File(...)):
    """Upload a CV file."""
    if not file.filename:
        return JSONResponse({"error": "No file provided"}, status_code=400)
    content = await file.read()
    if not content:
        return JSONResponse({"error": "Empty file"}, status_code=400)
    result = save_uploaded_cv(file.filename, content)
    if "error" in result:
        return JSONResponse(result, status_code=400)
    log_activity("cv_uploaded", f"Uploaded: {result['filename']} ({result['file_type']})")
    return JSONResponse(result)


@app.get("/api/cv/{cv_id}/download")
async def api_cv_download(cv_id: int):
    """Download a CV file."""
    download = get_cv_download(cv_id)
    if not download:
        raise HTTPException(404, "CV not found")
    file_path, original_filename, content, cv = download
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{original_filename}"'}
    )


@app.get("/api/cv/{cv_id}/text")
async def api_cv_text(cv_id: int):
    """View extracted text from a CV."""
    cv = get_cv(cv_id)
    if not cv:
        raise HTTPException(404, "CV not found")
    text = cv.get("text_content", "") or "(No text extracted)"
    return PlainTextResponse(text, media_type="text/plain")


@app.get("/api/cv/{cv_id}/primary")
async def api_cv_set_primary(cv_id: int):
    """Set a CV as primary."""
    success = set_primary_cv(cv_id)
    if not success:
        raise HTTPException(404, "CV not found")
    cv = get_cv(cv_id)
    log_activity("cv_primary_set", f"Primary CV: {cv.get('original_filename','')}")
    return HTMLResponse(f'<meta http-equiv="refresh" content="1;url={BASE}/cv">')


@app.get("/api/cv/{cv_id}/delete")
async def api_cv_delete(cv_id: int):
    """Delete a CV."""
    cv = get_cv(cv_id)
    if not cv:
        raise HTTPException(404, "CV not found")
    delete_cv(cv_id)
    log_activity("cv_deleted", f"Deleted: {cv.get('original_filename','')}")
    return HTMLResponse(f'<meta http-equiv="refresh" content="1;url={BASE}/cv">')


@app.get("/api/cv/list")
async def api_cv_list():
    """List all uploaded CVs."""
    return JSONResponse(get_uploaded_cvs())


# --- WhatsApp ---

@app.get("/api/whatsapp/qr")
async def api_wa_qr():
    if not HAS_WHATSAPP:
        return JSONResponse({"error": "WhatsApp module not available"}, status_code=500)
    import asyncio
    loop = asyncio.get_event_loop()
    qr = await loop.run_in_executor(None, get_qr_code)
    if qr == "ALREADY_CONNECTED":
        return JSONResponse({"connected": True})
    if qr:
        if qr.startswith("FULLPAGE:"):
            return JSONResponse({"qr": qr.replace("FULLPAGE:", ""), "fullpage": True})
        return JSONResponse({"qr": qr})
    return JSONResponse({"error": "Could not get QR code. Try the pairing code method instead."}, status_code=500)


@app.post("/api/whatsapp/pair")
async def api_wa_pair(request: Request):
    """Get a pairing code for WhatsApp Web login."""
    if not HAS_WHATSAPP:
        return JSONResponse({"error": "WhatsApp module not available"}, status_code=500)
    data = await request.json()
    phone = data.get("phone", "")
    if not phone:
        return JSONResponse({"error": "Phone number required"}, status_code=400)
    # Run in a separate thread to avoid asyncio/playwright conflict
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, wa_get_pairing_code, phone)
    return JSONResponse(result)


@app.get("/api/whatsapp/check")
async def api_wa_check():
    if not HAS_WHATSAPP:
        return JSONResponse({"connected": False})
    import asyncio
    loop = asyncio.get_event_loop()
    connected = await loop.run_in_executor(None, check_connection)
    return JSONResponse({"connected": connected})


@app.post("/api/whatsapp/config")
async def api_wa_config(request: Request):
    if not HAS_WHATSAPP:
        return JSONResponse({"error": "WhatsApp module not available"}, status_code=500)
    data = await request.json()
    cfg = wa_get_config()
    if "phone" in data:
        cfg["phone"] = data["phone"]
    wa_save_config(cfg)
    return JSONResponse({"status": "ok"})


@app.post("/api/whatsapp/test")
async def api_wa_test():
    if not HAS_WHATSAPP:
        return JSONResponse({"error": "WhatsApp module not available"}, status_code=500)
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, wa_notify, "🔔 Test message from your AI Job Agent! WhatsApp is working correctly. 🎉")
    return JSONResponse(result)


@app.get("/api/whatsapp/disconnect")
async def api_wa_disconnect():
    if not HAS_WHATSAPP:
        return JSONResponse({"error": "WhatsApp module not available"}, status_code=500)
    wa_disconnect()
    return HTMLResponse(f'<meta http-equiv="refresh" content="2;url={BASE}/whatsapp"><body style="background:#0f172a;color:#e2e8f0;font-family:sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh"><div style="background:#1e293b;padding:40px;border-radius:12px;text-align:center"><h2>WhatsApp Disconnected</h2><p style="color:#94a3b8;margin-top:10px">Redirecting...</p></div></body>')


# --- AI API ---

@app.get("/api/ai/test")
async def api_ai_test():
    """Test the AI connection."""
    result = test_ai()
    return JSONResponse({"ai_working": result})


@app.post("/api/ai/cv/{job_id}")
async def api_ai_cv(job_id: str):
    """Generate an AI-powered CV for a job."""
    job = get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    profile = get_profile()
    if not profile:
        return JSONResponse({"error": "Profile not set up"}, status_code=400)
    job_dict = dict(job)
    try: job_dict["tags"] = json.loads(job.get("tags", "[]"))
    except: job_dict["tags"] = []
    cv = ai_generate_cv(profile, job_dict)
    if cv:
        return PlainTextResponse(cv, media_type="text/plain")
    return JSONResponse({"error": "AI generation failed"}, status_code=500)


@app.post("/api/ai/cover/{job_id}")
async def api_ai_cover(job_id: str):
    """Generate an AI-powered cover letter for a job."""
    job = get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    profile = get_profile()
    if not profile:
        return JSONResponse({"error": "Profile not set up"}, status_code=400)
    job_dict = dict(job)
    try: job_dict["tags"] = json.loads(job.get("tags", "[]"))
    except: job_dict["tags"] = []
    cover = ai_generate_cover_letter(profile, job_dict)
    if cover:
        return PlainTextResponse(cover, media_type="text/plain")
    return JSONResponse({"error": "AI generation failed"}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9300, workers=1)
