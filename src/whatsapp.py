"""
WhatsApp Integration — sends notifications via WhatsApp Web (no API key needed).

Uses Playwright to automate WhatsApp Web in a headless browser.
Two login methods:
  1. Pairing code (8-digit code entered on phone) — DEFAULT, no QR scanning needed
  2. QR code — scans with phone camera

Both methods link a device to WhatsApp Web without paid API keys.
"""

import os
import time
import json
import re
import base64
import threading
import urllib.parse
from pathlib import Path
from typing import Optional

WHATSAPP_DATA_DIR = Path(__file__).parent / "data" / "whatsapp"
WHATSAPP_DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = WHATSAPP_DATA_DIR / "config.json"
SESSION_DIR = WHATSAPP_DATA_DIR / "session"

# Modern Chrome UA — WhatsApp blocks old Chromium versions
MODERN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Try to import playwright
HAS_PLAYWRIGHT = False
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    HAS_PLAYWRIGHT = True
except ImportError:
    print("[WhatsApp] Playwright not installed. Run: pip install playwright && python -m playwright install chromium")


def get_config() -> dict:
    """Get WhatsApp config."""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {"phone": "", "connected": False}


def save_config(config: dict):
    """Save WhatsApp config."""
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def is_connected() -> bool:
    """Check if WhatsApp is connected."""
    return get_config().get("connected", False)


def _launch_browser():
    """Launch persistent browser context with modern UA."""
    p = sync_playwright().start()
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(SESSION_DIR),
        headless=True,
        args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled"],
        user_agent=MODERN_UA,
        viewport={"width": 1280, "height": 720},
        locale="en-US",
    )
    return p, context


def get_qr_code() -> Optional[str]:
    """
    Start WhatsApp Web and capture the QR code as base64 image.
    Returns base64-encoded PNG of the QR code, "ALREADY_CONNECTED" if logged in, or None on error.
    """
    if not HAS_PLAYWRIGHT:
        return None

    config = get_config()
    if config.get("connected"):
        return "ALREADY_CONNECTED"

    p = None
    context = None
    try:
        p, context = _launch_browser()
        page = context.new_page()
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)
        time.sleep(10)

        # Check if already logged in
        try:
            page.wait_for_selector("div[data-testid='chat-list']", timeout=5000)
            config["connected"] = True
            save_config(config)
            context.close()
            p.stop()
            return "ALREADY_CONNECTED"
        except:
            pass

        # Try to get QR code canvas
        for selector in [
            "canvas[aria-label='Scan me!']",
            "canvas[aria-label='Scan this QR code']",
            "div[data-testid='link-device-qr-code'] canvas",
            "div[data-ref] canvas",
            "div[data-testid='link-device-qr-code']",
            "canvas",
        ]:
            try:
                qr_element = page.wait_for_selector(selector, timeout=8000)
                if qr_element:
                    qr_screenshot = qr_element.screenshot()
                    result = base64.b64encode(qr_screenshot).decode()
                    context.close()
                    p.stop()
                    return result
            except:
                continue

        context.close()
        p.stop()
        return None
    except Exception as e:
        print(f"[WhatsApp] QR error: {e}")
        try:
            if context: context.close()
            if p: p.stop()
        except:
            pass
        return None


