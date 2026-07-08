"""
Multi-Source Job Scraper — autonomous job discovery.
Scrapes jobs from multiple free sources:
  - LinkedIn (public jobs-guest API)
  - RemoteOK (free JSON API)
  - Remotive (free JSON API)
  - WeWorkRemotely (RSS feed)
  - Jobspresso (RSS feed)

All sources are free, require no authentication, and return structured data.
"""

import json
import re
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional

# --- Shared ---

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CATEGORIES = [
    "remote-back-end-programming-jobs",
    "remote-front-end-programming-jobs",
    "remote-full-stack-programming-jobs",
    "remote-devops-sysadmin-jobs",
    "remote-product-jobs",
    "remote-design-jobs",
    "remote-customer-support-jobs",
    "remote-sales-marketing-jobs",
    "remote-data-jobs",
]


def _fetch(url: str, timeout: int = 15, max_retries: int = 3) -> str:
    """Fetch URL content with retries."""
    delay = 0.5
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
            if (e.code == 429 or e.code >= 500) and attempt < max_retries - 1:
                time.sleep(delay + (attempt * 1))
                delay *= 2
                continue
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    return ""


def _parse_job_date(date_str: str) -> Optional[datetime]:
    """Parse a job date string from various sources into a datetime object."""
    if not date_str:
        return None
    date_str = date_str.strip()
    
    # Try ISO format (RemoteOK, Remotive): "2024-01-15T10:30:00.000Z" or "2024-01-15T10:30:00+00:00"
    try:
        # Handle Z suffix
        ds = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ds)
    except:
        pass
    
    # Try RFC 822 format (WWR, Jobspresso RSS): "Mon, 15 Jan 2024 10:30:00 +0000"
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]:
        try:
            return datetime.strptime(date_str, fmt)
        except:
            pass
    
    # Try relative dates from LinkedIn: "2 days ago", "1 week ago", "Just now"
    date_lower = date_str.lower()
    if "just now" in date_lower or "today" in date_lower:
        return datetime.now()
    if "yesterday" in date_lower:
        return datetime.now() - timedelta(days=1)
    
    import re
    m = re.search(r'(\d+)\s*(minute|hour|day|week|month)s?\s*ago', date_lower)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if unit == "minute":
            return datetime.now() - timedelta(minutes=num)
        elif unit == "hour":
            return datetime.now() - timedelta(hours=num)
        elif unit == "day":
            return datetime.now() - timedelta(days=num)
        elif unit == "week":
            return datetime.now() - timedelta(weeks=num)
        elif unit == "month":
            return datetime.now() - timedelta(days=num * 30)
    
    return None


def _is_job_fresh(date_str: str, max_age_days: int = 2) -> bool:
    """Check if a job is fresh enough (posted within max_age_days)."""
    job_date = _parse_job_date(date_str)
    if job_date is None:
        # If we can't parse the date, keep the job (better to check than skip)
        return True
    
    # Handle timezone-naive datetimes
    if job_date.tzinfo is None:
        now = datetime.now()
    else:
        from datetime import timezone
        now = datetime.now(timezone.utc)
    
    age = now - job_date
    return age.days <= max_age_days


def _strip_html(text: str) -> str:
    """Remove HTML tags and clean up whitespace."""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "\n• ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;", "'", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@dataclass
class JobListing:
    id: str
    source: str
    title: str
    company: str
    location: str
    url: str
    description: str
    apply_url: str
    date: str
    tags: list
    salary: str

    def to_dict(self) -> dict:
        return asdict(self)


# --- LinkedIn ---

