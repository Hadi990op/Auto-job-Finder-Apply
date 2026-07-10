"""
AI Engine — Intelligent job matching and document generation.

Uses Pollinations AI (free, no API key) for:
  1. Smart job evaluation — AI reads the job description and profile, 
     understands the ACTUAL requirements (not just keyword matching)
  2. Tailored CV generation — AI reads the job and writes a CV that 
     highlights relevant skills and experience
  3. Cover letter generation — AI writes a personalized cover letter
  4. Profile enhancement — AI fills gaps in the profile

The AI is the brain. It reads everything, understands context, and makes
intelligent decisions about fit scoring and document tailoring.
"""

import json
import time
import urllib.request
import urllib.parse
import random
from typing import Optional


def _ai_chat_get(prompt: str, timeout: int = 30) -> str:
    """Call Pollinations AI via GET endpoint (more reliable, shorter prompts)."""
    url = f"https://text.pollinations.ai/{urllib.parse.quote(prompt)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace").strip()
    except Exception as e:
        print(f"  [AI] GET error: {e}")
        return ""


def _ai_chat_post(messages: list, timeout: int = 60, retries: int = 3) -> str:
    """Call Pollinations AI via POST endpoint (OpenAI-compatible)."""
    payload = json.dumps({
        "model": "openai",
        "messages": messages,
        "temperature": 0.7,
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
                print(f"  [AI] Rate limited, waiting {wait:.1f}s... (attempt {attempt+1}/{retries})")
                time.sleep(wait)
                continue
            print(f"  [AI] HTTP {e.code}: {e.reason}")
            if attempt < retries - 1:
                time.sleep(3)
                continue
            return ""
        except Exception as e:
            print(f"  [AI] POST error (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                time.sleep(3)
                continue
            return ""
    return ""


def _build_profile_context(profile: dict) -> str:
    """Build a rich text description of the candidate's profile for AI."""
    name = profile.get("name", "Unknown")
    email = profile.get("email", "")
    phone = profile.get("phone", "")
    location = profile.get("location", "")
    linkedin = profile.get("linkedin", "")
    github = profile.get("github", "")
    portfolio = profile.get("portfolio", "")
    years_exp = profile.get("years_experience", "")
    current_role = profile.get("current_role", "")
    summary = profile.get("summary", "")
    
    skills = profile.get("skills", [])
    core_skills = profile.get("core_skills", [])
    job_titles = profile.get("job_titles", [])
    exp_domains = profile.get("experience_domains", [])
    career_goals = profile.get("career_goals", [])
    preferred_locations = profile.get("preferred_locations", [])
    languages = profile.get("languages", [])
    
    # Format experience
    experiences = profile.get("experience", [])
    exp_text = ""
    if experiences and isinstance(experiences, list):
        for exp in experiences:
            if isinstance(exp, dict):
                exp_text += f"  • {exp.get('role','')} at {exp.get('company','')} ({exp.get('period','')})"
                if exp.get('location'):
                    exp_text += f" — {exp.get('location')}"
                exp_text += "\n"
                for b in exp.get("bullets", []):
                    exp_text += f"    - {b}\n"
    
    # Format education
    education = profile.get("education", [])
    edu_text = ""
    if education and isinstance(education, list):
        for e in education:
            if isinstance(e, dict):
                edu_text += f"  • {e.get('degree','')} — {e.get('institution','')} ({e.get('year','')})\n"
    
    # Format projects
    projects = profile.get("projects", [])
    proj_text = ""
    if projects and isinstance(projects, list):
        for p in projects:
            if isinstance(p, dict):
                proj_text += f"  • {p.get('name','')}: {p.get('description','')}\n"
    
    context = f"""CANDIDATE PROFILE:
Name: {name}
Email: {email}
Phone: {phone}
Location: {location}
LinkedIn: {linkedin}
GitHub: {github}
Portfolio: {portfolio}
Years of Experience: {years_exp}
Current Role: {current_role}
Professional Summary: {summary}
Core Skills: {', '.join(core_skills) if core_skills else 'Not specified'}
All Skills: {', '.join(skills) if skills else 'Not specified'}
Target Job Titles: {', '.join(job_titles) if job_titles else 'Not specified'}
Experience Domains: {', '.join(exp_domains) if exp_domains else 'Not specified'}
Career Goals: {'; '.join(career_goals) if career_goals else 'Not specified'}
Preferred Locations: {', '.join(preferred_locations) if preferred_locations else 'Not specified'}
Languages: {', '.join(languages) if languages else 'Not specified'}

WORK EXPERIENCE:
{exp_text if exp_text else '  Not detailed'}

EDUCATION:
{edu_text if edu_text else '  Not specified'}

PROJECTS:
{proj_text if proj_text else '  Not specified'}"""
    
    return context


def _build_job_context(job: dict) -> str:
    """Build a rich text description of the job for AI."""
    job_title = job.get("title", "Unknown Position")
    company = job.get("company", "Unknown Company")
    job_location = job.get("location", "")
    job_desc = job.get("description") or ""
    job_tags = job.get("tags", [])
    if isinstance(job_tags, str):
        try:
            job_tags = json.loads(job_tags)
        except:
            job_tags = []
    
    # Truncate description to keep prompt manageable
    if len(job_desc) > 3000:
        job_desc = job_desc[:3000] + "..."
    
    context = f"""JOB DETAILS:
Title: {job_title}
Company: {company}
Location: {job_location}
Source: {job.get('source', '')}
Apply URL: {job.get('apply_url') or job.get('url', '')}
Tags/Keywords: {', '.join(job_tags) if job_tags else 'None'}

JOB DESCRIPTION:
{job_desc}"""
    
    return context


def ai_evaluate_job(profile: dict, job: dict) -> Optional[dict]:
    """
    Use AI to intelligently evaluate job fit.
    
    The AI reads the FULL job description and the candidate's profile,
    understands the actual requirements, and provides:
    - A fit score (0-100)
    - A verdict (Strong/Good/Moderate/Weak/Poor Fit)
    - Which skills match
    - Which skills are missing
    - A reasoning for the score
    - Whether to recommend applying
    """
    profile_ctx = _build_profile_context(profile)
    job_ctx = _build_job_context(job)
    
    auto_apply_threshold = profile.get("auto_apply_threshold", 50)
    
    prompt = f"""You are an expert technical recruiter and career advisor. Evaluate how well a candidate fits a job.

{profile_ctx}

---
{job_ctx}

---
TASK: Analyze the candidate's profile against the job requirements. Consider:
1. Do the candidate's skills match what the job actually requires? (Read the description carefully, not just the title)
2. Is the experience level appropriate?
3. Does the location match the candidate's preferences?
4. Is this a realistic, good match — or a stretch?

Respond in EXACTLY this format (no other text, no markdown):
SCORE: [number 0-100]
VERDICT: [Strong Fit / Good Fit / Moderate Fit / Weak Fit / Poor Fit]
MATCHED: [comma-separated skills and qualities that match]
MISSING: [comma-separated important requirements the candidate lacks, or "None"]
REASON: [one or two sentences explaining the score]
RECOMMEND: [Apply / Consider / Skip]"""

    result = _ai_chat_post([
        {"role": "system", "content": "You are an expert technical recruiter. You evaluate job-candidate fit precisely and honestly. You read job descriptions carefully to understand ACTUAL requirements. Follow the output format exactly."},
        {"role": "user", "content": prompt}
    ], timeout=45, retries=2)

    if not result:
        # Fallback to GET endpoint
        result = _ai_chat_get(prompt[:2000], timeout=45)

    if not result:
        return None

    # Parse the AI response
    evaluation = {}
    for line in result.split("\n"):
        line = line.strip()
        if line.startswith("SCORE:"):
            try:
                score_str = line.split(":", 1)[1].strip()
                # Handle ranges like "75/100" or just "75"
                score_str = score_str.split("/")[0].strip()
                evaluation["overall_score"] = float(score_str)
            except:
                pass
        elif line.startswith("VERDICT:"):
            evaluation["verdict"] = line.split(":", 1)[1].strip()
        elif line.startswith("MATCHED:"):
            matched = line.split(":", 1)[1].strip()
            evaluation["matched_skills"] = [s.strip() for s in matched.split(",") if s.strip() and s.strip() != "None"]
        elif line.startswith("MISSING:"):
            missing = line.split(":", 1)[1].strip()
            evaluation["missing_skills"] = [s.strip() for s in missing.split(",") if s.strip() and s.strip() != "None"]
        elif line.startswith("REASON:"):
            evaluation["reason"] = line.split(":", 1)[1].strip()
        elif line.startswith("RECOMMEND:"):
            evaluation["recommendation"] = line.split(":", 1)[1].strip()

    return evaluation if "overall_score" in evaluation else None


def ai_generate_cv(profile: dict, job: dict) -> str:
    """
    Generate a professional, tailored CV using AI.
    
    The AI reads the FULL job description and the candidate's profile,
    then writes a CV that:
    - Highlights the most relevant skills for THIS specific job
    - Rewrites the summary to match the job requirements
    - Reorders experience to put relevant items first
    - Uses professional language and formatting
    """
    profile_ctx = _build_profile_context(profile)
    job_ctx = _build_job_context(job)
    
    name = profile.get("name", "Your Name")
    job_title = job.get("title", "the position")
    company = job.get("company", "the company")

    # Try to get the user's uploaded CV text to use as reference
    uploaded_cv_text = ""
    try:
        from cv_manager import get_primary_cv
        cv = get_primary_cv()
        if cv and cv.get("text_content"):
            uploaded_cv_text = cv["text_content"]
    except Exception:
        pass

    if uploaded_cv_text:
        prompt = f"""You are an expert CV writer. Create a professional, well-structured CV in plain text format.

CANDIDATE'S ACTUAL RESUME (use this as the base — all facts, skills, education, and experience come from here):
{uploaded_cv_text}

---
TARGET JOB:
{job_ctx}

---
INSTRUCTIONS:
1. Take the candidate's actual resume above as the BASE — do NOT invent experience, skills, or education that are not in it
2. Tailor the CV for THIS specific job at {company}:
   - Reorder and emphasize skills that match the job requirements
   - Adjust the professional summary to highlight relevance to this role
   - Keep all education and certifications from the original resume
3. Do NOT inflate experience or add fake job titles — be honest about the candidate's actual background
4. Use clean plain text format with clear section headers in CAPS
5. Include: CONTACT INFO, PROFESSIONAL SUMMARY, SKILLS, EXPERIENCE (if any), EDUCATION, CERTIFICATIONS
6. Do NOT include any preamble, explanation, or notes — just the CV itself
7. Keep it professional and concise (maximum 80 lines)

Generate the CV now:"""
    else:
        prompt = f"""You are an expert CV writer. Create a professional, well-structured CV in plain text format.

{profile_ctx}

---
{job_ctx}

---
INSTRUCTIONS:
1. Create a complete, professional CV tailored to THIS specific job at {company}
2. Carefully read the job description and identify what they're actually looking for
3. Reorder and emphasize skills from the candidate's profile that match the job requirements
4. Rewrite the professional summary to highlight relevance to this specific role
5. If the candidate has experience entries, reorder bullets to put the most relevant ones first
6. If the candidate's skills are described broadly (e.g., "Full development skills"), expand them into specific, relevant technical skills based on what the job requires — use the job description as a guide for what specific skills to highlight
7. Use clean plain text format with clear section headers in CAPS
8. Include: CONTACT INFO, PROFESSIONAL SUMMARY, SKILLS, EXPERIENCE, EDUCATION
9. Do NOT include any preamble, explanation, or notes — just the CV itself
10. Keep it professional and concise (maximum 70 lines)

Generate the CV now:"""

    result = _ai_chat_post([
        {"role": "system", "content": "You are an expert CV writer. You create professional, tailored CVs in plain text. You read job descriptions carefully and tailor the CV to highlight relevant qualifications. Be honest — never invent experience or skills the candidate does not have. Be concise and impactful. Output ONLY the CV, no explanations."},
        {"role": "user", "content": prompt}
    ], timeout=90, retries=2)

    if not result:
        # Fallback to GET endpoint with shorter prompt
        result = _ai_chat_get(prompt[:2000], timeout=60)

    if not result:
        return None

    return result


def ai_generate_cover_letter(profile: dict, job: dict) -> str:
    """
    Generate a tailored, professional cover letter using AI.
    
    The AI reads the job description and writes a cover letter that:
    - Shows genuine understanding of the role and company
    - Connects specific skills and experience to job requirements
    - Uses natural, professional language
    - Is concise (3-4 paragraphs max)
    """
    profile_ctx = _build_profile_context(profile)
    job_ctx = _build_job_context(job)
    
    name = profile.get("name", "Your Name")
    job_title = job.get("title", "the position")
    company = job.get("company", "the company")

    prompt = f"""You are an expert cover letter writer. Write a professional cover letter.

{profile_ctx}

---
{job_ctx}

---
INSTRUCTIONS:
1. Write a professional, concise cover letter (maximum 3-4 paragraphs)
2. Address it to the hiring team at {company}
3. Open with strong, specific interest in the {job_title} role — mention something from the job description
4. Connect the candidate's specific skills and experience to the job requirements
5. If the candidate's skills are described broadly, expand into specific relevant skills based on the job description
6. Close with a confident call to action
7. Use formal but natural, confident language
8. Sign with: Sincerely, {name}
9. Output ONLY the cover letter, no explanations or notes

Write the cover letter:"""

    result = _ai_chat_post([
        {"role": "system", "content": "You are an expert cover letter writer. You write concise, professional, tailored cover letters that show genuine understanding of the role. Output ONLY the letter, no explanations."},
        {"role": "user", "content": prompt}
    ], timeout=90, retries=2)

    if not result:
        result = _ai_chat_get(prompt[:2000], timeout=60)

    if not result:
        return None

    return result


def ai_enhance_profile(profile: dict) -> dict:
    """
    Use AI to enhance the profile — fill gaps, improve summary,
    and expand vague skill descriptions into specific skills.
    """
    name = profile.get("name", "")
    skills = profile.get("skills", [])
    summary = profile.get("summary", "")
    current_role = profile.get("current_role", "")
    years_exp = profile.get("years_experience", "")

    # If summary is empty or very short, generate one
    if not summary or len(summary) < 50:
        prompt = f"""Write a professional summary (2-3 sentences) for a CV:
Name: {name}
Role: {current_role}
Years of Experience: {years_exp}
Skills: {', '.join(skills)}
Make it professional, concise, and impactful. Output ONLY the summary, nothing else."""

        enhanced = _ai_chat_get(prompt, timeout=30)
        if enhanced and len(enhanced) > 20:
            profile["summary"] = enhanced.strip()

    return profile


def ai_generate_summary_text(skills: list, role: str, years: str) -> str:
    """Quick summary generation for profile setup page."""
    prompt = f"Write a 2-sentence professional CV summary for a {role} with {years} years experience in {', '.join(skills[:5])}. Output only the summary."
    return _ai_chat_get(prompt, timeout=30)


def test_ai():
    """Test the AI module."""
    print("[AI] Testing Pollinations AI...")
    result = _ai_chat_get("Say 'AI is working' in exactly those words.", timeout=15)
    if "AI is working" in result:
        print(f"[AI] GET endpoint works: {result}")
    else:
        print(f"[AI] GET endpoint failed: {result}")
    return "working" in result.lower()


if __name__ == "__main__":
    test_ai()
