# Auto Job Finder & Apply Agent

Autonomous AI-powered job search and application agent. Monitors 12+ job sources every 2 minutes, evaluates matches with keyword scoring, generates AI-powered CVs and cover letters, and auto-applies to matching jobs — all automatically.

**Now with Leads & Outreach**: Discovers potential clients from 7 sources, generates AI cold emails/proposals, and sends outreach via Gmail or LinkedIn DMs.

## Features

### Job Discovery (12 Sources)
- **LinkedIn** — job search + detail fetch
- **Freelancer.com** — project scraping
- **Jobicy** — remote tech jobs (API)
- **Python.org Jobs** — RSS feed
- **Mustakbil.com** — Pakistani jobs
- **TechJobs.pk** — Pakistani tech jobs
- **JSRemotely/javascript.jobs** — remote JS jobs
- **micro1.ai/refer** — AI talent platform
- **We Work Remotely** — remote jobs
- **RemoteOK** — remote jobs
- **Himalayas** — remote jobs
- **Wellfound (AngelList)** — startup jobs

### AI-Powered Application
- **CV Generation**: AI generates tailored CV per job using Pollinations (free, no API key)
- **Cover Letter**: AI generates personalized cover letter per job
- **Auto-Apply**: Playwright-based web form filling with:
  - ATS detection (Greenhouse, Lever, Workable, LinkedIn, Mustakbil, TechJobs.pk)
  - Auto login/signup with profile info
  - Form field auto-fill (email, name, phone, LinkedIn, GitHub, cover letter, CV upload)
  - Dropdown/checkbox handling
  - Screenshot proof capture

### Leads & Outreach (New)
- **7 Lead Sources**: Y Combinator, ProductHunt, Wellfound, Freelancer.com, IndieHackers, GitHub, RemoteOK
- **AI Proposal Generation**: Cold emails, LinkedIn DMs, Twitter DMs, and full proposals generated using Pollinations AI
- **Outreach Engine**: Sends emails via SMTP or Gmail browser; LinkedIn DMs via persistent browser
- **Autonomous Loop**: Discovers → enriches → evaluates → generates → sends (every 30 min)
- **Lead Scoring**: Fit score based on keyword matching and relevance
- **Dedup**: Tracks seen leads to avoid duplicate outreach

### Persistent Browser (LinkedIn)
- Chromium stays running with LinkedIn session (li_at cookie)
- User logs in once via noVNC (web-based VNC) — solves captcha manually (free!)
- Session persists across agent cycles
- Auto-apply uses the same browser — no new login needed

### Dashboard
- **Real-time monitoring**: Agent scans every 2 minutes
- **Start/Stop controls**: Start/stop agent loop and persistent browser from dashboard
- **LinkedIn status**: Shows if logged in (green/red badge)
- **Job management**: View, filter, sort, apply manually
- **Activity log**: Live progress of all agent actions
- **Leads dashboard**: Stats, recent leads, source breakdown
- **Outreach history**: All sent/draft/failed messages
- **WhatsApp notifications**: Get notified when agent applies to a job

### Anti-Bot Bypass
- **Raw Chromium + CDP**: Avoids Playwright's `--enable-automation` flag
- **Headed mode (Xvfb)**: Uses virtual display to bypass bot detection
- **Playwright Stealth**: Hides automation indicators
- **2Captcha integration**: Solves reCAPTCHA when needed ($0.77/1000 solves)
- **Manual login (noVNC)**: User solves captcha once, session saved forever
- **429 rate limit handling**: Detects and recovers gracefully

## Setup

### Prerequisites
```bash
# Install Python 3.11+ and venv
python3 -m venv /opt/venv-jobagent
source /opt/venv-jobagent/bin/activate

# Install dependencies
pip install fastapi uvicorn playwright playwright-stealth httpx beautifulsoup4
playwright install chromium

# Install Xvfb for headed mode
apt-get install -y xvfb x11vnc novnc websockify
```

