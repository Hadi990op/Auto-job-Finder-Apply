"""
Manual Login Browser — lets user log in to LinkedIn (or any site) in a real browser
via noVNC (web-based VNC). Session is saved to the persistent browser profile.

Flow:
  1. User clicks "Open Login Browser" on dashboard
  2. This starts Xvfb + Chromium + x11vnc + websockify (noVNC)
  3. User sees the browser live in their web browser, interacts with it
  4. User logs in (solves captcha manually)
  5. User clicks "Save Session" — session cookies are saved to browser_profile
  6. All services killed, session persists for future auto-apply

Usage:
  python manual_login.py start <url>   — start browser with given URL
  python manual_login.py status        — check if running
  python manual_login.py stop          — stop everything
  python manual_login.py session       — check if LinkedIn session is saved
"""

import subprocess
import os
import sys
import time
import json
import signal
import socket
import shutil
import sqlite3 as s3
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "data" / "manual_login_state.json"
BROWSER_PROFILE = str(BASE_DIR / "data" / "browser_profile")
PIDS_FILE = BASE_DIR / "data" / "manual_login_pids.txt"

# Ports
VNC_DISPLAY = ":99"
VNC_PORT = 5999
NOVNC_PORT = 6080
DEBUG_PORT = 9222
COOKIES_FILE = BASE_DIR / "data" / "saved_cookies.json"


def _save_pids(pids: dict):
    PIDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PIDS_FILE, "w") as f:
        json.dump(pids, f)


