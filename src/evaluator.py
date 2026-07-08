"""
Job Evaluation Engine — scores jobs against the candidate's profile.
Decides whether a job is worth auto-applying to.

Scoring:
  - Technical Skills Match (30%): keyword overlap between job and profile skills
  - Experience Match (25%): job title and domain match
  - Career Alignment (30%): career goals and motivation
  - Location/Logistics (15%): remote/location preferences
  - Salary fit (bonus): if salary info available and matches expectations

Threshold for auto-apply: >= 50 (configurable in profile)
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EvaluationResult:
    technical_score: int = 0
    experience_score: int = 0
    career_score: int = 0
    location_score: int = 0
    overall_score: float = 0
    verdict: str = ""
    strengths: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    recommendation: str = ""
    should_auto_apply: bool = False
    matched_skills: list = field(default_factory=list)
    missing_skills: list = field(default_factory=list)


def _keyword_overlap(text: str, keywords: list[str]) -> tuple[list[str], list[str]]:
    """Check which keywords appear in text. Returns (matched, missing)."""
    text_lower = text.lower()
    matched = []
    missing = []
    for kw in keywords:
        kw_lower = kw.lower()
        # Check for exact match or word boundary
        if kw_lower in text_lower:
            matched.append(kw)
        else:
            missing.append(kw)
    return matched, missing


def evaluate_job(job: dict, profile: dict) -> EvaluationResult:
    """Evaluate a job against the candidate's profile."""

    # Combine all job text for keyword matching
    job_text = " ".join(filter(None, [
        str(job.get("title", "")),
        str(job.get("description") or ""),
        str(job.get("company") or ""),
        str(job.get("location") or ""),
        " ".join(job.get("tags", []) if isinstance(job.get("tags"), list) else []),
        str(job.get("job_function") or ""),
    ]))

    result = EvaluationResult()

    # 1. Technical Skills Match (0-100, weight 30%)
    all_skills = profile.get("skills", [])
    core_skills = profile.get("core_skills", all_skills[:5])

    if all_skills:
        matched_all, missing_all = _keyword_overlap(job_text, all_skills)
        result.matched_skills = matched_all
        result.missing_skills = missing_all

        if core_skills:
            matched_core, _ = _keyword_overlap(job_text, core_skills)
            # Score: 60% for core skills match + 40% for overall skills match
            core_ratio = len(matched_core) / len(core_skills) if core_skills else 0
            all_ratio = len(matched_all) / len(all_skills) if all_skills else 0
            result.technical_score = int(min(100, (core_ratio * 60) + (all_ratio * 40)))
        else:
            result.technical_score = int((len(matched_all) / len(all_skills)) * 100) if all_skills else 50
    else:
        result.technical_score = 50

    # 2. Experience Match (0-100, weight 25%)
    job_titles = profile.get("job_titles", [])
    exp_domains = profile.get("experience_domains", [])
    exp_score = 50  # default

    if job_titles:
        title_lower = (job.get("title") or "").lower()
        title_matches = sum(1 for t in job_titles if t.lower() in title_lower)
        if title_matches:
            exp_score = min(100, 60 + title_matches * 15)

    if exp_domains:
        matched_domains, _ = _keyword_overlap(job_text, exp_domains)
        domain_ratio = len(matched_domains) / len(exp_domains)
        exp_score = max(exp_score, int(domain_ratio * 100))

    # Check experience bullet points if available
    experiences = profile.get("experience", [])
    if experiences and isinstance(experiences, list):
        exp_text = ""
        for exp in experiences:
            if isinstance(exp, dict):
                exp_text += " ".join(exp.get("bullets", [])) + " "
        if exp_text:
            matched_exp, _ = _keyword_overlap(job_text, exp_text.split())
            # Boost score if experience keywords match
            if matched_exp:
                exp_score = min(100, exp_score + 10)

    result.experience_score = exp_score

    # 3. Career Alignment (0-100, weight 30%)
    career_goals = profile.get("career_goals", [])
    if career_goals:
        matched_goals, _ = _keyword_overlap(job_text, career_goals)
        result.career_score = int((len(matched_goals) / len(career_goals)) * 100) if career_goals else 50
    else:
        result.career_score = 50

    # 4. Location/Logistics (0-100, weight 15%)
    job_location = (job.get("location") or "").lower()
    preferred = profile.get("preferred_locations", [])

    if not preferred:
        result.location_score = 75  # neutral
    elif any("remote" in p.lower() for p in preferred) and "remote" in job_location:
        result.location_score = 100
    elif any("world" in p.lower() or "anywhere" in p.lower() for p in preferred) and ("world" in job_location or "anywhere" in job_location):
        result.location_score = 100
    else:
        matched_locs, _ = _keyword_overlap(job_location, [p.lower() for p in preferred])
        if matched_locs:
            result.location_score = 90
        elif "remote" in job_location:
            result.location_score = 80  # remote is usually acceptable
        else:
            result.location_score = 30  # location mismatch

    # 5. Calculate overall score
    result.overall_score = round(
        result.technical_score * 0.30 +
        result.experience_score * 0.25 +
        result.career_score * 0.30 +
        result.location_score * 0.15,
        1
    )

    # 6. Verdict
    if result.overall_score >= 75:
        result.verdict = "Strong Fit"
    elif result.overall_score >= 60:
        result.verdict = "Good Fit"
    elif result.overall_score >= 45:
        result.verdict = "Moderate Fit"
    elif result.overall_score >= 30:
        result.verdict = "Weak Fit"
    else:
        result.verdict = "Poor Fit"

    # 7. Strengths and gaps
    if result.matched_skills:
        result.strengths.append(f"Skills match: {', '.join(result.matched_skills[:5])}")
    if job_titles:
        title_lower = (job.get("title") or "").lower()
        if any(t.lower() in title_lower for t in job_titles):
            result.strengths.append("Job title aligns with target roles")
    if result.location_score >= 80:
        result.strengths.append("Location matches preferences")

    if result.missing_skills:
        result.gaps.append(f"Missing skills: {', '.join(result.missing_skills[:5])}")
    if result.location_score < 50:
        result.gaps.append("Location doesn't match preferences")

    # 8. Recommendation
    if result.overall_score >= 60:
        result.recommendation = "Apply"
    elif result.overall_score >= 45:
        result.recommendation = "Consider"
    elif result.overall_score >= 30:
        result.recommendation = "Skip"
    else:
        result.recommendation = "Skip"

    # 9. Auto-apply decision
    auto_apply_threshold = profile.get("auto_apply_threshold", 50)
    result.should_auto_apply = result.overall_score >= auto_apply_threshold

    return result