def get_pairing_code(phone_number: str) -> dict:
    """
    Get a pairing code for linking WhatsApp Web with a phone number.
    
    The user enters this 8-digit code on their phone:
      WhatsApp → Settings → Linked Devices → Link a Device → Link with phone number
    
    Args:
        phone_number: Full phone number with country code (e.g., +923225490551)
    
    Returns:
        {"success": True, "code": "ABCD-EFGH"} or {"success": False, "error": "..."}
    """
    if not HAS_PLAYWRIGHT:
        return {"success": False, "error": "Playwright not installed"}

    # Clean phone number
    clean_phone = phone_number.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not clean_phone.isdigit():
        return {"success": False, "error": "Invalid phone number. Use format: +923225490551"}

    # Save phone to config
    config = get_config()
    config["phone"] = phone_number
    save_config(config)

    p = None
    context = None
    try:
        p, context = _launch_browser()
        page = context.new_page()
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)
        time.sleep(15)

        # Check if already logged in
        try:
            page.wait_for_selector("div[data-testid='chat-list']", timeout=5000)
            config["connected"] = True
            save_config(config)
            context.close()
            p.stop()
            return {"success": True, "already_connected": True}
        except:
            pass

        # Click "Link with phone number instead" button
        print("[WhatsApp] Clicking phone number link...")
        try:
            phone_link = page.locator("[data-testid='link-device-qrcode-alt-linking-hint']").first
            phone_link.click(force=True, timeout=30000)
            time.sleep(5)
        except Exception as e:
            print(f"[WhatsApp] Could not click phone link: {e}")
            # Take a screenshot for debugging
            try:
                page.screenshot(path=str(WHATSAPP_DATA_DIR / "phone_link_error.png"))
            except:
                pass
            context.close()
            p.stop()
            return {"success": False, "error": "Could not access phone number login. WhatsApp layout may have changed."}

        # Enter phone number — type full number with + prefix, WhatsApp auto-detects country
        print("[WhatsApp] Entering phone number...")
        try:
            phone_input = page.locator("[data-testid='phone-number-input']").first
            phone_input.click()
            time.sleep(0.5)
            phone_input.fill("")
            time.sleep(0.3)
            phone_input.type("+" + clean_phone, delay=30)
            time.sleep(1)
            val = phone_input.get_attribute("value") or ""
            print(f"[WhatsApp] Typed: {val}")
        except Exception as e:
            print(f"[WhatsApp] Phone input error: {e}")

        # Click Next button
        print("[WhatsApp] Clicking Next...")
        try:
            # Look for Next button
            next_clicked = False
            for selector in [
                "button:has-text('Next')",
                "[role='button']:has-text('Next')",
                "div:has-text('Next')",
            ]:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible():
                        btn.click(force=True, timeout=5000)
                        next_clicked = True
                        time.sleep(3)
                        break
                except:
                    continue
            
            if not next_clicked:
                # Try pressing Enter
                page.keyboard.press("Enter")
                time.sleep(3)
        except Exception as e:
            print(f"[WhatsApp] Next button error: {e}")

        # Check for error messages (invalid phone number)
        time.sleep(3)
        try:
            page_text = page.evaluate("() => document.body.innerText")
            if "Valid phone number is required" in page_text or "invalid" in page_text.lower():
                # Take screenshot for debugging
                screenshot = page.screenshot()
                with open(str(WHATSAPP_DATA_DIR / "phone_error.png"), "wb") as f:
                    f.write(screenshot)
                context.close()
                p.stop()
                return {"success": False, "error": "Invalid phone number. Check the number and try again."}
        except:
            pass

        # Wait for pairing code to appear
        print("[WhatsApp] Waiting for pairing code...")
        pairing_code = None
        
        for attempt in range(15):  # Wait up to 30 seconds
            time.sleep(2)
            try:
                # Only use the specific data-testid element — page text has false positives
                code_cell = page.locator("[data-testid='link-with-phone-number-code-cells']").first
                if code_cell.count() > 0 and code_cell.is_visible():
                    raw_text = code_cell.inner_text()
                    # Remove all whitespace/newlines and format as XXXX-XXXX
                    clean_code = re.sub(r'\s+', '', raw_text)
                    # The code is 8 chars + a hyphen, e.g., "2W6P-JJSD"
                    # Pairing codes always contain letters, so require at least one letter
                    code_match = re.search(r'([A-Z0-9]{4})-?([A-Z0-9]{4})', clean_code)
                    if code_match:
                        part1 = code_match.group(1)
                        part2 = code_match.group(2)
                        # Reject if it's all digits (that's a phone number, not a pairing code)
                        has_letter1 = any(c.isalpha() for c in part1)
                        has_letter2 = any(c.isalpha() for c in part2)
                        if has_letter1 or has_letter2:
                            pairing_code = f"{part1}-{part2}"
                            print(f"[WhatsApp] Found pairing code: {pairing_code}")
                            break
            except:
                continue

        # Take a screenshot of current state for debugging
        try:
            screenshot = page.screenshot()
            with open(str(WHATSAPP_DATA_DIR / "pairing_state.png"), "wb") as f:
                f.write(screenshot)
        except:
            pass

        if pairing_code:
            # Keep browser open in background thread — user needs to enter code on phone
            # The session will be saved when they complete the linking
            config["pairing_code"] = pairing_code
            config["pairing_time"] = time.time()
            save_config(config)
            
            # Wait for linking to complete (user enters code on phone)
            # Check for chat list appearing
            print("[WhatsApp] Waiting for user to enter code on phone...")
            connected = False
            for wait in range(120):  # Wait up to 4 minutes
                time.sleep(2)
                try:
                    page.wait_for_selector("div[data-testid='chat-list']", timeout=2000)
                    connected = True
                    break
                except:
                    # Check if still on pairing screen
                    try:
                        page_text = page.evaluate("() => document.body.innerText")
                        if "incorrect" in page_text.lower() or "expired" in page_text.lower():
                            break
                    except:
                        pass

            if connected:
                config["connected"] = True
                config.pop("pairing_code", None)
                save_config(config)
                print("[WhatsApp] Connected successfully!")
                context.close()
                p.stop()
                return {"success": True, "code": pairing_code, "connected": True}
            else:
                # Save session anyway — user might complete it later
                context.close()
                p.stop()
                return {"success": True, "code": pairing_code, "connected": False, 
                        "message": "Code generated. Enter it on your phone within 60 seconds."}
        else:
            context.close()
            p.stop()
            return {"success": False, "error": "Could not get pairing code. The phone number might be invalid or WhatsApp changed their layout."}

    except Exception as e:
        print(f"[WhatsApp] Pairing error: {e}")
        try:
            if context: context.close()
            if p: p.stop()
        except:
            pass
        return {"success": False, "error": str(e)}


