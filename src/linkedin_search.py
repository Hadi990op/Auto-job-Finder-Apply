"""
LinkedIn Job Search API — ported from the repo's TypeScript CLI to Python.
Uses LinkedIn's public jobs-guest endpoints. No authentication required.
"""

import re
import json
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass, asdict
from typing import Optional

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class JobCard:
    id: str
    title: str
    company: Optional[str] = None
    company_url: Optional[str] = None
    location: Optional[str] = None
    date: Optional[str] = None
    url: str = ""


@dataclass
class JobDetail:
    id: str
    title: str
    company: Optional[str] = None
    company_url: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    seniority: Optional[str] = None
    employment_type: Optional[str] = None
    job_function: Optional[str] = None
    industries: Optional[str] = None
    apply_url: Optional[str] = None
    url: str = ""


def _html_fetch(url: str, max_retries: int = 6) -> str:
    """Fetch HTML with exponential backoff on 429/5xx. Returns '' on 404."""
    delay = 0.5
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
            if e.code == 429 or e.code >= 500:
                if attempt == max_retries:
                    raise Exception(f"Request failed: {e.code}")
                import random
                time.sleep(delay + random.uniform(0, 0.5))
                delay = min(delay * 2, 8)
                continue
            raise
        except Exception as e:
            if attempt == max_retries:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 8)
    raise Exception("Request failed after max retries")


def _decode_entities(text: str) -> str:
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&apos;", "'")
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))) if 0 <= int(m.group(1)) <= 0x10FFFF else "", text)
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)) if 0 <= int(m.group(1), 16) <= 0x10FFFF else "", text)
    return text


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def _clean(html: str) -> str:
    return _decode_entities(_strip_tags(html)).strip()


def _jobage_to_tpr(days: int) -> Optional[str]:
    if not days or days <= 0 or days >= 9999:
        return None
    return f"r{days * 86400}"


def _work_type_flag(mode: Optional[str]) -> Optional[str]:
    if not mode:
        return None
    m = mode.lower()
    if m == "remote":
        return "2"
    if m == "hybrid":
        return "3"
    if m in ("onsite", "on-site"):
        return "1"
    return None


def _parse_job_cards(html: str) -> list[JobCard]:
    results = []
    chunks = html.split('data-entity-urn="urn:li:jobPosting:')[1:]

    for chunk in chunks:
        id_match = re.match(r"^(\d+)", chunk)
        if not id_match:
            continue
        job_id = id_match.group(1)

        link_match = re.search(r'class="base-card__full-link[^"]*"[^>]*href="([^"]+)"', chunk, re.I)
        url = _decode_entities(link_match.group(1)).split("?")[0] if link_match else ""

        title = None
        h3 = re.search(r'class="base-search-card__title"[^>]*>([\s\S]*?)</h3>', chunk, re.I)
        if h3:
            title = _clean(h3.group(1))
        if not title:
            sr = re.search(r'class="sr-only"[^>]*>([\s\S]*?)</span>', chunk, re.I)
            if sr:
                title = _clean(sr.group(1))
        if not title:
            continue

        company = None
        company_url = None
        sub = re.search(r'class="base-search-card__subtitle"[^>]*>([\s\S]*?)</h4>', chunk, re.I)
        if sub:
            a = re.search(r'href="([^"]+)"', sub.group(1), re.I)
            if a:
                company_url = _decode_entities(a.group(1)).split("?")[0]
            company = _clean(sub.group(1)) or None

        loc = re.search(r'class="job-search-card__location"[^>]*>([\s\S]*?)</span>', chunk, re.I)
        location = _clean(loc.group(1)) if loc else None

        dt = re.search(r'class="job-search-card__listdate[^"]*"[^>]*datetime="([^"]+)"', chunk, re.I)
        date = dt.group(1) if dt else None

        results.append(JobCard(
            id=job_id, title=title, company=company,
            company_url=company_url, location=location,
            date=date, url=url or f"https://www.linkedin.com/jobs/view/{job_id}"
        ))

    return results


def _parse_job_detail(html: str, job_id: str) -> JobDetail:
    title = re.search(r'class="(?:top-card-layout__title|topcard__title)[^"]*"[^>]*>([\s\S]*?)</h[12]>', html, re.I)
    org = re.search(r'class="topcard__org-name-link[^"]*"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', html, re.I)
    loc = re.search(r'class="topcard__flavor topcard__flavor--bullet"[^>]*>([\s\S]*?)</span>', html, re.I)

    desc = re.search(r'class="(?:show-more-less-html__markup|description__text[^"]*)"[^>]*>([\s\S]*?)</div>', html, re.I)
    description = None
    if desc:
        d = desc.group(1)
        d = re.sub(r"<\s*br\s*/?>", "\n", d, flags=re.I)
        d = re.sub(r"</(p|li|ul|ol|div|h\d)>", "\n", d, flags=re.I)
        description = _decode_entities(_strip_tags(d)).strip() or None

    criteria = {}
    for m in re.finditer(
        r'class="description__job-criteria-subheader"[^>]*>([\s\S]*?)</h3>[\s\S]*?class="description__job-criteria-text[^"]*"[^>]*>([\s\S]*?)</span>',
        html, re.I
    ):
        criteria[_clean(m.group(1)).lower()] = _clean(m.group(2))

    apply = re.search(r'class="topcard__link[^"]*"[^>]*href="([^"]+)"', html, re.I)

    return JobDetail(
        id=job_id,
        title=_clean(title.group(1)) if title else "(untitled)",
        company=_clean(org.group(2)) if org else None,
        company_url=_decode_entities(org.group(1)).split("?")[0] if org else None,
        location=_clean(loc.group(1)) if loc else None,
        description=description,
        seniority=criteria.get("seniority level"),
        employment_type=criteria.get("employment type"),
        job_function=criteria.get("job function"),
        industries=criteria.get("industries"),
        apply_url=_decode_entities(apply.group(1)).split("?")[0] if apply else None,
        url=f"https://www.linkedin.com/jobs/view/{job_id}",
    )


def search_jobs(
    location: str,
    query: str = "",
    jobage: int = 0,
    remote: str = "",
    page: int = 1,
    limit: int = 0,
) -> dict:
    """Search LinkedIn jobs. Returns dict with 'meta' and 'results'."""
    params = {}
    if query:
        params["keywords"] = query
    if location:
        params["location"] = location
    tpr = _jobage_to_tpr(jobage)
    if tpr:
        params["f_TPR"] = tpr
    wt = _work_type_flag(remote)
    if wt:
        params["f_WT"] = wt
    params["start"] = str((page - 1) * 10)

    url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
    html = _html_fetch(url)
    cards = _parse_job_cards(html)
    if limit and limit > 0:
        cards = cards[:limit]

    return {
        "meta": {"count": len(cards), "page": page},
        "results": [asdict(c) for c in cards],
    }


def get_job_detail(job_id_or_url: str) -> dict:
    """Get full details for a job by ID or URL."""
    # Extract ID from URL if needed
    m = re.search(r"-(\d{6,})(?:\?|$)", job_id_or_url) or re.search(r"(\d{6,})", job_id_or_url)
    job_id = m.group(1) if m else job_id_or_url

    url = f"{DETAIL_URL}/{job_id}"
    html = _html_fetch(url)
    if not html:
        return {"error": "Job not found", "id": job_id}

    detail = _parse_job_detail(html, job_id)
    return asdict(detail)
