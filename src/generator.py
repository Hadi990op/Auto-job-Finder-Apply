"""
CV & Cover Letter Generator — generates tailored documents for each job.
Produces clean, professional text documents customized per job posting.

The generator:
  1. Reorders skills by relevance to the job
  2. Tailors the professional summary to the role
  3. Highlights experience bullets most relevant to the job
  4. Writes a cover letter that connects specific experience to job requirements
"""

from datetime import datetime
from typing import Optional


def _job_text(job: dict) -> str:
    """Get combined job text for keyword matching."""
    parts = [
        str(job.get("title") or ""),
        str(job.get("description") or ""),
        " ".join(job.get("tags", []) if isinstance(job.get("tags"), list) else []),
    ]
    return " ".join(parts).lower()


def _relevance_sort(items: list[str], job: dict) -> list[str]:
    """Sort items by relevance to the job (most relevant first)."""
    text = _job_text(job)
    return sorted(items, key=lambda s: (s.lower() not in text, s))


def generate_cv(profile: dict, job: dict, evaluation=None) -> str:
    """Generate a tailored CV based on the user's profile and job posting."""
    name = profile.get("name", "Your Name")
    email = profile.get("email", "")
    phone = profile.get("phone", "")
    location = profile.get("location", "")
    linkedin = profile.get("linkedin", "")
    github = profile.get("github", "")
    portfolio = profile.get("portfolio", "")
    job_title = job.get("title", "the role")
    company = job.get("company", "your company")
    years_exp = profile.get("years_experience", "")

    lines = []

    # Header
    lines.append(name.upper())
    lines.append("=" * max(len(name), 10))
    contact_parts = [email, phone, location]
    if linkedin:
        contact_parts.append(f"LinkedIn: {linkedin}")
    if github:
        contact_parts.append(f"GitHub: {github}")
    if portfolio:
        contact_parts.append(f"Portfolio: {portfolio}")
    lines.append(" | ".join(filter(None, contact_parts)))
    lines.append("")

    # Professional Summary — tailored to the job
    summary = profile.get("summary", "")
    lines.append("PROFESSIONAL SUMMARY")
    lines.append("-" * 20)
    if summary:
        # If we have evaluation info, tailor the summary
        if evaluation and evaluation.matched_skills:
            lines.append(
                f"{summary} Seeking the {job_title} position at {company}, "
                f"where I can leverage my expertise in "
                f"{', '.join(evaluation.matched_skills[:3])}."
            )
        else:
            lines.append(summary)
    else:
        core_skills = profile.get("core_skills", profile.get("skills", [])[:5])
        skills_str = ", ".join(core_skills[:5]) if core_skills else "software development"
        current_role = profile.get("current_role", "Professional")
        if years_exp:
            lines.append(
                f"{current_role} with {years_exp} years of experience in {skills_str}. "
                f"Seeking to bring my expertise to the {job_title} position at {company}."
            )
        else:
            lines.append(
                f"Skilled professional specializing in {skills_str}. "
                f"Seeking to bring my expertise to the {job_title} position at {company}."
            )
    lines.append("")

    # Technical Skills — reordered by job relevance
    skills = profile.get("skills", [])
    if skills:
        lines.append("TECHNICAL SKILLS")
        lines.append("-" * 16)
        sorted_skills = _relevance_sort(skills, job)
        # Group: matched first, then others
        if evaluation and evaluation.matched_skills:
            matched = [s for s in sorted_skills if s in evaluation.matched_skills]
            others = [s for s in sorted_skills if s not in evaluation.matched_skills]
            if matched:
                lines.append(f"Core: {' • '.join(matched)}")
            if others:
                lines.append(f"Additional: {' • '.join(others)}")
        else:
            lines.append(" • ".join(sorted_skills))
        lines.append("")

    # Professional Experience — highlight relevant bullets
    experiences = profile.get("experience", [])
    if experiences and isinstance(experiences, list):
        lines.append("PROFESSIONAL EXPERIENCE")
        lines.append("-" * 23)
        job_text = _job_text(job)
        for exp in experiences:
            if not isinstance(exp, dict):
                continue
            lines.append(f"\n{exp.get('role', '')}")
            lines.append(f"{exp.get('company', '')} | {exp.get('period', '')}")
            if exp.get("location"):
                lines.append(exp["location"])
            bullets = exp.get("bullets", [])
            # Sort bullets by relevance to job
            if bullets:
                sorted_bullets = sorted(bullets, key=lambda b: sum(
                    kw.lower() in b.lower() for kw in job_text.split()
                ), reverse=True)
                for bullet in sorted_bullets:
                    lines.append(f"  • {bullet}")
        lines.append("")

    # Education
    education = profile.get("education", [])
    if education and isinstance(education, list):
        lines.append("EDUCATION")
        lines.append("-" * 9)
        for edu in education:
            if not isinstance(edu, dict):
                continue
            lines.append(
                f"{edu.get('degree', '')} — {edu.get('institution', '')} "
                f"({edu.get('year', '')})"
            )
            if edu.get("details"):
                lines.append(f"  {edu['details']}")
        lines.append("")

    # Projects
    projects = profile.get("projects", [])
    if projects and isinstance(projects, list):
        lines.append("PROJECTS")
        lines.append("-" * 9)
        for proj in projects:
            if not isinstance(proj, dict):
                continue
            lines.append(f"\n{proj.get('name', '')}")
            if proj.get("description"):
                lines.append(f"  {proj['description']}")
            if proj.get("link"):
                lines.append(f"  Link: {proj['link']}")
        lines.append("")

    # Certifications
    certs = profile.get("certifications", [])
    if certs and isinstance(certs, list):
        lines.append("CERTIFICATIONS")
        lines.append("-" * 14)
        for cert in certs:
            if not isinstance(cert, dict):
                continue
            lines.append(f"• {cert.get('name', '')} — {cert.get('issuer', '')} ({cert.get('year', '')})")
        lines.append("")

    # Languages
    languages = profile.get("languages", [])
    if languages:
        lines.append("LANGUAGES")
        lines.append("-" * 9)
        lines.append(" • ".join(languages))
        lines.append("")

    lines.append(f"\n--- CV tailored for {job_title} at {company} ---")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    return "\n".join(lines)


