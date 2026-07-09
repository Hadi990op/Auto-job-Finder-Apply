"""
Multi-Source Job Scraper — autonomous job discovery.
Scrapes jobs from multiple free sources:
  - LinkedIn (public jobs-guest API)
  - RemoteOK (free JSON API)
  - Remotive (free JSON API)
  - WeWorkRemotely (RSS feed)
  - Jobspresso (RSS feed)
  - Freelancer.com (RSS feed)
  - Jobicy (free JSON API)
  - Python.org Jobs (RSS feed)
  - Mustakbil.com (Pakistani jobs, Playwright scraping)
  - TechJobs.pk (Pakistani tech jobs, Playwright scraping)
  - JSRemotely / javascript.jobs (remote JS jobs, Playwright)
  - micro1.ai/refer (AI talent platform, requires account — auto-apply mode)

All sources are free, require no authentication, and return structured data.
Sources requiring account creation are handled by the auto-apply engine.
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
            jobage=jobage, limit=limit,
            easy_apply_only=True,  # Only get Easy Apply jobs
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


# --- Freelancer.com (RSS) ---

def search_freelancer(keywords: list = None, limit: int = 50) -> list[JobListing]:
    """Fetch projects from Freelancer.com RSS feeds by keyword."""
    if keywords is None:
        keywords = ["python", "javascript", "react", "node.js", "web development"]

    jobs = []
    seen_ids = set()

    for kw in keywords:
        try:
            url = f"https://www.freelancer.com/rss.xml?keyword={urllib.parse.quote(kw)}"
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
                guid_elem = item.find("guid")

                title = title_elem.text if title_elem is not None else ""
                link = link_elem.text if link_elem is not None else ""
                raw_desc = desc_elem.text if desc_elem is not None else ""
                desc = _strip_html(raw_desc)
                pub_date = date_elem.text if date_elem is not None else ""
                guid = guid_elem.text if guid_elem is not None else ""

                # Build ID from guid, e.g. "Freelancer_project_40568038" -> "flc_40568038"
                num_match = re.search(r"(\d+)", guid)
                if num_match:
                    job_id = f"flc_{num_match.group(1)}"
                else:
                    job_id = f"flc_{re.sub(r'[^a-zA-Z0-9]', '_', guid)[-40:]}"

                # Deduplicate across keywords
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                # Parse budget and skills from the description text
                # Expected format: "... (Budget: $X - $Y USD, Jobs: Skill1, Skill2, ...)"
                budget = ""
                tags = []
                m = re.search(r"\(Budget: (.+?), Jobs: (.+?)\)", raw_desc or "")
                if m:
                    budget = m.group(1).strip()
                    jobs_str = m.group(2).strip()
                    tags = [t.strip() for t in jobs_str.split(",") if t.strip()]

                # Supplement tags from <category> elements
                for cat_elem in item.findall("category"):
                    if cat_elem.text:
                        cat = cat_elem.text.strip()
                        if cat and cat not in tags:
                            tags.append(cat)

                jobs.append(JobListing(
                    id=job_id,
                    source="freelancer",
                    title=title,
                    company="Freelancer.com Client",
                    location="Remote",
                    url=link,
                    description=desc[:5000],
                    apply_url=link,
                    date=pub_date,
                    tags=tags,
                    salary=budget
                ))

                if len(jobs) >= limit:
                    return jobs
            time.sleep(0.3)  # be nice to server
        except Exception as e:
            print(f"  [Freelancer:{kw}] Error: {e}")
            continue

    return jobs[:limit]


# --- Jobicy (free JSON API) ---

def search_jobicy(limit: int = 50, query: str = "") -> list[JobListing]:
    """Fetch remote jobs from Jobicy free API."""
    try:
        url = "https://jobicy.com/api/v2/remote-jobs"
        raw = _fetch(url)
        if not raw:
            return []
        data = json.loads(raw)
        raw_jobs = data.get("jobs", [])
        if not isinstance(raw_jobs, list):
            return []

        jobs = []
        for item in raw_jobs[:limit]:
            title = item.get("jobTitle", "")
            if query and query.lower() not in title.lower() and query.lower() not in (item.get("jobExcerpt", "") or "").lower():
                continue
            jobs.append(JobListing(
                id=f"jobicy_{item.get('id', '')}",
                source="jobicy",
                title=title,
                company=item.get("companyName", ""),
                location=item.get("jobGeo", "") or "Remote",
                url=item.get("url", ""),
                description=_strip_html(item.get("jobDescription", "") or item.get("jobExcerpt", ""))[:5000],
                apply_url=item.get("url", ""),
                date=item.get("pubDate", ""),
                tags=[item.get("jobIndustry", ""), item.get("jobType", "")] if item.get("jobIndustry") else [],
                salary=""
            ))
        return jobs
    except Exception as e:
        print(f"  [Jobicy] Error: {e}")
        return []


# --- Python.org Jobs (RSS feed) ---

def search_python_jobs(limit: int = 20) -> list[JobListing]:
    """Fetch Python-related jobs from python.org RSS feed."""
    try:
        raw = _fetch("https://www.python.org/jobs/feed/rss/")
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
            author_elem = item.find("author")

            title = title_elem.text if title_elem is not None else ""
            link = link_elem.text if link_elem is not None else ""
            desc = _strip_html(desc_elem.text if desc_elem is not None else "")
            pub_date = date_elem.text if date_elem is not None else ""
            company = author_elem.text if author_elem is not None else ""

            job_id = re.sub(r"[^a-zA-Z0-9]", "_", link)[-60:]

            jobs.append(JobListing(
                id=f"pyjobs_{job_id}",
                source="python_jobs",
                title=title,
                company=company,
                location="Various",
                url=link,
                description=desc[:5000],
                apply_url=link,
                date=pub_date,
                tags=["Python"],
                salary=""
            ))
        return jobs
    except Exception as e:
        print(f"  [PythonJobs] Error: {e}")
        return []


# --- Mustakbil.com (Pakistani jobs, Playwright) ---

def search_mustakbil(limit: int = 30, query: str = "") -> list[JobListing]:
    """Scrape Pakistani jobs from Mustakbil.com using Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [Mustakbil] Playwright not available")
        return []

    jobs = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
            page = browser.new_page()

            url = "https://www.mustakbil.com/jobs"
            if query:
                url += f"?search={urllib.parse.quote(query)}"

            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # Find job cards
            cards = page.query_selector_all(".jobs-grid .job-card, .job-card")

            for card in cards[:limit]:
                try:
                    # Title link
                    title_elem = card.query_selector(".job-card__title")
                    if not title_elem:
                        continue

                    href = title_elem.get_attribute("href") or ""
                    if not href.startswith("http"):
                        href = "https://www.mustakbil.com" + href

                    title = title_elem.inner_text().strip()

                    # Company — get the span inside, not the icon text
                    company = ""
                    company_elem = card.query_selector(".job-card__company span")
                    if company_elem:
                        company = company_elem.inner_text().strip()

                    # Location — get the span inside
                    location = "Pakistan"
                    location_elem = card.query_selector(".job-card__location span")
                    if location_elem:
                        location = location_elem.inner_text().strip()

                    # Date — from footer
                    date_text = ""
                    footer = card.query_selector(".job-card__footer-meta")
                    if footer:
                        date_text = footer.inner_text().strip()

                    # Job type
                    job_type = ""
                    type_elem = card.query_selector(".job-card__footer-meta .badge, .job-card__type")
                    if type_elem:
                        job_type = type_elem.inner_text().strip()

                    job_id_match = re.search(r"/jobs/job/(\d+)", href)
                    job_id = f"mtk_{job_id_match.group(1)}" if job_id_match else f"mtk_{re.sub(r'[^a-zA-Z0-9]', '_', href)[-40:]}"

                    jobs.append(JobListing(
                        id=job_id,
                        source="mustakbil",
                        title=title,
                        company=company,
                        location=location,
                        url=href,
                        description=f"Job type: {job_type}. Posted: {date_text}",
                        apply_url=href,
                        date=date_text,
                        tags=[job_type] if job_type else [],
                        salary=""
                    ))
                except Exception:
                    continue

            browser.close()
    except Exception as e:
        print(f"  [Mustakbil] Error: {e}")

    return jobs[:limit]


