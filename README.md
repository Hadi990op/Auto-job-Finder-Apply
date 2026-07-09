# Auto Job Finder & Apply Agent

Autonomous AI-powered job search and application agent. Monitors 12+ job sources every 2 minutes, evaluates matches with keyword scoring, generates AI-powered CVs and cover letters, and auto-applies to matching jobs — all automatically.

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
```

### Configuration
1. Visit dashboard at `http://localhost:9300/`
2. Set up your profile at `/profile`
3. Upload your CV at `/cv`
4. Set credentials (Gmail, LinkedIn passwords) at `/credentials`
5. Connect WhatsApp at `/whatsapp` (optional)
6. Log in to LinkedIn via `/login-browser` (noVNC — solve captcha once)
7. Click "Start Agent" on dashboard

### Caddy Reverse Proxy (optional)
```caddy
# /etc/caddy/conf.d/job-agent.caddy
handle_path /jobs/* {
    reverse_proxy localhost:9300
}
```

## Architecture

```
┌─────────────────────────────────────────────┐
│              Dashboard (FastAPI)             │
│         http://localhost:9300                 │
├─────────────────────────────────────────────┤
│  ┌─────────┐  ┌──────────┐  ┌─────────────┐  │
│  │ Profile  │  │  Jobs    │  │  Activity   │  │
│  │ Setup    │  │  List    │  │  Log        │  │
│  └─────────┘  └──────────┘  └─────────────┘  │
├─────────────────────────────────────────────┤
│            Agent Loop (every 2 min)          │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ Discover  │→ │ Evaluate  │→ │ Auto-Apply │  │
│  │ (12 srcs) │  │ (keyword) │  │ (Playwright)│  │
│  └──────────┘  └──────────┘  └───────────┘  │
├─────────────────────────────────────────────┤
│       Persistent Browser (Chromium)          │
│     CDP port 9222 — LinkedIn logged in       │
└─────────────────────────────────────────────┘
```

## Services

| Service | Description | Port |
|---------|-------------|------|
| `job-agent.service` | FastAPI web dashboard | 9300 |
| `job-agent-loop.service` | Autonomous agent loop | — |
| `job-agent-browser.service` | Persistent Chromium | 9222 (CDP) |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agent/status` | Agent loop + browser + LinkedIn status |
| POST | `/api/agent/start` | Start agent loop + browser |
| POST | `/api/agent/stop` | Stop agent loop |
| POST | `/api/browser/start` | Start persistent browser |
| POST | `/api/browser/stop` | Stop persistent browser |
| GET | `/api/jobs` | List all jobs |
| POST | `/api/apply/{job_id}` | Trigger auto-apply for a job |
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

## License

MIT