### Installation
```bash
# Clone
git clone https://github.com/Hadi990op/Auto-job-Finder-Apply.git
cd Auto-job-Finder-Apply

# Copy source files
cp src/*.py /opt/job-agent/
cp src/requirements.txt /opt/job-agent/

# Install systemd services
cp deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now job-agent.service
systemctl enable --now job-agent-loop.service
systemctl enable --now leads-agent.service
```

### Configuration
1. Visit dashboard at `http://localhost:9300/`
2. Set up your profile at `/profile` (name, skills, job titles, locations, hourly rate, services)
3. Upload your CV at `/cv`
4. Set credentials (Gmail, LinkedIn passwords) at `/credentials`
5. Connect WhatsApp at `/whatsapp` (optional)
6. Log in to LinkedIn via `/login-browser` (noVNC — solve captcha once)
7. Configure leads settings at `/leads-config` (keywords, threshold, interval, auto-outreach)
8. (Optional) Log in to Gmail via `/gmail-login` for browser-based email sending
9. Click "Start Agent" on dashboard

### Caddy Reverse Proxy (optional)
```caddy
# /etc/caddy/conf.d/job-agent.caddy
handle_path /jobs/* {
    reverse_proxy localhost:9300
}
redir /jobs /jobs/jobs 308
```

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Dashboard (FastAPI)                    │
│               http://localhost:9300                        │
├────────────────┬────────────────┬────────────────────────┤
│  Jobs Dashboard │  Leads Dashboard │  Outreach History    │
│  • Job list     │  • Lead stats    │  • Sent messages     │
│  • Filters      │  • Source breakdown│ • Drafts/failed   │
│  • Manual apply │  • Lead detail   │  • Resend           │
│  • Activity log │  • Generate/send │                     │
├────────────────┴────────────────┴────────────────────────┤
│              Job Agent Loop (every 2 min)                 │
│  ┌──────────┐   ┌──────────┐   ┌───────────┐              │
│  │ Discover  │→ │ Evaluate  │→ │ Auto-Apply │              │
│  │ (12 srcs) │  │ (keyword)  │  │ (Playwright)│             │
│  └──────────┘   └──────────┘   └───────────┘              │
├──────────────────────────────────────────────────────────┤
│             Leads Agent Loop (every 30 min)              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐ │
│  │ Discover  │→ │ Evaluate  │→ │ Generate  │→ │ Outreach│ │
│  │ (7 srcs)  │  │ (fit score)│  │ (AI msg)  │  │ (email) │ │
│  └──────────┘   └──────────┘   └──────────┘   └────────┘ │
├──────────────────────────────────────────────────────────┤
│          Persistent Browsers (Chromium + CDP)             │
│   LinkedIn (port 9222)    │    Gmail (port 9333)          │
└──────────────────────────────────────────────────────────┘
```

## Services

| Service | Description | Port |
|---------|-------------|------|
| `job-agent.service` | FastAPI web dashboard | 9300 |
| `job-agent-loop.service` | Autonomous job agent loop | — |
| `job-agent-browser.service` | Persistent Chromium for LinkedIn | 9222 (CDP) |
| `leads-agent.service` | Autonomous leads & outreach loop | — |
| `gmail-browser.service` | Persistent Chromium for Gmail | 9333 (CDP) |

## Dashboard Pages

| Path | Description |
|------|-------------|
| `/jobs` | Jobs dashboard — stats, job list, filters, manual apply |
| `/jobs/profile` | Profile setup (name, skills, job titles, locations, rate, services) |
| `/jobs/credentials` | Gmail/LinkedIn passwords |
| `/jobs/activity` | Activity log |
| `/jobs/job/{id}` | Job detail with AI bid/proposal generation |
| `/leads` | Leads dashboard — stats, recent leads, source breakdown |
| `/leads/all` | All leads list |
| `/lead/{id}` | Lead detail — company info, generate/send outreach |
| `/leads-config` | Leads settings (keywords, threshold, interval, auto-outreach) |
| `/outreach` | Outreach history — all sent/draft/failed messages |
| `/gmail-login` | noVNC Gmail login page |

## API Endpoints

### Jobs & Agent
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agent/status` | Agent loop + browser + LinkedIn status |
| POST | `/api/agent/start` | Start agent loop + browser |
| POST | `/api/agent/stop` | Stop agent loop |
| POST | `/api/browser/start` | Start persistent browser |
| POST | `/api/browser/stop` | Stop persistent browser |
| GET | `/api/jobs` | List all jobs |
| POST | `/api/apply/{job_id}` | Trigger auto-apply for a job |
| POST | `/api/job/{job_id}/bid` | Generate AI proposal for a job |
| POST | `/api/job/{job_id}/bid/submit` | Submit proposal to Freelancer.com |
| GET | `/api/activity` | Activity log |
| GET | `/api/profile` | Get profile |
| POST | `/api/profile` | Save profile |
| GET | `/api/credentials` | Get credentials |
| POST | `/api/credentials` | Save credentials |
| GET | `/api/settings` | Get settings (2Captcha key, headed mode) |
| POST | `/api/settings` | Save settings |
| POST | `/api/login-browser/start` | Start noVNC login browser |
| POST | `/api/login-browser/stop` | Stop noVNC browser (saves session) |
| GET | `/api/login-browser/status` | Browser + VNC status |
| GET | `/api/ai/cv/{job_id}` | Generate AI CV for a job |
| GET | `/api/ai/cover/{job_id}` | Generate AI cover letter for a job |

