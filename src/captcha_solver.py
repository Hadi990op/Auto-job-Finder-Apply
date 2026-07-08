"""
CAPTCHA Solver Module
Supports 2Captcha API for solving reCAPTCHA v2/v3 and image captchas.

2Captcha API:
  - Sign up at https://2captcha.com
  - Deposit $5 (gives ~6500 captcha solves)
  - Get API key from dashboard
  - Cost: $0.77 per 1000 reCAPTCHA v2 solves
  - Average solve time: 10-30 seconds

Usage:
  from captcha_solver import solve_recaptcha, solve_image_captcha
  
  # Solve reCAPTCHA v2
  token = solve_recaptcha(api_key, site_key, page_url)
  if token:
      # Set token in page
      page.evaluate(f'document.getElementById("g-recaptcha-response").value = "{token}"')
"""

import time
import requests
import json
from typing import Optional

# 2Captcha API endpoints
TWO_CAPTCHA_SUBMIT = "https://2captcha.com/in.php"
TWO_CAPTCHA_RESULT = "https://2captcha.com/res.php"


def solve_recaptcha_v2(api_key: str, site_key: str, page_url: str, timeout: int = 120) -> Optional[str]:
    """
    Solve reCAPTCHA v2 using 2Captcha API.
    
    Args:
        api_key: 2Captcha API key
        site_key: reCAPTCHA site key (data-sitekey attribute)
        page_url: URL of the page with reCAPTCHA
        timeout: Maximum seconds to wait for solution
    
    Returns:
        reCAPTCHA token (g-recaptcha-response) or None if failed
    """
    if not api_key:
        return None
    
    try:
        # Submit captcha to 2Captcha
        print(f"  [Captcha] Submitting reCAPTCHA to 2Captcha...")
        response = requests.post(TWO_CAPTCHA_SUBMIT, data={
            "key": api_key,
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }, timeout=30)
        
        result = response.json()
        if result.get("status") != 1:
            print(f"  [Captcha] Submit failed: {result.get('request', 'unknown error')}")
            return None
        
        captcha_id = result["request"]
        print(f"  [Captcha] Submitted (ID: {captcha_id}), waiting for solution...")
        
        # Poll for result
        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(5)  # Wait 5 seconds between polls
            elapsed = int(time.time() - start_time)
            print(f"  [Captcha] Waiting... ({elapsed}s elapsed)")
            
            response = requests.get(TWO_CAPTCHA_RESULT, params={
                "key": api_key,
                "action": "get",
                "id": captcha_id,
                "json": 1,
            }, timeout=30)
            
            result = response.json()
            if result.get("status") == 1:
                token = result["request"]
                print(f"  [Captcha] ✅ Solved! Token: {token[:30]}...")
                return token
            elif result.get("request") == "CAPCHA_NOT_READY":
                continue
            else:
                print(f"  [Captcha] Error: {result.get('request', 'unknown')}")
                return None
        
        print(f"  [Captcha] Timeout after {timeout}s")
        return None
        
    except Exception as e:
        print(f"  [Captcha] Error: {e}")
        return None


def solve_recaptcha_v3(api_key: str, site_key: str, page_url: str, action: str = "", score: float = 0.7, timeout: int = 120) -> Optional[str]:
    """
    Solve reCAPTCHA v3 using 2Captcha API.
    """
    if not api_key:
        return None
    
    try:
        print(f"  [Captcha] Submitting reCAPTCHA v3 to 2Captcha...")
        response = requests.post(TWO_CAPTCHA_SUBMIT, data={
            "key": api_key,
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": page_url,
            "action": action,
            "score": score,
            "json": 1,
        }, timeout=30)
        
        result = response.json()
        if result.get("status") != 1:
            print(f"  [Captcha] Submit failed: {result.get('request')}")
            return None
        
        captcha_id = result["request"]
        print(f"  [Captcha] Submitted (ID: {captcha_id})")
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(5)
            response = requests.get(TWO_CAPTCHA_RESULT, params={
                "key": api_key,
                "action": "get",
                "id": captcha_id,
                "json": 1,
            }, timeout=30)
            
            result = response.json()
            if result.get("status") == 1:
                return result["request"]
            elif result.get("request") == "CAPCHA_NOT_READY":
                continue
            else:
                return None
        
        return None
        
    except Exception as e:
        print(f"  [Captcha] Error: {e}")
        return None


