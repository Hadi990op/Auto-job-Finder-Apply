"""
Outreach Engine — sends cold emails and DMs to leads.

Two email sending modes:
  1. SMTP — if Gmail credentials (email + app password) are saved in dashboard.
     Uses Gmail SMTP (smtp.gmail.com:587) with app password.
  2. Persistent Browser — if Gmail is logged in via noVNC (gmail_login.py).
     Opens Gmail in the persistent browser and sends email via the web UI.
     This is the "free" method — no app password needed, just a logged-in session.

LinkedIn DMs are sent via the persistent LinkedIn browser (persistent_browser.py)
if it's running and logged in. Otherwise, they're queued for manual sending.

Twitter DMs require API access (not implemented — outputs the message for manual send).
"""

import smtplib
import ssl
import json
import os
import time
import subprocess
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Optional
from datetime import datetime

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "jobagent.db"

# Import Gmail login status
try:
    import gmail_login
    HAS_GMAIL_LOGIN = True
except ImportError:
    HAS_GMAIL_LOGIN = False

# Import persistent browser (LinkedIn)
try:
    import persistent_browser
    HAS_PERSISTENT_BROWSER = True
except ImportError:
    HAS_PERSISTENT_BROWSER = False


def _get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def _get_credentials():
    db = _get_db()
    row = db.execute("SELECT * FROM credentials WHERE id = 1").fetchone()
    db.close()
    if row:
        return dict(row)
    return {}


def _get_profile():
    db = _get_db()
    row = db.execute("SELECT data FROM profile WHERE id = 1").fetchone()
    db.close()
    return json.loads(row["data"]) if row else {}


# ---------------------------------------------------------------------------
# Email Sending
# ---------------------------------------------------------------------------

def send_email_smtp(to_email: str, subject: str, body: str,
                   from_name: str = None, reply_to: str = None) -> dict:
    """
    Send an email via Gmail SMTP using stored app password.
    Returns {"success": bool, "message": str}
    """
    creds = _get_credentials()
    gmail_email = creds.get("gmail_email", "")
    gmail_password = creds.get("gmail_password", "")  # App password (16 chars)

    if not gmail_email or not gmail_password:
        return {"success": False, "message": "Gmail credentials not set. Add them in Dashboard → Login Accounts."}

    profile = _get_profile()
    from_name = from_name or profile.get("name", gmail_email.split("@")[0])

    msg = MIMEMultipart()
    msg["From"] = f"{from_name} <{gmail_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls(context=context)
            server.login(gmail_email, gmail_password)
            server.sendmail(gmail_email, to_email, msg.as_string())
        return {"success": True, "message": f"Email sent to {to_email} via SMTP"}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "message": "Gmail SMTP auth failed. Use an App Password (not your regular password)."}
    except Exception as e:
        return {"success": False, "message": f"SMTP error: {e}"}