def generate_cover_letter(profile: dict, job: dict, evaluation=None) -> str:
    """Generate a tailored cover letter for the job application."""
    name = profile.get("name", "Your Name")
    email = profile.get("email", "")
    phone = profile.get("phone", "")
    location = profile.get("location", "")
    company = job.get("company", "your company")
    job_title = job.get("title", "the open position")
    job_location = job.get("location", "")
    job_source = job.get("source", "")

    lines = []

    # Header
    lines.append(name)
    if location:
        lines.append(location)
    lines.append(" | ".join(filter(None, [email, phone])))
    lines.append("")
    lines.append(datetime.now().strftime("%B %d, %Y"))
    lines.append("")

    # Recipient
    lines.append(f"Dear Hiring Team" + (f" at {company}," if company else ","))
    lines.append("")

    # Opening — express interest in the specific role
    location_phrase = ""
    if job_location and job_location.lower() not in ("remote", "worldwide", "anywhere"):
        location_phrase = f" in {job_location}"
    elif job_location and job_location.lower() in ("remote", "worldwide", "anywhere"):
        location_phrase = " (remote)"

    lines.append(
        f"I am writing to express my strong interest in the {job_title} position"
        f" at {company}{location_phrase}."
    )

    # Body paragraph 1 — who I am
    summary = profile.get("summary", "")
    years_exp = profile.get("years_experience", "")
    current_role = profile.get("current_role", "")

    if summary:
        lines.append("")
        lines.append(summary)
    else:
        core_skills = profile.get("core_skills", profile.get("skills", [])[:5])
        skills_str = ", ".join(core_skills[:5]) if core_skills else "my field"
        if years_exp:
            lines.append(
                f"\nWith {years_exp} years of experience as {current_role or 'a professional'} "
                f"specializing in {skills_str}, I have developed expertise that aligns well "
                f"with the requirements of this role."
            )
        else:
            lines.append(
                f"\nMy expertise in {skills_str} makes me a strong candidate for this position."
            )

    # Body paragraph 2 — why I'm a fit (use evaluation data)
    if evaluation and evaluation.matched_skills:
        lines.append("")
        lines.append("What makes me a strong fit for this role:")
        for s in evaluation.strengths[:4]:
            lines.append(f"  • {s}")

    # Body paragraph 3 — relevant experience highlight
    experiences = profile.get("experience", [])
    if experiences and isinstance(experiences, list) and len(experiences) > 0:
        top_exp = experiences[0]
        if isinstance(top_exp, dict):
            lines.append("")
            lines.append(
                f"In my role as {top_exp.get('role', '')} at "
                f"{top_exp.get('company', '')}, "
            )
            bullets = top_exp.get("bullets", [])
            if bullets:
                lines.append(f"I {bullets[0].lower().rstrip('.;')}.")
                if len(bullets) > 1:
                    lines.append(f"I also {bullets[1].lower().rstrip('.;')}.")

    # Body paragraph 4 — why this company
    lines.append("")
    if company:
        lines.append(
            f"I am particularly drawn to {company} and the opportunity to contribute "
            f"my skills to your team. I am confident that my background, technical "
            f"expertise, and enthusiasm would make me a valuable addition to your organization."
        )
    else:
        lines.append(
            "I am confident that my background and skills would make me a valuable "
            "addition to your team."
        )

    # Address gaps if any
    if evaluation and evaluation.gaps:
        lines.append("")
        for gap in evaluation.gaps[:2]:
            if "Missing skills" in gap:
                # Frame positively
                missing = gap.replace("Missing skills: ", "")
                lines.append(
                    f"While I am continuously expanding my skill set, I am eager to "
                    f"develop further in areas such as {missing}."
                )
                break

    # Closing
    lines.append("")
    lines.append(
        "Thank you for considering my application. I would welcome the opportunity to "
        "discuss how my experience can contribute to your team's success. "
        "I am available for an interview at your convenience."
    )
    lines.append("")
    lines.append("Sincerely,")
    lines.append(name)

    return "\n".join(lines)


