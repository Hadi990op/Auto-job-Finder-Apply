"""
Gmail Login Browser — lets user log in to Gmail via noVNC (web-based VNC).
Session is saved to a SEPARATE persistent browser profile (so Gmail cookies
don't interfere with the LinkedIn profile).

Flow:
  1. User clicks "Login to Gmail" on dashboard
  2. This starts Xvfb + Chromium + x11vnc + websockify (noVNC)
  3. User sees the browser live, logs in to Gmail (solves 2FA/captcha manually)
  4. User clicks "Save Session" — Gmail cookies are saved to gmail_profile
  5. All services killed, session persists for future cold-email sending

Usage:
  python gmail_login.py start          — start browser with Gmail login
  python gmail_login.py status          — check if running
  python gmail_login.py stop            — stop everything, save session
  python gmail_login.py session         — check if Gmail session is saved
"""

import subprocess
import os
import sys
import time
import json
import signal
import shutil
import sqlite3 as s3
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "data" / "gmail_login_state.json"
BROWSER_PROFILE = str(BASE_DIR / "data" / "gmail_profile")
PIDS_FILE = BASE_DIR / "data" / "gmail_login_pids.txt"

# Use different ports to avoid clash with LinkedIn browser
VNC_DISPLAY = ":98"
VNC_PORT = 5998
NOVNC_PORT = 6081
DEBUG_PORT = 9333
COOKIES_FILE = BASE_DIR / "data" / "gmail_cookies.json"

GMAIL_URL = "https://accounts.google.com/signin"


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


def _get_chromium_bin():
    """Find the Chromium binary."""
    import glob
    chromium_bin = os.environ.get("CHROMIUM_BIN", "")
    if chromium_bin and os.path.exists(chromium_bin):
        return chromium_bin
    playwright_paths = glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome")
    if playwright_paths:
        playwright_paths.sort()
        if os.path.exists(playwright_paths[-1]):
            return playwright_paths[-1]
    for path in ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]:
        if os.path.exists(path):
            return path
    return "chromium"