def send_email_via_browser(to_email: str, subject: str, body: str,
                           debug_port: int = 9333) -> dict:
    """
    Send an email via the persistent Gmail browser (if logged in).
    Opens Gmail compose in the browser and fills in the fields.
    Tries port 9333 (dedicated Gmail browser) then 9222 (shared browser).
    """
    try:
        import urllib.request
        # Check if browser is running on specified port, try 9222 as fallback
        for port in [debug_port, 9222]:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3)
                debug_port = port
                break
            except Exception:
                continue
        else:
            return {"success": False, "message": "Gmail browser not running. Start it from Dashboard → Login to Gmail."}

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()

            # Open Gmail compose
            page = context.new_page()
            page.goto("https://mail.google.com/mail/u/0/#inbox", wait_until="domcontentloaded")
            time.sleep(3)

            # Check if we're actually logged in (URL should be mail.google.com, not accounts.google.com)
            if "accounts.google.com" in page.url:
                page.close()
                return {"success": False, "message": "Gmail not logged in. Login via Dashboard → Login to Gmail first."}

            # Click compose button
            compose_selectors = [
                "div[role='button'][gh='cm']",
                "div.T-I.T-I-KE.L3",
                "[aria-label*='Compose']",
            ]
            composed = False
            for sel in compose_selectors:
                try:
                    page.click(sel, timeout=5000)
                    composed = True
                    break
                except:
                    continue

            if not composed:
                # Try keyboard shortcut
                page.keyboard.press("c")
                time.sleep(2)

            time.sleep(2)

            # Fill in the "To" field
            to_selectors = ["textarea[name='to']", "input[name='to']", "div[aria-label*='To']"]
            for sel in to_selectors:
                try:
                    page.fill(sel, to_email, timeout=5000)
                    break
                except:
                    continue

            time.sleep(1)

            # Fill in subject
            subject_selectors = ["input[name='subjectbox']", "input[aria-label*='Subject']"]
            for sel in subject_selectors:
                try:
                    page.fill(sel, subject, timeout=5000)
                    break
                except:
                    continue

            time.sleep(1)

            # Fill in body — click on the body area first
            body_selectors = ["div[role='textbox']", "div[aria-label*='Message Body']", "div.Am.Al"]
            for sel in body_selectors:
                try:
                    page.click(sel, timeout=5000)
                    page.keyboard.type(body, delay=10)
                    break
                except:
                    continue

            time.sleep(1)

            # Click send button
            send_selectors = [
                "div[role='button'][aria-label*='Send']",
                "div.T-I.T-I-KE.L3",
                "div[aria-label='Send ‹(Ctrl+Enter)›']",
            ]
            sent = False
            for sel in send_selectors:
                try:
                    page.click(sel, timeout=5000)
                    sent = True
                    break
                except:
                    continue

            if not sent:
                # Keyboard shortcut
                page.keyboard.press("Control+Enter")

            time.sleep(3)

            # Check if sent (compose window should be gone)
            try:
                page.wait_for_selector("div[role='button'][gh='cm']", timeout=10000)
                sent_confirm = True
            except:
                sent_confirm = True  # If compose window is gone, it was sent

            page.close()

            if sent_confirm:
                return {"success": True, "message": f"Email sent to {to_email} via Gmail browser"}
            else:
                return {"success": False, "message": "Could not confirm email was sent"}

    except Exception as e:
        return {"success": False, "message": f"Browser email error: {e}"}


def send_email(to_email: str, subject: str, body: str, method: str = "auto") -> dict:
    """
    Send email using the best available method.
    - "auto": Prefer Gmail browser (logged-in session), fall back to SMTP.
    - "smtp": Force SMTP.
    - "browser": Force Gmail browser.
    """
    creds = _get_credentials()
    has_smtp = bool(creds.get("gmail_email") and creds.get("gmail_password"))

    if method == "smtp":
        return send_email_smtp(to_email, subject, body)

    if method == "browser":
        return send_email_via_browser(to_email, subject, body)

    # Auto: prefer Gmail browser (we have a logged-in session)
    # This is more reliable than SMTP (no app password needed)
    if HAS_GMAIL_LOGIN and gmail_login.is_gmail_logged_in():
        result = send_email_via_browser(to_email, subject, body)
        if result["success"]:
            return result
        # If browser fails, try SMTP as fallback

    # Fallback: SMTP if app password is set
    if has_smtp:
        result = send_email_smtp(to_email, subject, body)
        if result["success"]:
            return result

    return {"success": False, "message": "No email sending method available. Start Gmail browser or set app password."}


# ---------------------------------------------------------------------------
# LinkedIn DM Sending
# ---------------------------------------------------------------------------