def search_linkedin(query: str, location: str, jobage: int = 14, limit: int = 25) -> list[JobListing]:
    """Search LinkedIn public job board."""
    from linkedin_search import search_jobs
    try:
        result = search_jobs(
            location=location, query=query,
            remote="remote" if location.lower() == "remote" else "",
            jobage=jobage, limit=limit
        )
        jobs = []
        for j in result.get("results", []):
            jobs.append(JobListing(
                id=f"li_{j['id']}",
                source="linkedin",
                title=j.get("title", ""),
                company=j.get("company") or "",
                location=j.get("location") or "",
                url=j.get("url", ""),
                description="",  # LinkedIn cards don't have descriptions, need detail fetch
                apply_url=j.get("url", ""),
                date=j.get("date") or "",
                tags=[],
                salary=""
            ))
        return jobs
    except Exception as e:
        print(f"  [LinkedIn] Error: {e}")
        return []


# --- RemoteOK ---

def search_remoteok(limit: int = 50, query: str = "") -> list[JobListing]:
    """Fetch jobs from RemoteOK free API."""
    try:
        raw = _fetch("https://remoteok.com/api")
        data = json.loads(raw)
        if not isinstance(data, list):
            return []

        jobs = []
        for item in data[1:]:  # first item is metadata
            if not isinstance(item, dict) or "id" not in item:
                continue
            title = item.get("position", "")
            if query and query.lower() not in title.lower() and query.lower() not in " ".join(item.get("tags", [])).lower():
                continue
            jobs.append(JobListing(
                id=f"rok_{item['id']}",
                source="remoteok",
                title=title,
                company=item.get("company", ""),
                location=item.get("location") or "Remote",
                url=item.get("url", f"https://remoteok.com/remote-jobs/{item['id']}"),
                description=_strip_html(item.get("description", ""))[:5000],
                apply_url=item.get("apply_url", item.get("url", "")),
                date=item.get("date", ""),
                tags=item.get("tags", []),
                salary=f"${item.get('salary_min', '')}-${item.get('salary_max', '')}" if item.get("salary_min") else ""
            ))
            if len(jobs) >= limit:
                break
        return jobs
    except Exception as e:
        print(f"  [RemoteOK] Error: {e}")
        return []


# --- Remotive ---

def search_remotive(limit: int = 50, query: str = "", category: str = "") -> list[JobListing]:
    """Fetch jobs from Remotive free API."""
    try:
        url = "https://remotive.com/api/remote-jobs"
        if category:
            url += f"?category={category}"
        if query and not category:
            url += f"?search={urllib.parse.quote(query)}"
        elif query and category:
            url += f"&search={urllib.parse.quote(query)}"
        if limit:
            sep = "&" if "?" in url else "?"
            url += f"{sep}limit={limit}"

        raw = _fetch(url)
        data = json.loads(raw)
        raw_jobs = data.get("jobs", [])

        jobs = []
        for item in raw_jobs[:limit]:
            jobs.append(JobListing(
                id=f"rem_{item['id']}",
                source="remotive",
                title=item.get("title", ""),
                company=item.get("company_name", ""),
                location=item.get("candidate_required_location") or "Worldwide",
                url=item.get("url", ""),
                description=_strip_html(item.get("description", ""))[:5000],
                apply_url=item.get("url", ""),
                date=item.get("publication_date", ""),
                tags=item.get("tags", []),
                salary=item.get("salary") or ""
            ))
        return jobs
    except Exception as e:
        print(f"  [Remotive] Error: {e}")
        return []


# --- WeWorkRemotely (RSS) ---