def check_connection() -> bool:
    """Check if WhatsApp Web session is still valid."""
    if not HAS_PLAYWRIGHT:
        return False

    if not SESSION_DIR.exists():
        return False

    p = None
    context = None
    try:
        p, context = _launch_browser()
        page = context.new_page()
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)
        time.sleep(10)

        try:
            page.wait_for_selector("div[data-testid='chat-list']", timeout=10000)
            config = get_config()
            config["connected"] = True
            save_config(config)
            context.close()
            p.stop()
            return True
        except:
            config = get_config()
            config["connected"] = False
            save_config(config)

        context.close()
        p.stop()
        return False
    except Exception as e:
        print(f"[WhatsApp] Connection check error: {e}")
        try:
            if context: context.close()
            if p: p.stop()
        except:
            pass
        return False


def send_message(phone: str, message: str) -> dict:
    """
    Send a WhatsApp message to a phone number.
    Returns {"success": bool, "error": str}
    """
    if not HAS_PLAYWRIGHT:
        return {"success": False, "error": "Playwright not installed"}

    if not SESSION_DIR.exists():
        return {"success": False, "error": "WhatsApp not connected. Pair your phone first."}

    clean_phone = phone.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    p = None
    context = None
    try:
        p, context = _launch_browser()
        page = context.new_page()
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # Check if logged in
        try:
            page.wait_for_selector("div[data-testid='chat-list']", timeout=15000)
        except:
            context.close()
            p.stop()
            config = get_config()
            config["connected"] = False
            save_config(config)
            return {"success": False, "error": "WhatsApp session expired. Re-pair your phone."}

        # Navigate directly to the chat with the phone number
        encoded_text = urllib.parse.quote(message)
        page.goto(f"https://web.whatsapp.com/send?phone={clean_phone}&text={encoded_text}", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        # Wait for the chat to load
        try:
            page.wait_for_selector("footer div[contenteditable='true']", timeout=15000)
        except:
            try:
                page.wait_for_selector("div[data-testid='conversation-info-panel']", timeout=10000)
            except:
                context.close()
                p.stop()
                return {"success": False, "error": "Could not open chat with this number"}

        time.sleep(2)

        # Find and click the send button
        try:
            send_button = page.wait_for_selector("button[data-testid='concat-message-send-button']", timeout=5000)
            send_button.click()
            time.sleep(2)
            print(f"[WhatsApp] Message sent to {clean_phone}")
            context.close()
            p.stop()
            return {"success": True, "error": ""}
        except:
            try:
                page.keyboard.press("Enter")
                time.sleep(2)
                print(f"[WhatsApp] Message sent (via Enter) to {clean_phone}")
                context.close()
                p.stop()
                return {"success": True, "error": ""}
            except:
                pass

        context.close()
        p.stop()
        return {"success": False, "error": "Could not send message"}

    except Exception as e:
        print(f"[WhatsApp] Send error: {e}")
        try:
            if context: context.close()
            if p: p.stop()
        except:
            pass
        return {"success": False, "error": str(e)}


def send_notification(message: str) -> dict:
    """Send a notification to the configured phone number."""
    config = get_config()
    phone = config.get("phone", "")
    if not phone:
        return {"success": False, "error": "No phone number configured"}
    if not config.get("connected"):
        return {"success": False, "error": "WhatsApp not connected"}
    return send_message(phone, message)


def disconnect():
    """Disconnect WhatsApp (delete session)."""
    import shutil
    if SESSION_DIR.exists():
        shutil.rmtree(SESSION_DIR)
    config = get_config()
    config["connected"] = False
    config.pop("pairing_code", None)
    save_config(config)
    return True