def solve_image_captcha(api_key: str, image_base64: str, timeout: int = 60) -> Optional[str]:
    """
    Solve a regular image captcha (distorted text) using 2Captcha.
    
    Args:
        api_key: 2Captcha API key
        image_base64: Base64-encoded captcha image
        timeout: Maximum seconds to wait
    
    Returns:
        Text solution or None
    """
    if not api_key:
        return None
    
    try:
        print(f"  [Captcha] Submitting image captcha to 2Captcha...")
        response = requests.post(TWO_CAPTCHA_SUBMIT, data={
            "key": api_key,
            "method": "base64",
            "body": image_base64,
            "json": 1,
        }, timeout=30)
        
        result = response.json()
        if result.get("status") != 1:
            print(f"  [Captcha] Submit failed: {result.get('request')}")
            return None
        
        captcha_id = result["request"]
        print(f"  [Captcha] Submitted (ID: {captcha_id})")
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(5)
            response = requests.get(TWO_CAPTCHA_RESULT, params={
                "key": api_key,
                "action": "get",
                "id": captcha_id,
                "json": 1,
            }, timeout=30)
            
            result = response.json()
            if result.get("status") == 1:
                print(f"  [Captcha] ✅ Solved: {result['request']}")
                return result["request"]
            elif result.get("request") == "CAPCHA_NOT_READY":
                continue
            else:
                return None
        
        return None
        
    except Exception as e:
        print(f"  [Captcha] Error: {e}")
        return None


def get_recaptcha_site_key(page) -> Optional[str]:
    """
    Extract reCAPTCHA site key from a Playwright page.
    Looks for data-sitekey attribute or grecaptcha render call.
    """
    try:
        # Method 1: Look for data-sitekey attribute
        site_key = page.evaluate(r"""
            () => {
                // Check for div with data-sitekey
                const elem = document.querySelector('[data-sitekey]');
                if (elem) return elem.getAttribute('data-sitekey');
                
                // Check for grecaptcha.render calls in scripts
                const scripts = document.querySelectorAll('script');
                for (const s of scripts) {
                    const match = s.textContent.match(/grecaptcha\.render\([^)]*sitekey['"]\s*:\s*['"]([^'"]+)['"]/);
                    if (match) return match[1];
                }
                
                // Check iframe src
                const iframe = document.querySelector('iframe[src*="recaptcha"]');
                if (iframe) {
                    const match = iframe.src.match(/[?&]k=([^&]+)/);
                    if (match) return match[1];
                }
                
                return null;
            }
        """)
        
        if site_key:
            print(f"  [Captcha] Found reCAPTCHA site key: {site_key[:20]}...")
            return site_key
        
        # Method 2: Look in iframe URLs
        for frame in page.frames:
            if "recaptcha" in frame.url and "anchor" in frame.url:
                import urllib.parse
                parsed = urllib.parse.urlparse(frame.url)
                params = urllib.parse.parse_qs(parsed.query)
                if "k" in params:
                    site_key = params["k"][0]
                    print(f"  [Captcha] Found site key from iframe: {site_key[:20]}...")
                    return site_key
        
        return None
        
    except Exception as e:
        print(f"  [Captcha] Error finding site key: {e}")
        return None


def apply_recaptcha_token(page, token: str) -> bool:
    """
    Apply reCAPTCHA token to the page.
    Sets the g-recaptcha-response value and triggers callback.
    """
    try:
        # Set the token in the textarea
        page.evaluate(f"""
            () => {{
                // Set token in textarea
                const textarea = document.getElementById('g-recaptcha-response');
                if (textarea) {{
                    textarea.style.display = 'block';
                    textarea.value = '{token}';
                }}
                
                // Also try setting via data-callback
                const widget = document.querySelector('[data-callback]');
                if (widget) {{
                    const callbackName = widget.getAttribute('data-callback');
                    if (callbackName && window[callbackName]) {{
                        window[callbackName]('{token}');
                    }}
                }}
            }}
        """)
        
        # Try calling the reCAPTCHA callback directly
        try:
            page.evaluate(f"""
                () => {{
                    if (typeof ___grecaptcha_cfg !== 'undefined') {{
                        const clients = ___grecaptcha_cfg.clients;
                        for (const key of Object.keys(clients)) {{
                            const client = clients[key];
                            // Try to find and call the callback
                            const callback = client?.callback?.(null, '{token}');
                        }}
                    }}
                }}
            """)
        except Exception:
            pass
        
        print(f"  [Captcha] Token applied to page")
        return True
        
    except Exception as e:
        print(f"  [Captcha] Error applying token: {e}")
        return False


def get_2captcha_balance(api_key: str) -> Optional[float]:
    """Check 2Captcha account balance."""
    try:
        response = requests.get(TWO_CAPTCHA_RESULT, params={
            "key": api_key,
            "action": "getbalance",
            "json": 1,
        }, timeout=15)
        result = response.json()
        if result.get("status") == 1:
            return float(result["request"])
        return None
    except Exception:
        return None