def search_wwr(categories: list[str] = None, limit: int = 30) -> list[JobListing]:
    """Fetch jobs from WeWorkRemotely RSS feeds."""
    if categories is None:
        categories = CATEGORIES[:5]  # top 5 categories by default

    jobs = []
    for cat in categories:
        try:
            url = f"https://weworkremotely.com/categories/{cat}.rss"
            raw = _fetch(url)
            if not raw:
                continue

            root = ET.fromstring(raw)
            items = root.findall(".//item")

            for item in items:
                title_elem = item.find("title")
                link_elem = item.find("link")
                desc_elem = item.find("description")
                date_elem = item.find("pubDate")
                region_elem = item.find("region")

                title = title_elem.text if title_elem is not None else ""
                link = link_elem.text if link_elem is not None else ""
                desc = _strip_html(desc_elem.text if desc_elem is not None else "")
                pub_date = date_elem.text if date_elem is not None else ""
                region = region_elem.text if region_elem is not None else "Anywhere"

                # Parse title: "Company: Role" format
                parts = title.split(":", 1)
                company = parts[0].strip() if len(parts) > 1 else ""
                job_title = parts[1].strip() if len(parts) > 1 else title

                # Generate ID from link
                job_id = re.sub(r"[^a-zA-Z0-9]", "_", link)[-60:]

                jobs.append(JobListing(
                    id=f"wwr_{job_id}",
                    source="weworkremotely",
                    title=job_title,
                    company=company,
                    location=region,
                    url=link,
                    description=desc[:5000],
                    apply_url=link,
                    date=pub_date,
                    tags=[cat.replace("remote-", "").replace("-jobs", "")],
                    salary=""
                ))
            time.sleep(0.3)  # be nice to server
        except Exception as e:
            print(f"  [WWR:{cat}] Error: {e}")
            continue

    return jobs[:limit]


# --- Jobspresso (RSS) ---

def search_jobspresso(limit: int = 20) -> list[JobListing]:
    """Fetch jobs from Jobspresso RSS feed."""
    try:
        raw = _fetch("https://jobspresso.co/feed/")
        if not raw:
            return []

        root = ET.fromstring(raw)
        items = root.findall(".//item")
        jobs = []

        for item in items[:limit]:
            title_elem = item.find("title")
            link_elem = item.find("link")
            desc_elem = item.find("description")
            date_elem = item.find("pubDate")

            title = title_elem.text if title_elem is not None else ""
            link = link_elem.text if link_elem is not None else ""
            desc = _strip_html(desc_elem.text if desc_elem is not None else "")
            pub_date = date_elem.text if date_elem is not None else ""

            job_id = re.sub(r"[^a-zA-Z0-9]", "_", link)[-60:]

            jobs.append(JobListing(
                id=f"jsp_{job_id}",
                source="jobspresso",
                title=title,
                company="",  # Jobspresso doesn't separate company in RSS
                location="Remote",
                url=link,
                description=desc[:5000],
                apply_url=link,
                date=pub_date,
                tags=[],
                salary=""
            ))
        return jobs
    except Exception as e:
        print(f"  [Jobspresso] Error: {e}")
        return []


# --- LinkedIn detail fetch ---

def fetch_linkedin_detail(job_id: str) -> str:
    """Fetch full job description from LinkedIn."""
    from linkedin_search import get_job_detail
    try:
        # Strip the "li_" prefix
        clean_id = job_id.replace("li_", "")
        detail = get_job_detail(clean_id)
        return detail.get("description") or ""
    except Exception:
        return ""


# --- Master scrape function ---

