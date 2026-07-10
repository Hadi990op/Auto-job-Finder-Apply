"""
Proposal Generator — creates personalized, startup-aware proposals for leads.

Uses Pollinations AI (free, no API key) to generate:
  1. Cold email — personalized to the startup and what they're building
  2. LinkedIn DM — shorter, more casual
  3. Twitter DM — very short, punchy
  4. Full proposal document — detailed service offering tailored to their needs

The AI reads the lead's company description, industry, stage, and the user's
profile/services to create a message that shows genuine understanding of
their business — not a generic template.
"""

import json
import urllib.request
import urllib.parse
import random
import time
from typing import Optional


# ---------------------------------------------------------------------------
# AI helper (same pattern as ai_engine.py — Pollinations, free, no key)
# ---------------------------------------------------------------------------

def _ai_chat_get(prompt: str, timeout: int = 45) -> str:
    url = f"https://text.pollinations.ai/{urllib.parse.quote(prompt)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace").strip()
    except Exception as e:
        print(f"  [Proposal AI] GET error: {e}")
        return ""


def _ai_chat_post(messages: list, timeout: int = 90, retries: int = 3) -> str:
    payload = json.dumps({
        "model": "openai",
        "messages": messages,
        "temperature": 0.8,
    }).encode()

    for attempt in range(retries):
        req = urllib.request.Request(
            "https://text.pollinations.ai/openai",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 5 + random.uniform(1, 5)
                print(f"  [Proposal AI] Rate limited, waiting {wait:.1f}s... (attempt {attempt+1}/{retries})")
                time.sleep(wait)
                continue
            print(f"  [Proposal AI] HTTP {e.code}: {e.reason}")
            if attempt < retries - 1:
                time.sleep(3)
                continue
            return ""
        except Exception as e:
            print(f"  [Proposal AI] POST error (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                time.sleep(3)
                continue
            return ""
    return ""


# ---------------------------------------------------------------------------
# Context builders
# ---------------------------------------------------------------------------

def _build_lead_context(lead: dict) -> str:
    """Build a rich description of the lead for AI."""
    return f"""LEAD / POTENTIAL CLIENT:
Name: {lead.get('name', 'Unknown')}
Title: {lead.get('title', '')}
Company: {lead.get('company', 'Unknown')}
Company Website: {lead.get('company_url', '')}
Industry: {lead.get('industry', 'Unknown')}
Stage: {lead.get('stage', 'Unknown')}
Location: {lead.get('location', '')}
Company Description: {lead.get('company_description', 'Not available')}
What they do: {lead.get('description', '')}
Source: {lead.get('source', '')}
Lead Type: {lead.get('lead_type', '')}
Review Text (if freelancer client): {lead.get('review_text', 'N/A')}
Freelancer Platform: {lead.get('freelancer_platform', 'N/A')}
"""


def _build_user_context(profile: dict) -> str:
    """Build a description of the user (service provider / freelancer)."""
    name = profile.get("name", "Unknown")
    current_role = profile.get("current_role", "")
    summary = profile.get("summary", "")
    skills = profile.get("skills", [])
    core_skills = profile.get("core_skills", [])
    years_exp = profile.get("years_experience", "")
    portfolio = profile.get("portfolio", "")
    github = profile.get("github", "")
    linkedin = profile.get("linkedin", "")
    experience_domains = profile.get("experience_domains", [])
    services = profile.get("services", [])  # Custom field for leads — what services the user offers
    rate = profile.get("hourly_rate", "")
    availability = profile.get("availability", "Available immediately")

    exp_text = ""
    experiences = profile.get("experience", [])
    if experiences and isinstance(experiences, list):
        for exp in experiences[:3]:
            if isinstance(exp, dict):
                exp_text += f"  • {exp.get('role', '')} at {exp.get('company', '')}\n"

    projects_text = ""
    projects = profile.get("projects", [])
    if projects and isinstance(projects, list):
        for p in projects[:3]:
            if isinstance(p, dict):
                projects_text += f"  • {p.get('name', '')}: {p.get('description', '')}\n"

    return f"""SERVICE PROVIDER PROFILE:
Name: {name}
Current Role: {current_role}
Years of Experience: {years_exp}
Professional Summary: {summary}
Core Skills: {', '.join(core_skills) if core_skills else 'Not specified'}
All Skills: {', '.join(skills) if skills else 'Not specified'}
Experience Domains: {', '.join(experience_domains) if experience_domains else 'Not specified'}
Services Offered: {', '.join(services) if services else 'Not specified (infer from skills)'}
Hourly Rate: {rate if rate else 'Negotiable'}
Availability: {availability}
Portfolio: {portfolio}
GitHub: {github}
LinkedIn: {linkedin}

RECENT EXPERIENCE:
{exp_text if exp_text else '  Not detailed'}

NOTABLE PROJECTS:
{projects_text if projects_text else '  Not specified'}"""


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def generate_cold_email(profile: dict, lead: dict) -> Optional[str]:
    """
    Generate a personalized cold email for a lead.
    The email references their specific startup/product and connects the
    user's skills to what they're building.
    """
    user_ctx = _build_user_context(profile)
    lead_ctx = _build_lead_context(lead)

    lead_name = lead.get("name", "there")
    company = lead.get("company", "your company")

    prompt = f"""You are an expert at writing cold outreach emails that get responses.
Write a personalized cold email from a freelancer/service provider to a potential client.

{user_ctx}

---
{lead_ctx}

---
INSTRUCTIONS:
1. Write a cold email that is PERSONALIZED to {company} — reference what they specifically do/build
2. Show genuine understanding of their business (read the company description carefully)
3. Connect the sender's skills to what they likely need (based on their industry/stage/product)
4. Be concise (3-4 short paragraphs max)
5. Open with something specific about THEIR company, not a generic greeting
6. Include a clear, low-friction call to action (e.g., "Can I send you a quick proposal?" or "Worth a 15-min call?")
7. Be professional but warm — not corporate, not overly casual
8. If they're a freelancer client (have review history), mention you saw their project needs
9. Do NOT use placeholders like [Your Name] — use the actual name from the profile
10. Sign off with the sender's actual name
11. Output ONLY the email subject line (prefixed with "Subject: ") followed by the email body. No other text.

Write the cold email:"""

    result = _ai_chat_post([
        {"role": "system", "content": "You are an expert cold email writer. You write personalized, high-converting outreach emails that get responses. You research the recipient and tailor every email. Output ONLY the email, no explanations."},
        {"role": "user", "content": prompt}
    ], timeout=90, retries=2)

    if not result:
        result = _ai_chat_get(prompt[:2000], timeout=60)

    return result if result else None


def generate_linkedin_dm(profile: dict, lead: dict) -> Optional[str]:
    """
    Generate a LinkedIn connection message / DM (short, under 300 chars).
    """
    user_ctx = _build_user_context(profile)
    lead_ctx = _build_lead_context(lead)

    company = lead.get("company", "your company")

    prompt = f"""Write a short LinkedIn connection request message (under 300 characters) from a freelancer to a potential client.

{user_ctx}

---
{lead_ctx}

---
RULES:
1. Maximum 300 characters (LinkedIn limit for connection requests)
2. Reference something specific about {company}
3. Be warm, genuine, not salesy
4. End with a soft question or invitation to chat
5. No placeholders — use real names
6. Output ONLY the message, nothing else.

Write the LinkedIn message:"""

    result = _ai_chat_post([
        {"role": "system", "content": "You write concise, personalized LinkedIn outreach messages. Max 300 characters. No fluff."},
        {"role": "user", "content": prompt}
    ], timeout=60, retries=2)

    if not result:
        result = _ai_chat_get(prompt[:1500], timeout=45)

    return result if result else None


def generate_twitter_dm(profile: dict, lead: dict) -> Optional[str]:
    """
    Generate a Twitter/X DM (very short, punchy, under 280 chars for tweet
    or 1000 chars for DM).
    """
    user_ctx = _build_user_context(profile)
    lead_ctx = _build_lead_context(lead)

    company = lead.get("company", "your company")

    prompt = f"""Write a short Twitter/X DM (under 500 characters) from a freelancer to a founder.

{user_ctx}

---
{lead_ctx}

---
RULES:
1. Maximum 500 characters
2. Reference something specific about {company}
3. Be casual, genuine, not salesy
4. End with a question
5. No placeholders
6. Output ONLY the message

Write the DM:"""

    result = _ai_chat_post([
        {"role": "system", "content": "You write concise, casual Twitter DMs for outreach. Max 500 characters."},
        {"role": "user", "content": prompt}
    ], timeout=60, retries=2)

    if not result:
        result = _ai_chat_get(prompt[:1500], timeout=45)

    return result if result else None


def generate_full_proposal(profile: dict, lead: dict) -> Optional[str]:
    """
    Generate a full, detailed proposal document tailored to the lead's
    startup and needs. This is a comprehensive document, not just a message.
    """
    user_ctx = _build_user_context(profile)
    lead_ctx = _build_lead_context(lead)

    company = lead.get("company", "your company")
    industry = lead.get("industry", "their industry")

    prompt = f"""You are an expert proposal writer. Create a detailed, professional service proposal.

{user_ctx}

---
{lead_ctx}

---
INSTRUCTIONS:
1. Create a complete proposal document tailored to {company}
2. Structure it as:
   - GREETING (addressed to the lead by name)
   - UNDERSTANDING (show you understand their business, product, and challenges)
   - WHAT I CAN HELP WITH (3-5 specific services relevant to their industry/stage)
   - APPROACH (brief methodology — how you'd work with them)
   - TIMELINE (rough estimates)
   - INVESTMENT (pricing approach — be flexible, not prescriptive)
   - WHY ME (connect your skills/experience to their specific needs)
   - NEXT STEPS (clear call to action)
3. Make every section specific to {company} and {industry} — not generic
4. Be professional, confident, but not arrogant
5. Use plain text formatting with clear section headers in CAPS
6. If they're a freelancer client, reference their previous projects positively
7. Sign off with the sender's actual name and contact info from the profile
8. Output ONLY the proposal, no explanations

Write the proposal:"""

    result = _ai_chat_post([
        {"role": "system", "content": "You are an expert proposal writer. You create detailed, personalized, professional proposals that win clients. You research the client and tailor every section. Output ONLY the proposal."},
        {"role": "user", "content": prompt}
    ], timeout=120, retries=2)

    if not result:
        result = _ai_chat_get(prompt[:2500], timeout=90)

    return result if result else None


def evaluate_lead_fit(profile: dict, lead: dict) -> Optional[dict]:
    """
    Use AI to evaluate how good a lead is for the user's services.
    Returns a fit score (0-100) and reasoning.
    """
    user_ctx = _build_user_context(profile)
    lead_ctx = _build_lead_context(lead)

    prompt = f"""You are an expert business development advisor.
Evaluate how good a lead this is for the service provider.

{user_ctx}

---
{lead_ctx}

---
TASK: Analyze how well the service provider's skills match what this lead likely needs.
Consider:
1. Does the lead's company/product need the provider's skills?
2. Is the company at a stage where they'd hire freelancers/contractors?
3. Is there a clear value proposition?
4. What's the likelihood of a positive response?

Respond in EXACTLY this format:
SCORE: [number 0-100]
REASON: [one or two sentences]
OUTREACH: [Email / LinkedIn / Twitter / Skip]
ANGLE: [one sentence on the best angle for outreach]"""

    result = _ai_chat_post([
        {"role": "system", "content": "You are an expert business development advisor. Evaluate lead fit precisely. Follow the output format exactly."},
        {"role": "user", "content": prompt}
    ], timeout=25, retries=1)

    if not result:
        result = _ai_chat_get(prompt[:2000], timeout=20)

    if not result:
        return None

    evaluation = {}
    for line in result.split("\n"):
        line = line.strip()
        if line.startswith("SCORE:"):
            try:
                score_str = line.split(":", 1)[1].strip().split("/")[0].strip()
                evaluation["fit_score"] = float(score_str)
            except:
                pass
        elif line.startswith("REASON:"):
            evaluation["reason"] = line.split(":", 1)[1].strip()
        elif line.startswith("OUTREACH:"):
            evaluation["outreach_method"] = line.split(":", 1)[1].strip()
        elif line.startswith("ANGLE:"):
            evaluation["angle"] = line.split(":", 1)[1].strip()

    return evaluation if "fit_score" in evaluation else None


if __name__ == "__main__":
    # Quick test
    test_profile = {
        "name": "Test User",
        "current_role": "Full Stack Developer",
        "skills": ["Python", "React", "AWS", "PostgreSQL"],
        "core_skills": ["Python", "React"],
        "years_experience": "5",
        "summary": "Full stack developer specializing in SaaS products",
    }
    test_lead = {
        "name": "John Doe",
        "company": "TechStartup",
        "company_description": "Building a SaaS platform for small businesses",
        "industry": "SaaS",
        "stage": "Seed",
        "lead_type": "founder",
    }
    email = generate_cold_email(test_profile, test_lead)
    print("=== COLD EMAIL ===")
    print(email)