### Leads & Outreach
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/leads` | List leads (with filters) |
| GET | `/api/leads/stats` | Lead statistics + source breakdown |
| GET | `/api/leads/{lead_id}` | Lead detail |
| POST | `/api/leads/{lead_id}/generate` | Generate outreach message (AI) |
| POST | `/api/leads/{lead_id}/send` | Send outreach message |
| GET | `/api/outreach` | List outreach messages |
| POST | `/api/outreach/{message_id}/resend` | Resend a failed message |
| GET | `/api/leads/config` | Get leads config |
| POST | `/api/leads/config` | Save leads config |
| POST | `/api/leads/discover` | Manually trigger lead discovery |
| POST | `/api/gmail-browser/start` | Start Gmail noVNC browser |
| POST | `/api/gmail-browser/stop` | Stop Gmail browser |
| GET | `/api/gmail-browser/status` | Gmail browser + VNC status |

### CV Management
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cv/list` | List uploaded CVs |
| POST | `/api/cv/upload` | Upload a CV file |
| GET | `/api/cv/{cv_id}/text` | Get CV text content |
| GET | `/api/cv/{cv_id}/download` | Download CV file |
| POST | `/api/cv/{cv_id}/primary` | Set primary CV |
| POST | `/api/cv/{cv_id}/delete` | Delete a CV |

## Project Structure

```
src/
├── app.py                  # FastAPI dashboard + all API routes
├── agent.py                # Job agent loop (discover → evaluate → apply)
├── job_sources.py          # 12 job source scrapers
├── evaluator.py            # Keyword scoring & job matching
├── generator.py            # AI CV & cover letter generation
├── ai_engine.py            # Pollinations AI chat interface
├── cv_manager.py           # CV upload, storage, text extraction
├── persistent_browser.py   # Raw Chromium + CDP manager
├── linkedin_search.py      # LinkedIn job search via browser
├── captcha_solver.py       # 2Captcha reCAPTCHA solver
├── manual_login.py         # noVNC browser login flow
├── leads_agent.py          # Leads loop (discover → enrich → outreach)
├── leads_sources.py        # 7 lead source scrapers
├── proposal_generator.py   # AI cold email/DM/proposal generation
├── outreach_engine.py      # Email (SMTP/Gmail) + LinkedIn DM sending
├── gmail_login.py          # noVNC Gmail login (separate browser profile)
├── whatsapp.py             # WhatsApp notification integration
├── update_profile.py       # Profile management utility
└── reset_and_run.sh        # Reset DB & restart all services
```

## Tech Stack

- **Backend**: Python, FastAPI, Uvicorn
- **Browser Automation**: Playwright, raw Chromium + CDP
- **AI**: Pollinations API (free, no API key needed)
- **Database**: SQLite
- **Captcha**: 2Captcha (optional)
- **Notifications**: WhatsApp
- **Server**: Systemd services, Caddy reverse proxy

## License

MIT