def scrape_all_jobs(
    profile: dict,
    max_per_source: int = 50,
    linkedin_queries: list = None,
    linkedin_locations: list = None,
    max_age_days: int = 2,
) -> list[JobListing]:
    """
    Scrape jobs from all sources based on the user's profile.
    Uses profile skills/job_titles to build search queries.
    Only returns jobs posted within max_age_days (default: 2 days).
    """
    all_jobs = []
    skills = profile.get("core_skills", profile.get("skills", []))
    job_titles = profile.get("job_titles", [])
    preferred_locations = profile.get("preferred_locations", ["Remote"])

    # Build search queries from profile
    if linkedin_queries is None:
        linkedin_queries = []
        if job_titles:
            linkedin_queries.extend(job_titles[:3])
        if skills:
            for s in skills[:3]:
                if s not in linkedin_queries:
                    linkedin_queries.append(s)
        
        # If queries are too vague (generic terms that won't find good jobs),
        # use better default search terms based on the candidate's role
        vague_terms = {"full development skills", "everything includes", "every related", 
                       "all skills", "developer", "software", "programming", "coding"}
        linkedin_queries = [q for q in linkedin_queries if q.lower().strip() not in vague_terms]
        
        if not linkedin_queries:
            # Use the current role as a search term, or default to common dev roles
            current_role = profile.get("current_role", "")
            if current_role and current_role.lower() not in vague_terms:
                linkedin_queries = [current_role, "Software Engineer", "Developer"]
            else:
                linkedin_queries = ["Software Engineer", "Full Stack Developer", "Web Developer"]
            print(f"  [LinkedIn] Profile skills are too vague, using default queries: {linkedin_queries}")

    # Clean up location — remove "and" prefix (e.g., "and gov pk" is not a valid location)
    if linkedin_locations is None:
        clean_locations = []
        for loc in (preferred_locations if preferred_locations else ["Remote"]):
            loc = loc.strip()
            if loc.lower().startswith("and "):
                loc = loc[4:].strip()
            if loc and len(loc) > 2:
                clean_locations.append(loc)
        linkedin_locations = clean_locations if clean_locations else ["Remote"]

    # 1. LinkedIn
    print("  [LinkedIn] Searching...")
    for query in linkedin_queries[:3]:
        for loc in linkedin_locations[:2]:
            print(f"    -> query='{query}', location='{loc}'")
            jobs = search_linkedin(query=query, location=loc, jobage=max_age_days, limit=max_per_source)
            print(f"    -> Found {len(jobs)} jobs")
            all_jobs.extend(jobs)
            time.sleep(1)  # be nice to LinkedIn

    # 2. RemoteOK
    print("  [RemoteOK] Fetching...")
    # Use job titles or current role for search, not vague skill descriptions
    search_query = " ".join(skills[:3]) if skills else ""
    # If skills are vague, use job titles instead
    vague_terms = {"full development skills", "everything includes", "every related", "all skills"}
    if not search_query or any(s.lower().strip() in vague_terms for s in skills[:3]):
        search_query = " ".join(job_titles[:3]) if job_titles else ""
    rok_jobs = search_remoteok(limit=max_per_source, query=search_query)
    print(f"    -> Found {len(rok_jobs)} jobs")
    all_jobs.extend(rok_jobs)

    # 3. Remotive
    print("  [Remotive] Fetching...")
    rem_jobs = search_remotive(limit=max_per_source, query=search_query)
    print(f"    -> Found {len(rem_jobs)} jobs")
    all_jobs.extend(rem_jobs)

    # 4. WeWorkRemotely
    print("  [WWR] Fetching RSS...")
    wwr_jobs = search_wwr(limit=max_per_source)
    print(f"    -> Found {len(wwr_jobs)} jobs")
    all_jobs.extend(wwr_jobs)

    # 5. Jobspresso
    print("  [Jobspresso] Fetching RSS...")
    jsp_jobs = search_jobspresso(limit=20)
    print(f"    -> Found {len(jsp_jobs)} jobs")
    all_jobs.extend(jsp_jobs)

    # Filter by date — only keep jobs posted within max_age_days
    if max_age_days > 0:
        fresh_jobs = [j for j in all_jobs if _is_job_fresh(j.date, max_age_days)]
        old_count = len(all_jobs) - len(fresh_jobs)
        print(f"  [Date Filter] Keeping {len(fresh_jobs)} fresh jobs (posted within {max_age_days} days), filtered out {old_count} old jobs")
        all_jobs = fresh_jobs

    # Deduplicate by ID
    seen_ids = set()
    unique_jobs = []
    for job in all_jobs:
        if job.id not in seen_ids:
            seen_ids.add(job.id)
            unique_jobs.append(job)

    print(f"  [TOTAL] {len(unique_jobs)} unique jobs from all sources")
    return unique_jobs