# --- TechJobs.pk (Pakistani tech jobs, Playwright) ---

def search_techjobs_pk(limit: int = 30, query: str = "") -> list[JobListing]:
    """Scrape Pakistani tech jobs from TechJobs.pk using Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [TechJobs.pk] Playwright not available")
        return []

    jobs = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
            page = browser.new_page()

            url = "https://techjobs.pk/jobs"
            if query:
                url += f"?search={urllib.parse.quote(query)}"

            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            # Scroll to load lazy content
            for _ in range(3):
                page.evaluate("window.scrollBy(0, 800)")
                page.wait_for_timeout(1000)

            # TechJobs.pk renders job cards as divs with border classes
            # Each card contains: title, company, location, type, salary, tags
            cards = page.query_selector_all(".overflow-hidden.rounded-lg.border")

            for card in cards[:limit]:
                try:
                    text = card.inner_text()
                    if len(text) < 20:
                        continue

                    lines = [l.strip() for l in text.split("\n") if l.strip()]

                    # Skip non-job cards
                    if not lines or any(lines[0] == nav for nav in ["Browse Jobs", "Companies", "Sign In"]):
                        continue

                    # First line is usually the title (may have company initials prefix)
                    title = lines[0]
                    # Remove common prefixes like "ET" (company initials)
                    if len(title) < 5 and len(lines) > 1:
                        title = lines[1]

                    company = ""
                    location = "Pakistan"
                    job_type = ""
                    salary = ""
                    posted = ""

                    for line in lines:
                        # Company · Location pattern
                        if "·" in line and not company:
                            parts = line.split("·")
                            company = parts[0].strip()
                            if len(parts) > 1:
                                location = parts[1].strip()
                        # Job type
                        if any(t in line.lower() for t in ["full-time", "part-time", "contract", "freelance", "internship"]):
                            job_type = line
                        # Location
                        if any(c in line.lower() for c in ["lahore", "karachi", "islamabad", "remote", "hybrid", "onsite"]):
                            if "·" not in line:
                                location = line
                        # Salary
                        if "pkr" in line.lower() or "$" in line:
                            salary = line
                        # Posted date
                        if "posted" in line.lower():
                            posted = line

                    if not title or len(title) < 3:
                        continue

                    # Generate ID from title
                    job_id = f"tjp_{re.sub(r'[^a-zA-Z0-9]', '_', title)[:40]}_{len(jobs)}"

                    jobs.append(JobListing(
                        id=job_id,
                        source="techjobs_pk",
                        title=title,
                        company=company,
                        location=location,
                        url=f"https://techjobs.pk/jobs?search={urllib.parse.quote(title[:30])}",
                        description=f"Job type: {job_type}. Salary: {salary}. Location: {location}. {posted}",
                        apply_url=f"https://techjobs.pk/jobs",
                        date=posted,
                        tags=[job_type] if job_type else [],
                        salary=salary
                    ))
                except Exception:
                    continue

            browser.close()
    except Exception as e:
        print(f"  [TechJobs.pk] Error: {e}")

    return jobs[:limit]


# --- JSRemotely / javascript.jobs (Playwright) ---

def search_jsremotely(limit: int = 30) -> list[JobListing]:
    """Scrape remote JavaScript jobs from JSRemotely using Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [JSRemotely] Playwright not available")
        return []

    jobs = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
            page = browser.new_page()

            page.goto("https://jsremotely.com", timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # JSRemotely has job links with /job/ in href
            # The link text format is: "Title\n4D\nFull Time\nRemote\nCompany\nCountry"
            job_links = page.query_selector_all("a[href*='/job/']")

            seen_hrefs = set()
            for link in job_links[:limit * 2]:  # get extra to account for dups
                try:
                    href = link.get_attribute("href") or ""
                    if not href.startswith("http"):
                        href = "https://jsremotely.com" + href

                    if href in seen_hrefs:
                        continue
                    seen_hrefs.add(href)

                    # Parse the link text: "Title\n4D\nFull Time\nRemote\nCompany\nCountry"
                    raw_text = link.inner_text().strip()
                    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]

                    if not lines:
                        continue

                    title = lines[0]
                    if len(title) < 3:
                        continue

                    # Parse metadata from remaining lines
                    company = ""
                    location = "Remote"
                    job_type = ""
                    salary = ""
                    date_text = ""

                    for line in lines[1:]:
                        if re.match(r'^\d+[DMWYh]', line):
                            date_text = line
                        elif any(t in line.lower() for t in ["full time", "part time", "contract", "freelance"]):
                            job_type = line
                        elif line.lower() in ["remote", "worldwide"]:
                            location = "Remote"
                        elif any(c in line.lower() for c in ["pakistan", "india", "usa", "europe", "brazil", "canada", "germany", "uk"]):
                            location = line
                        elif "$" in line and "salary" in line.lower():
                            salary = line
                        elif not company and not re.match(r'^\d+[DMWYh]', line) and \
                             not any(t in line.lower() for t in ["remote", "time", "salary", "full", "part", "contract", "freelance"]):
                            company = line

                    job_id = f"jsr_{re.sub(r'[^a-zA-Z0-9]', '_', href.split('/job/')[-1])[-40:]}"

                    jobs.append(JobListing(
                        id=job_id,
                        source="jsremotely",
                        title=title,
                        company=company,
                        location=location,
                        url=href,
                        description=f"Job type: {job_type}. Salary: {salary}",
                        apply_url=href,
                        date=date_text,
                        tags=["JavaScript", "Remote"] + ([job_type] if job_type else []),
                        salary=salary
                    ))
                except Exception:
                    continue

            browser.close()
    except Exception as e:
        print(f"  [JSRemotely] Error: {e}")

    return jobs[:limit]