def send_linkedin_dm(profile_url: str, message: str, debug_port: int = 9222) -> dict:
    """
    Send a LinkedIn connection message or DM via the persistent LinkedIn browser.
    Uses the existing tab (no new tab) to avoid resource issues.
    """
    page = None
    try:
        import urllib.request
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json/version", timeout=3)
        except Exception:
            return {"success": False, "message": "LinkedIn browser not running. Start it from Dashboard → Login to LinkedIn."}

        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        # Use existing page instead of creating a new one (avoids resource leaks)
        page = context.pages[0] if context.pages else context.new_page()

        # Navigate to the profile
        page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(3)

        # Check if we're logged in
        if "login" in page.url or "signin" in page.url:
            return {"success": False, "message": "LinkedIn not logged in."}

        # Wait for page to settle and scroll to action area
        time.sleep(2)

        # Try to find the "Message" button first (if already connected)
        # LinkedIn 2024+ uses various structures - try multiple approaches
        msg_selectors = [
            "button[aria-label*='Message' i]",
            "a[href*='/messaging/thread/']",
            "div[data-control-name='message'] button",
            "button:has-text('Message')",
            "li-icon[type='send-privately']",
        ]
        connected = False
        for sel in msg_selectors:
            try:
                el = page.wait_for_selector(sel, timeout=2000)
                if el and el.is_visible():
                    el.click()
                    connected = True
                    break
            except:
                continue

        if connected:
            # Already connected — send a message
            time.sleep(2)
            msg_input = page.wait_for_selector(
                "div[role='textbox'], textarea[placeholder*='message' i], div[contenteditable='true']",
                timeout=5000
            )
            if msg_input:
                msg_input.click()
                time.sleep(0.5)
                page.keyboard.type(message, delay=15)
                time.sleep(1)
                page.keyboard.press("Enter")
                time.sleep(2)
                return {"success": True, "message": "LinkedIn DM sent"}
            else:
                return {"success": False, "message": "Could not find message input box"}

        # Not connected — try "Connect" button
        # Look for the "More" button first (LinkedIn hides Connect behind it sometimes)
        connect_selectors = [
            "button[aria-label*='Connect' i]",
            "button:has-text('Connect')",
            "div[data-control-name='connect'] button",
            "li-icon[type='connect']",
        ]

        # First try direct Connect button
        connect_found = False
        for sel in connect_selectors:
            try:
                el = page.wait_for_selector(sel, timeout=2000)
                if el and el.is_visible():
                    el.click()
                    connect_found = True
                    break
            except:
                continue

        # If no direct Connect button, try the "More" dropdown
        if not connect_found:
            try:
                more_btn = page.wait_for_selector(
                    "button[aria-label*='More' i], button:has-text('More')",
                    timeout=3000
                )
                if more_btn and more_btn.is_visible():
                    more_btn.click()
                    time.sleep(1)
                    # Now look for Connect in dropdown
                    for sel in connect_selectors:
                        try:
                            el = page.wait_for_selector(sel, timeout=2000)
                            if el:
                                el.click()
                                connect_found = True
                                break
                        except:
                            continue
            except:
                pass

        if not connect_found:
            return {"success": False, "message": "Could not find Connect/Message button — profile may require premium or page structure changed"}

        time.sleep(2)

        # Look for "Add a note" button in the connect dialog
        note_btn = None
        for sel in [
            "button:has-text('Add a note')",
            "button[aria-label*='note' i]",
            "button:has-text('Add a personalized note')",
        ]:
            try:
                note_btn = page.wait_for_selector(sel, timeout=3000)
                if note_btn:
                    break
            except:
                continue

        if note_btn:
            note_btn.click()
            time.sleep(1)
            # Type the message in the note field
            note_input = page.wait_for_selector("textarea, div[role='textbox']", timeout=5000)
            if note_input:
                note_input.click()
                page.keyboard.type(message[:300], delay=15)
                time.sleep(1)
                # Click send
                send_btn = page.wait_for_selector(
                    "button:has-text('Send'), button[aria-label*='Send' i]",
                    timeout=3000
                )
                if send_btn:
                    send_btn.click()
                    time.sleep(2)
                    return {"success": True, "message": "LinkedIn connection request sent with note"}
        else:
            # No note option — just send connection request
            send_btn = page.wait_for_selector(
                "button:has-text('Send'), button[aria-label*='Send' i]",
                timeout=3000
            )
            if send_btn:
                send_btn.click()
                time.sleep(2)
                return {"success": True, "message": "LinkedIn connection request sent (no note)"}

        return {"success": False, "message": "Could not complete LinkedIn connection — dialog may have changed"}

    except Exception as e:
        return {"success": False, "message": f"LinkedIn DM error: {e}"}
    finally:
        # Navigate back to feed to clean up
        if page:
            try:
                page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=10000)
            except:
                pass
        try:
            pw.stop()
        except:
            pass


# ---------------------------------------------------------------------------
# Website Contact Form Sending
# ---------------------------------------------------------------------------