def _vnc_healthy() -> bool:
    """Check if VNC server is listening and responds with RFB handshake."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect(("127.0.0.1", VNC_PORT))
        data = sock.recv(12)
        sock.close()
        return data.startswith(b"RFB")
    except Exception:
        return False


def _kill_stale():
    """Kill any stale processes from previous runs (by port/command)."""
    import subprocess as sp
    for port in [VNC_PORT, NOVNC_PORT, DEBUG_PORT]:
        try:
            result = sp.run(
                ["fuser", f"{port}/tcp"], capture_output=True, text=True, timeout=3
            )
            if result.stdout.strip():
                for pid_str in result.stdout.strip().split():
                    try:
                        pid = int(pid_str)
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
        except Exception:
            pass
    try:
        sp.run(["pkill", "-f", f"Xvfb {VNC_DISPLAY}"], timeout=3,
               stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    except Exception:
        pass
    time.sleep(1)


def start(url: str = GMAIL_URL):
    pids = _load_pids()
    if pids.get("websockify") and _is_running(pids["websockify"]):
        if _vnc_healthy():
            return {"success": False, "error": "Gmail login browser already running. Stop it first."}

    # Kill any stale processes from previous runs
    _kill_stale()
    time.sleep(1)

    os.makedirs(BROWSER_PROFILE, exist_ok=True)

    # Start Xvfb
    xvfb = subprocess.Popen(
        ["Xvfb", VNC_DISPLAY, "-screen", "0", "1280x900x24"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)

    chromium_bin = _get_chromium_bin()

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
        f"--remote-debugging-port={DEBUG_PORT}",
        url,
    ]
    chromium = subprocess.Popen(
        chromium_args,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "DISPLAY": VNC_DISPLAY},
    )
    time.sleep(3)

    # Start x11vnc (no -bg flag so we can track the real PID)
    x11vnc = subprocess.Popen(
        ["x11vnc", "-display", VNC_DISPLAY, "-rfbport", str(VNC_PORT),
         "-nopw", "-forever", "-shared",
         "-o", str(BASE_DIR / "data" / "gmail_x11vnc.log")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)

    # Verify x11vnc is actually responding with RFB handshake, retry if not
    if not _vnc_healthy():
        try:
            x11vnc.kill()
        except Exception:
            pass
        time.sleep(1)
        x11vnc = subprocess.Popen(
            ["x11vnc", "-display", VNC_DISPLAY, "-rfbport", str(VNC_PORT),
             "-nopw", "-forever", "-shared",
             "-o", str(BASE_DIR / "data" / "gmail_x11vnc.log")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(2)

    # Start websockify (noVNC web proxy)
    novnc_dir = "/usr/share/novnc"
    websockify = subprocess.Popen(
        ["websockify", "--web", novnc_dir, str(NOVNC_PORT), f"localhost:{VNC_PORT}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)

    pids = {
        "xvfb": xvfb.pid,
        "chromium": chromium.pid,
        "x11vnc": x11vnc.pid,
        "websockify": websockify.pid,
        "url": url,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "vnc_ok": _vnc_healthy(),
    }
    _save_pids(pids)

    return {"success": True, "pids": pids, "novnc_port": NOVNC_PORT,
            "vnc_ok": _vnc_healthy()}


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
    """Connect to running Chromium via CDP, save Gmail cookies, then close."""
    cookies_saved = []
    try:
        import urllib.request
        import json as _json

        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=3)
        except Exception:
            return {"success": False, "error": "Debug port not reachable", "cookies": []}

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
            all_cookies = []
            for ctx in browser.contexts:
                all_cookies.extend(ctx.cookies())

            COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(COOKIES_FILE, "w") as f:
                _json.dump(all_cookies, f, indent=2)

            # Check for Gmail session cookies
            google_cookies = [c["name"] for c in all_cookies if "google" in c.get("domain", "") or "gmail" in c.get("domain", "")]
            cookies_saved = google_cookies

            try:
                browser.close()
            except Exception:
                pass
            time.sleep(3)

        return {"success": True, "cookies": cookies_saved}
    except Exception as e:
        return {"success": False, "error": str(e), "cookies": []}


def stop():
    pids = _load_pids()

    cookie_result = {"success": False, "cookies": []}
    if _is_running(pids.get("chromium", 0)):
        cookie_result = _graceful_browser_close()

    for key in ["websockify", "x11vnc", "chromium", "xvfb"]:
        pid = pids.get(key)
        if pid:
            _kill_pid(pid)

    # Also kill any orphaned processes by port (catches -bg forked x11vnc etc.)
    _kill_stale()

    try:
        PIDS_FILE.unlink()
    except FileNotFoundError:
        pass

    if cookie_result.get("success") and cookie_result.get("cookies"):
        google_cookies = cookie_result["cookies"]
        # Key Gmail session cookies
        has_session = any(c in google_cookies for c in ["SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-1PSID"])
        msg = f"Gmail login browser stopped. Google cookies saved: {', '.join(google_cookies[:10])}"
        if has_session:
            msg += " ✅ Gmail session saved!"
        else:
            msg += " ⚠️ No session cookies found — login may not have completed."
        return {"success": True, "message": msg, "cookies": google_cookies, "has_session": has_session}
    elif cookie_result.get("success"):
        return {"success": True, "message": "Gmail login browser stopped. No Google cookies found."}
    else:
        return {"success": True, "message": f"Gmail login browser stopped. Cookie save note: {cookie_result.get('error', 'N/A')}", "cookies": []}


def get_session_status():
    """Check if Gmail session cookies exist in the persistent browser profile."""
    cookie_db = os.path.join(BROWSER_PROFILE, "Default", "Cookies")
    if not os.path.exists(cookie_db):
        return {"gmail_logged_in": False, "message": "No Gmail browser profile found."}

    try:
        # First check saved cookies JSON
        saved_cookies = []
        if COOKIES_FILE.exists():
            with open(COOKIES_FILE) as f:
                saved_cookies = json.load(f)
            google_cookies = [c["name"] for c in saved_cookies if "google" in c.get("domain", "") or "gmail" in c.get("domain", "")]
            has_session = any(c in google_cookies for c in ["SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-1PSID"])
            if has_session:
                return {
                    "gmail_logged_in": True,
                    "cookies_found": len(saved_cookies),
                    "cookie_names": google_cookies[:10],
                    "message": "Gmail session active (session cookies found)",
                    "source": "gmail_cookies.json",
                }

        # Check browser profile cookie DB
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        shutil.copy2(cookie_db, tmp.name)

        db = s3.connect(tmp.name)
        rows = db.execute(
            "SELECT name FROM cookies WHERE host_key LIKE '%google%' OR host_key LIKE '%gmail%' LIMIT 30"
        ).fetchall()
        db.close()
        os.unlink(tmp.name)

        if not rows:
            return {"gmail_logged_in": False, "message": "No Google/Gmail cookies found."}

        cookie_names = [r[0] for r in rows]
        has_session = any(c in cookie_names for c in ["SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-1PSID"])

        return {
            "gmail_logged_in": has_session,
            "cookies_found": len(rows),
            "cookie_names": cookie_names[:10],
            "message": "Session active" if has_session else "Cookies exist but no session cookie",
        }
    except Exception as e:
        return {"gmail_logged_in": False, "message": f"Error: {e}"}


def is_gmail_logged_in() -> bool:
    """Quick check — is Gmail session active?"""
    return get_session_status().get("gmail_logged_in", False)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        url = sys.argv[2] if len(sys.argv) > 2 else GMAIL_URL
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