# --- micro1.ai/refer (AI talent platform) ---
# This platform requires login. Jobs are discovered through the auto-apply engine
# which can create an account and browse/apply to matching positions.

def search_micro1(profile: dict) -> list[JobListing]:
    """
    Search micro1.ai referral platform for AI/tech jobs.
    Requires account — if no account exists, returns empty (auto-apply engine will handle).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    # Check if we have stored credentials
    import sqlite3
    from pathlib import Path
    db_path = Path(__file__).parent / "data" / "jobagent.db"
    if not db_path.exists():
        return []

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    # Check for stored micro1 credentials
    try:
        row = db.execute("SELECT value FROM settings WHERE key = 'micro1_credentials'").fetchone()
    except Exception:
        db.close()
        return []

    if not row:
        db.close()
        return []

    creds = json.loads(row["value"])
    db.close()

    jobs = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
            page = browser.new_page()

            # Login to micro1
            page.goto("https://refer.micro1.ai/", timeout=20000, wait_until="networkidle")
            page.wait_for_timeout(3000)

            # Fill login form
            email_input = page.query_selector("input[type='email']")
            if email_input:
                email_input.fill(creds.get("email", ""))
                page.wait_for_timeout(500)

                # Click continue
                continue_btn = page.query_selector("button:has-text('Continue')")
                if continue_btn:
                    continue_btn.click()
                    page.wait_for_timeout(3000)

            # Look for job listings
            page.wait_for_timeout(3000)
            cards = page.query_selector_all("[class*='job'], [class*='position'], [class*='card']")

            for card in cards[:30]:
                try:
                    link_elem = card.query_selector("a")
                    if not link_elem:
                        continue

                    href = link_elem.get_attribute("href") or ""
                    title = link_elem.inner_text().strip()
                    text = card.inner_text()

                    if not title or len(title) < 3:
                        continue

                    job_id = f"micro1_{re.sub(r'[^a-zA-Z0-9]', '_', href)[-40:]}"

                    jobs.append(JobListing(
                        id=job_id,
                        source="micro1",
                        title=title,
                        company="micro1",
                        location="Remote",
                        url=href if href.startswith("http") else f"https://refer.micro1.ai{href}",
                        description=text[:2000],
                        apply_url=href if href.startswith("http") else f"https://refer.micro1.ai{href}",
                        date="",
                        tags=["AI", "Remote"],
                        salary=""
                    ))
                except Exception:
                    continue

            browser.close()
    except Exception as e:
        print(f"  [micro1] Error: {e}")

    return jobs


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

    # 6. Freelancer.com
    print("  [Freelancer] Fetching RSS...")
    freelancer_keywords = []
    if job_titles:
        freelancer_keywords.extend(job_titles[:5])
    if skills:
        for s in skills[:5]:
            if s not in freelancer_keywords:
                freelancer_keywords.append(s)
    if not freelancer_keywords:
        freelancer_keywords = ["python", "javascript", "react", "node.js", "web development"]
    flc_jobs = search_freelancer(keywords=freelancer_keywords, limit=max_per_source)
    print(f"    -> Found {len(flc_jobs)} jobs")
    all_jobs.extend(flc_jobs)

    # 7. Jobicy
    print("  [Jobicy] Fetching...")
    jobicy_jobs = search_jobicy(limit=max_per_source, query=search_query)
    print(f"    -> Found {len(jobicy_jobs)} jobs")
    all_jobs.extend(jobicy_jobs)

    # 8. Python.org Jobs
    print("  [PythonJobs] Fetching RSS...")
    pyjobs = search_python_jobs(limit=20)
    print(f"    -> Found {len(pyjobs)} jobs")
    all_jobs.extend(pyjobs)

    # 9. Mustakbil.com (Pakistani jobs)
    print("  [Mustakbil] Scraping Pakistani jobs...")
    mtk_jobs = search_mustakbil(limit=max_per_source, query=search_query)
    print(f"    -> Found {len(mtk_jobs)} jobs")
    all_jobs.extend(mtk_jobs)

    # 10. TechJobs.pk (Pakistani tech jobs)
    print("  [TechJobs.pk] Scraping Pakistani tech jobs...")
    tjp_jobs = search_techjobs_pk(limit=max_per_source, query=search_query)
    print(f"    -> Found {len(tjp_jobs)} jobs")
    all_jobs.extend(tjp_jobs)

    # 11. JSRemotely (Remote JS jobs)
    print("  [JSRemotely] Scraping remote JS jobs...")
    jsr_jobs = search_jsremotely(limit=max_per_source)
    print(f"    -> Found {len(jsr_jobs)} jobs")
    all_jobs.extend(jsr_jobs)

    # 12. micro1.ai (if credentials available)
    print("  [micro1.ai] Checking...")
    micro1_jobs = search_micro1(profile)
    print(f"    -> Found {len(micro1_jobs)} jobs")
    all_jobs.extend(micro1_jobs)

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