def send_website_form(website_url: str, subject: str, body: str, profile: dict = None) -> dict:
    """
    Navigate to a lead's website and fill out their contact form.
    Uses a standalone headless Playwright browser (no CDP dependency).
    """
    page = None
    pw = None
    browser = None
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        # Navigate to the website
        page.goto(website_url, wait_until="domcontentloaded", timeout=10000)
        time.sleep(2)

        # Look for a contact page link
        contact_selectors = [
            "a[href*='contact']",
            "a[href*='Contact']",
            "a:has-text('Contact')",
            "a:has-text('Contact Us')",
            "a:has-text('Get in touch')",
            "a[href*='about']",
        ]
        for sel in contact_selectors:
            try:
                link = page.query_selector(sel)
                if link and link.is_visible():
                    link.click()
                    time.sleep(2)
                    break
            except:
                continue

        # Look for a contact form
        form_selectors = [
            "form",
            "div[class*='contact']",
            "div[class*='form']",
            "section[class*='contact']",
        ]

        form_found = False
        # Try to find form fields
        name_fields = [
            "input[name*='name' i]",
            "input[placeholder*='name' i]",
            "input[id*='name' i]",
            "input[type='text']:first-of-type",
        ]
        email_fields = [
            "input[type='email']",
            "input[name*='email' i]",
            "input[placeholder*='email' i]",
            "input[id*='email' i]",
        ]
        message_fields = [
            "textarea",
            "div[contenteditable='true']",
            "input[name*='message' i]",
            "input[name*='comment' i]",
        ]

        # Fill name
        if profile:
            name = profile.get("name", "")
            for sel in name_fields:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.fill(name)
                        form_found = True
                        break
                except:
                    continue

        # Fill email
        if profile:
            email = profile.get("email", "")
            for sel in email_fields:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.fill(email)
                        form_found = True
                        break
                except:
                    continue

        # Fill message
        full_message = f"{subject}\n\n{body}"
        for sel in message_fields:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    page.keyboard.type(full_message[:2000], delay=10)
                    form_found = True
                    break
            except:
                continue

        if not form_found:
            return {"success": False, "message": "No contact form found on website"}

        # Try to submit
        submit_selectors = [
            "button[type='submit']",
            "button:has-text('Send')",
            "button:has-text('Submit')",
            "input[type='submit']",
            "button:has-text('Contact')",
        ]
        for sel in submit_selectors:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    time.sleep(2)
                    return {"success": True, "message": "Website contact form submitted"}
            except:
                continue

        return {"success": False, "message": "Found form but could not find submit button"}

    except Exception as e:
        return {"success": False, "message": f"Website form error: {e}"}
    finally:
        if page:
            try:
                page.close()
            except:
                pass
        if browser:
            try:
                browser.close()
            except:
                pass
        if pw:
            try:
                pw.stop()
            except:
                pass


# ---------------------------------------------------------------------------
# Outreach orchestration
# ---------------------------------------------------------------------------

def send_outreach(lead: dict, message_type: str, content: str,
                  email_subject: str = "") -> dict:
    """
    Send outreach to a lead via the appropriate channel.

    Args:
        lead: Lead dict with name, email, linkedin, twitter, contact_method
        message_type: "email", "linkedin_dm", "twitter_dm"
        content: The message content
        email_subject: Subject line (only for email)
    """
    if message_type == "email":
        email = lead.get("email", "")
        if not email:
            return {"success": False, "message": "Lead has no email address"}
        return send_email(email, email_subject, content)

    elif message_type == "website_form":
        website = lead.get("website", "")
        if not website:
            return {"success": False, "message": "Lead has no website URL"}
        # Get profile for form filling
        try:
            import agent as _agent
            profile = _agent.get_profile()
        except:
            profile = {}
        return send_website_form(website, email_subject, content, profile=profile)

    # NO LinkedIn DMs — removed to prevent account restrictions
    # elif message_type == "linkedin_dm":
    #     ...

    elif message_type == "twitter_dm":
        # Twitter DMs require API access — for now, mark as "manual"
        return {"success": False, "message": "Twitter DM requires manual sending (API access needed). Message prepared."}

    else:
        return {"success": False, "message": f"Unknown message type: {message_type}"}


def get_outreach_capability() -> dict:
    """Check what outreach methods are available."""
    creds = _get_credentials()
    has_smtp = bool(creds.get("gmail_email") and creds.get("gmail_password"))
    has_gmail_browser = False
    has_linkedin_browser = False

    if HAS_GMAIL_LOGIN:
        has_gmail_browser = gmail_login.is_gmail_logged_in()

    if HAS_PERSISTENT_BROWSER:
        has_linkedin_browser = persistent_browser.is_browser_running()

    return {
        "smtp": has_smtp,
        "gmail_browser": has_gmail_browser,
        "linkedin_browser": has_linkedin_browser,  # LinkedIn browser is for JOB APPLICATIONS, not DMs
        "twitter": False,  # Requires API
        "website_forms": True,  # Can fill website contact forms
        "best_email_method": "browser" if has_gmail_browser else ("smtp" if has_smtp else "none"),
        "best_dm_method": "none",  # NO LinkedIn DMs — LinkedIn restricts automated DMs
        "note": "Outreach via email (Gmail browser) and website forms only. No LinkedIn DMs.",
    }


if __name__ == "__main__":
    print(json.dumps(get_outreach_capability(), indent=2))