def _load_pids() -> dict:
    if PIDS_FILE.exists():
        try:
            with open(PIDS_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill_pid(pid: int):
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        if _is_running(pid):
            os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def start(url: str = "https://www.linkedin.com/login"):
    pids = _load_pids()
    if pids.get("websockify") and _is_running(pids["websockify"]):
        return {"success": False, "error": "Login browser already running. Stop it first."}

    os.makedirs(BROWSER_PROFILE, exist_ok=True)

    # Start Xvfb
    xvfb = subprocess.Popen(
        ["Xvfb", VNC_DISPLAY, "-screen", "0", "1280x900x24"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)

    # Find chromium binary (Playwright's or system)
    chromium_bin = os.environ.get("CHROMIUM_BIN", "")
    if not chromium_bin:
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            chromium_bin = pw.chromium.executable_path
            pw.stop()
        except Exception:
            pass
    if not chromium_bin or not os.path.exists(chromium_bin):
        chromium_bin = "chromium"  # fallback to system

    # Start Chromium with persistent profile (same one auto-apply uses)
    chromium_args = [
        chromium_bin,
        "--no-sandbox",
        "--disable-gpu",
        "--user-data-dir=" + BROWSER_PROFILE,
        "--window-size=1260,860",
        "--window-position=0,0",
        "--disable-blink-features=AutomationControlled",
        "--lang=en-US",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-debugging-port=9222",
        url,
    ]
    chromium = subprocess.Popen(
        chromium_args,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "DISPLAY": VNC_DISPLAY},
    )
    time.sleep(3)

    # Start x11vnc (no password, shared, background)
    x11vnc = subprocess.Popen(
        ["x11vnc", "-display", VNC_DISPLAY, "-rfbport", str(VNC_PORT),
         "-nopw", "-forever", "-shared", "-bg",
         "-o", str(BASE_DIR / "data" / "x11vnc.log")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)

    # Start websockify (noVNC web proxy)
    novnc_dir = "/usr/share/novnc"
    websockify = subprocess.Popen(
        ["websockify", "--web", novnc_dir, str(NOVNC_PORT), f"localhost:{VNC_PORT}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1)

    pids = {
        "xvfb": xvfb.pid,
        "chromium": chromium.pid,
        "x11vnc": x11vnc.pid,
        "websockify": websockify.pid,
        "url": url,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_pids(pids)

    return {"success": True, "pids": pids, "novnc_port": NOVNC_PORT}


def status():
    pids = _load_pids()
    if not pids:
        return {"running": False}

    ws_running = _is_running(pids.get("websockify", 0))
    cr_running = _is_running(pids.get("chromium", 0))

    return {
        "running": ws_running and cr_running,
        "novnc_port": NOVNC_PORT,
        "url": pids.get("url", ""),
        "started_at": pids.get("started_at", ""),
    }


def _graceful_browser_close():
    """Connect to running Chromium via CDP, save cookies, then close gracefully."""
    cookies_saved = []
    try:
        import urllib.request
        import json as _json

        # Check if debug port is active
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=3)
            _ = _json.loads(resp.read())
        except Exception:
            return {"success": False, "error": "Debug port not reachable", "cookies": []}

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
            
            # Get all cookies from all contexts
            all_cookies = []
            for ctx in browser.contexts:
                all_cookies.extend(ctx.cookies())
            
            # Also check pages for localStorage/sessionStorage if needed
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    try:
                        # Try to get session cookies from JS
                        extra = pg.evaluate("""() => {
                            try { return document.cookie; } catch(e) { return ''; }
                        }""")
                        if extra and 'li_at' in str(extra):
                            pass  # cookies already captured via API
                    except Exception:
                        pass
            
            # Save all cookies to JSON file
            COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(COOKIES_FILE, 'w') as f:
                _json.dump(all_cookies, f, indent=2)
            
            cookies_saved = [c['name'] for c in all_cookies if 'linkedin' in c.get('domain', '')]
            
            # Close browser gracefully — this flushes cookies to disk
            try:
                browser.close()
            except Exception:
                pass
            
            # Give it time to flush
            time.sleep(3)
            
        return {"success": True, "cookies": cookies_saved}
    except Exception as e:
        return {"success": False, "error": str(e), "cookies": []}


def stop():
    pids = _load_pids()
    
    # Try graceful close first — saves session cookies (li_at etc.)
    cookie_result = {"success": False, "cookies": []}
    if _is_running(pids.get("chromium", 0)):
        cookie_result = _graceful_browser_close()
    
    # Now kill all processes
    for key in ["websockify", "x11vnc", "chromium", "xvfb"]:
        pid = pids.get(key)
        if pid:
            _kill_pid(pid)

    try:
        PIDS_FILE.unlink()
    except FileNotFoundError:
        pass

    # Build message based on cookie save result
    if cookie_result.get("success") and cookie_result.get("cookies"):
        linkedin_cookies = cookie_result["cookies"]
        has_li_at = "li_at" in linkedin_cookies
        msg = f"Login browser stopped. LinkedIn cookies saved: {', '.join(linkedin_cookies)}"
        if has_li_at:
            msg += " ✅ LinkedIn session saved (li_at found)!"
        else:
            msg += " ⚠️ No li_at cookie — login may not have completed."
        return {"success": True, "message": msg, "cookies": linkedin_cookies, "has_li_at": has_li_at}
    elif cookie_result.get("success"):
        return {"success": True, "message": "Login browser stopped. No LinkedIn cookies found."}
    else:
        return {"success": True, "message": f"Login browser stopped. Cookie save note: {cookie_result.get('error', 'N/A')}", "cookies": []}


def get_session_status():
    """Check if LinkedIn session cookies exist in the persistent browser profile."""
    cookie_db = os.path.join(BROWSER_PROFILE, "Default", "Cookies")
    if not os.path.exists(cookie_db):
        return {"linkedin_logged_in": False, "message": "No browser profile cookies found."}

    try:
        # First check saved_cookies.json (from CDP graceful close)
        saved_cookies = []
        if COOKIES_FILE.exists():
            import json as _json
            with open(COOKIES_FILE) as f:
                saved_cookies = _json.load(f)
            li_cookies = [c['name'] for c in saved_cookies if 'linkedin' in c.get('domain', '')]
            has_li_at = 'li_at' in li_cookies
            if has_li_at:
                return {
                    "linkedin_logged_in": True,
                    "cookies_found": len(saved_cookies),
                    "cookie_names": li_cookies,
                    "message": "LinkedIn session active (li_at found in saved cookies)",
                    "source": "saved_cookies.json"
                }

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        shutil.copy2(cookie_db, tmp.name)

        db = s3.connect(tmp.name)
        rows = db.execute(
            "SELECT name FROM cookies WHERE host_key LIKE '%linkedin%' LIMIT 20"
        ).fetchall()
        db.close()
        os.unlink(tmp.name)

        if not rows:
            return {"linkedin_logged_in": False, "message": "No LinkedIn cookies found."}

        cookie_names = [r[0] for r in rows]
        has_session = "li_at" in cookie_names or "li_session" in cookie_names

        return {
            "linkedin_logged_in": has_session,
            "cookies_found": len(rows),
            "cookie_names": cookie_names[:10],
            "message": "Session active" if has_session else "Cookies exist but no session cookie",
        }
    except Exception as e:
        return {"linkedin_logged_in": False, "message": f"Error: {e}"}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        url = sys.argv[2] if len(sys.argv) > 2 else "https://www.linkedin.com/login"
        print(json.dumps(start(url), indent=2))
    elif cmd == "stop":
        print(json.dumps(stop(), indent=2))
    elif cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd == "session":
        print(json.dumps(get_session_status(), indent=2))
    else:
        print(f"Usage: {sys.argv[0]} <start|stop|status|session> [url]")
        sys.exit(1)