def generate_job_match_report(job: dict, evaluation) -> str:
    """Generate a human-readable job evaluation report."""
    lines = []
    lines.append(f"JOB FIT EVALUATION: {job.get('title', 'N/A')} at {job.get('company', 'N/A')}")
    lines.append(f"Source: {job.get('source', 'N/A')} | Location: {job.get('location', 'N/A')}")
    lines.append("=" * 65)
    lines.append("")
    lines.append(f"{'Dimension':<25} {'Score':>8}")
    lines.append("-" * 35)
    lines.append(f"{'Technical Skills':<25} {evaluation.technical_score:>5}/100")
    lines.append(f"{'Experience Match':<25} {evaluation.experience_score:>5}/100")
    lines.append(f"{'Career Alignment':<25} {evaluation.career_score:>5}/100")
    lines.append(f"{'Location Fit':<25} {evaluation.location_score:>5}/100")
    lines.append("-" * 35)
    lines.append(f"{'OVERALL SCORE':<25} {evaluation.overall_score:>5}/100")
    lines.append(f"{'VERDICT':<25} {evaluation.verdict}")
    lines.append(f"{'AUTO-APPLY':<25} {'YES' if evaluation.should_auto_apply else 'NO'}")
    lines.append("")

    if evaluation.strengths:
        lines.append("STRENGTHS:")
        for s in evaluation.strengths:
            lines.append(f"  + {s}")
        lines.append("")

    if evaluation.gaps:
        lines.append("GAPS:")
        for g in evaluation.gaps:
            lines.append(f"  - {g}")
        lines.append("")

    lines.append(f"RECOMMENDATION: {evaluation.recommendation}")
    return "\n".join(lines)
