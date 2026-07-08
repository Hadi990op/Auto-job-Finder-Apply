"""
Persistent browser service — keeps Chromium running with LinkedIn logged in.
The agent loop and manual apply both connect to this browser via CDP.

Flow:
1. User logs in to LinkedIn via noVNC (manual_login.py) — session saved
2. This service starts Chromium with the persistent profile (has li_at cookie)
3. When a job matches, agent connects to this browser via CDP and navigates to the job page
4. Since LinkedIn is already logged in, Apply button works directly

The browser stays running as a systemd service. If it crashes, systemd restarts it.
"""

import subprocess
import os
import time
import sys
import signal
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
PROFILE_DIR = BASE_DIR / "data" / "browser_profile"
DEBUG_PORT = 9222  # Same port as manual_login for consistency

def get_chromium_bin():
    """Find the Chromium binary path."""
    import glob
    
    # Try environment variable
    chromium_bin = os.environ.get("CHROMIUM_BIN", "")
    if chromium_bin and os.path.exists(chromium_bin):
        return chromium_bin
    
    # Try Playwright's Chromium (known path — fastest check)
    playwright_paths = glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome")
    if playwright_paths:
        # Use the latest one
        playwright_paths.sort()
        if os.path.exists(playwright_paths[-1]):
            return playwright_paths[-1]
    
    # Try system Chromium
    for path in ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/snap/bin/chromium"]:
        if os.path.exists(path):
            return path
    
    # Last resort: try Playwright API
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        chromium_bin = pw.chromium.executable_path
        pw.stop()
        if chromium_bin and os.path.exists(chromium_bin):
            return chromium_bin
    except:
        pass
    
    return "chromium"

def cleanup_locks():
    """Remove stale Chromium lock files."""
    for lock in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
        p = PROFILE_DIR / lock
        if p.exists():
            try:
                p.unlink()
            except:
                pass

def is_browser_running():
    """Check if the persistent browser is running and responding to CDP."""
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{DEBUG_PORT}/json/version")
        resp = urllib.request.urlopen(req, timeout=3)
        return True
    except:
        return False

def get_chromium_pid():
    """Get the PID of the running Chromium process."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"remote-debugging-port={DEBUG_PORT}"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return int(result.stdout.strip().split("\n")[0])
    except:
        pass
    return None

def start_browser(url="about:blank", display=":99"):
    """Start the persistent Chromium browser."""
    if is_browser_running():
        print(f"[PersistentBrowser] Already running on port {DEBUG_PORT}")
        return True
    
    # Start Xvfb if not running
    xvfb_proc = None
    try:
        result = subprocess.run(["pgrep", "-f", "Xvfb :99"], capture_output=True, text=True)
        if result.returncode != 0:
            xvfb_proc = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1280x900x24"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(2)
    except:
        pass
    
    os.environ["DISPLAY"] = display
    
    cleanup_locks()
    
    chromium_bin = get_chromium_bin()
    args = [
        chromium_bin,
        "--no-sandbox",
        "--disable-gpu",
        f"--user-data-dir={PROFILE_DIR}",
        "--window-size=1280,900",
        "--window-position=0,0",
        "--disable-blink-features=AutomationControlled",
        "--lang=en-US",
        "--no-first-run",
        "--no-default-browser-check",
        f"--remote-debugging-port={DEBUG_PORT}",
        url,
    ]
    
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    
    # Wait for CDP to be ready
    for _ in range(10):
        time.sleep(1)
        if is_browser_running():
            print(f"[PersistentBrowser] Started (PID {proc.pid}, port {DEBUG_PORT})")
            return True
    
    print(f"[PersistentBrowser] Failed to start — CDP not responding")
    return False

def stop_browser():
    """Stop the persistent Chromium browser."""
    pid = get_chromium_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(2)
            # Force kill if still running
            try:
                os.kill(pid, signal.SIGKILL)
            except:
                pass
            print(f"[PersistentBrowser] Stopped (PID {pid})")
        except Exception as e:
            print(f"[PersistentBrowser] Stop error: {e}")
    
    cleanup_locks()

def get_status():
    """Get the status of the persistent browser."""
    running = is_browser_running()
    pid = get_chromium_pid()
    
    # Check if LinkedIn is logged in via cookie DB (faster, no CDP needed)
    linkedin_logged_in = False
    cookie_db = PROFILE_DIR / "Default" / "Cookies"
    if not cookie_db.exists():
        cookie_db = PROFILE_DIR / "Cookies"
    
    if cookie_db.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(cookie_db))
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM cookies WHERE name='li_at' AND host_key LIKE '%linkedin%'")
            rows = cursor.fetchall()
            linkedin_logged_in = len(rows) > 0
            conn.close()
        except:
            pass
    
    # If not in DB, try saved_cookies.json
    if not linkedin_logged_in:
        try:
            import json
            saved = BASE_DIR / "data" / "saved_cookies.json"
            if saved.exists():
                with open(saved) as f:
                    cookies = json.load(f)
                linkedin_logged_in = any(c["name"] == "li_at" for c in cookies if "linkedin" in c.get("domain", ""))
        except:
            pass
    
    return {
        "running": running,
        "pid": pid,
        "port": DEBUG_PORT,
        "linkedin_logged_in": linkedin_logged_in,
    }

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "start"
    
    if mode == "start":
        url = sys.argv[2] if len(sys.argv) > 2 else "about:blank"
        start_browser(url=url)
        # Keep the process alive
        print("[PersistentBrowser] Running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(60)
                if not is_browser_running():
                    print("[PersistentBrowser] Browser died, restarting...")
                    start_browser(url="about:blank")
        except KeyboardInterrupt:
            stop_browser()
    
    elif mode == "stop":
        stop_browser()
    
    elif mode == "status":
        status = get_status()
        print(json.dumps(status, indent=2))
    
    else:
        print(f"Usage: python persistent_browser.py [start [url] | stop | status]")
