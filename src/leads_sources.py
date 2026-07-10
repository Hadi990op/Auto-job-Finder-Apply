"""
Lead Discovery Module — finds startup founders, entrepreneurs, and
freelancer clients who have posted reviews on freelancing platforms.

Sources:
  1. Y Combinator companies (ycombinator.com/companies) — startup founders
  2. ProductHunt (API) — product makers / founders
  3. Wellfound (AngelList) startups — founders + company info
  4. Freelancer.com — clients who reviewed freelancers (public reviews page)
  5. Upwork public profiles — clients with reviews (via search)
  6. Crunchbase (via web scraping) — startup founders + details
  7. GitHub — open-source project owners who might need help
  8. Indie Hackers — indie founders looking for contractors
  9. Twitter/X — founders tweeting about hiring or needing help

Each lead gets:
  - name, title, company, email (if found), social links
  - company description, stage, industry
  - source, review history (for freelancer clients)
  - contact method (email, linkedin, twitter, website form)
"""

import json
import re
import time
import urllib.request
import urllib.parse
from typing import Optional
from dataclasses import dataclass, asdict, field
from datetime import datetime

import httpx
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Lead:
    id: str = ""
    name: str = ""
    title: str = ""
    company: str = ""
    company_url: str = ""
    company_description: str = ""
    industry: str = ""
    stage: str = ""  # Pre-seed, Seed, Series A, etc.
    email: str = ""
    linkedin: str = ""
    twitter: str = ""
    website: str = ""
    source: str = ""
    source_url: str = ""
    lead_type: str = ""  # "founder", "entrepreneur", "freelancer_client"
    review_text: str = ""
    review_rating: float = 0.0
    review_count: int = 0
    freelancer_platform: str = ""  # "freelancer.com", "upwork", etc.
    description: str = ""
    location: str = ""
    first_seen: str = ""
    fit_score: float = 0.0  # how good a lead this is for the user's services
    status: str = "new"  # new, contacted, replied, not_interested
    contact_method: str = ""  # email, twitter_dm, website_form (NO linkedin_dm — LinkedIn restricts DMs)

    def __post_init__(self):
        if not self.id:
            # Generate ID from name + company
            raw = f"{self.name}_{self.company}_{self.source}"
            self.id = re.sub(r"[^a-zA-Z0-9_]", "", raw).lower()[:60]
            if not self.id:
                self.id = f"lead_{int(time.time() * 1000)}"


HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch(url: str, timeout: int = 8) -> str:
    """Fetch a URL and return HTML text."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            # Limit response size to 500KB to avoid huge pages
            return data[:500000].decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [Leads] Fetch error for {url}: {e}")
        return ""


def _fetch_json(url: str, timeout: int = 8) -> dict:
    """Fetch a URL and return parsed JSON."""
    try:
        req = urllib.request.Request(url, headers={**HEADERS, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"  [Leads] JSON fetch error for {url}: {e}")
        return {}


# ---------------------------------------------------------------------------
# Source 1: Y Combinator Companies
# ---------------------------------------------------------------------------

def scrape_yc_companies(max_results: int = 50) -> list:
    """Scrape Y Combinator companies directory for startup founders."""
    print("  [Leads] Scraping Y Combinator companies...")
    leads = []

    # YC companies API endpoint (returns JSON)
    url = "https://www.ycombinator.com/companies?api=true"
    data = _fetch_json(url)
    if not data or "companies" not in data:
        # Fallback to scraping the page
        html = _fetch("https://www.ycombinator.com/companies")
        if not html:
            print("  [Leads] YC: no data")
            return []
        # Try to extract company data from the HTML
        soup = BeautifulSoup(html, "lxml")
        # YC embeds data in script tags
        scripts = soup.find_all("script")
        for script in scripts:
            text = script.string or ""
            if "companies" in text and "name" in text:
                try:
                    # Find JSON in script
                    start = text.find("{")
                    if start >= 0:
                        data = json.loads(text[start:])
                        break
                except:
                    pass
        if not data:
            # Parse from HTML cards
            cards = soup.select("[class*='company']")
            for card in cards[:max_results]:
                name_el = card.find("a")
                if not name_el:
                    continue
                name = name_el.get_text(strip=True)
                url = f"https://www.ycombinator.com{name_el.get('href', '')}"
                desc = card.find("p")
                leads.append(Lead(
                    name=name,
                    title="Founder",
                    company=name,
                    company_url=url,
                    company_description=desc.get_text(strip=True) if desc else "",
                    source="ycombinator",
                    source_url=url,
                    lead_type="founder",
                ))
            return leads[:max_results]

    for company in data.get("companies", [])[:max_results]:
        name = company.get("name", "")
        company_url = f"https://www.ycombinator.com/companies/{company.get('slug', name.lower().replace(' ', '-'))}"
        desc = company.get("one_liner", "") or company.get("description", "")
        industry = company.get("industry", "")
        stage = company.get("stage", "")
        website = company.get("website", "")
        location = company.get("location", "")

        leads.append(Lead(
            name=name,
            title="Founder / CEO",
            company=name,
            company_url=company_url,
            company_description=desc,
            industry=industry,
            stage=stage,
            website=website,
            location=location,
            source="ycombinator",
            source_url=company_url,
            lead_type="founder",
        ))

    print(f"  [Leads] YC: found {len(leads)} companies")
    return leads


# ---------------------------------------------------------------------------
# Source 2: ProductHunt
# ---------------------------------------------------------------------------

def scrape_producthunt(max_results: int = 30) -> list:
    """Scrape ProductHunt for recent product makers/founders."""
    print("  [Leads] Scraping ProductHunt...")
    leads = []

    html = _fetch("https://www.producthunt.com/")
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")

    # ProductHunt embeds data in script tags
    for script in soup.find_all("script", type="application/json"):
        text = script.string or ""
        if '"name"' in text and '"tagline"' in text:
            try:
                data = json.loads(text)
                _extract_ph_leads(data, leads, max_results)
            except:
                pass
            if len(leads) >= max_results:
                break

    # Fallback: parse from data attributes or rendered HTML
    if not leads:
        for item in soup.select("[data-test*='post-item'], [class*='post']")[:max_results]:
            name_el = item.select_one("a[href*='/posts/']")
            if not name_el:
                continue
            product_name = name_el.get_text(strip=True)
            product_url = "https://www.producthunt.com" + name_el.get("href", "")
            tagline_el = item.select_one("[class*='tagline'], [class*='subtitle']")
            tagline = tagline_el.get_text(strip=True) if tagline_el else ""

            leads.append(Lead(
                name=product_name,
                title="Maker / Founder",
                company=product_name,
                company_url=product_url,
                company_description=tagline,
                source="producthunt",
                source_url=product_url,
                lead_type="founder",
            ))

    print(f"  [Leads] ProductHunt: found {len(leads)} leads")
    return leads[:max_results]


def _extract_ph_leads(data: dict, leads: list, max_results: int):
    """Recursively extract product/maker info from ProductHunt JSON data."""
    if len(leads) >= max_results:
        return

    if isinstance(data, dict):
        # Check if this looks like a product entry
        if "name" in data and ("tagline" in data or "description" in data) and "votesCount" in data:
            name = data.get("name", "")
            tagline = data.get("tagline", "") or data.get("description", "")
            url = data.get("url", "") or data.get("website", "")
            slug = data.get("slug", "")
            ph_url = f"https://www.producthunt.com/posts/{slug}" if slug else ""
            maker = data.get("makers", [])
            if maker and isinstance(maker, list):
                for m in maker[:1]:  # Take first maker
                    leads.append(Lead(
                        name=m.get("name", name),
                        title="Maker / Founder",
                        company=name,
                        company_url=ph_url,
                        company_description=tagline,
                        website=url,
                        source="producthunt",
                        source_url=ph_url,
                        lead_type="founder",
                    ))
            else:
                leads.append(Lead(
                    name=name,
                    title="Maker / Founder",
                    company=name,
                    company_url=ph_url,
                    company_description=tagline,
                    website=url,
                    source="producthunt",
                    source_url=ph_url,
                    lead_type="founder",
                ))
        # Recurse into nested structures
        for v in data.values():
            if isinstance(v, (dict, list)):
                _extract_ph_leads(v, leads, max_results)
    elif isinstance(data, list):
        for item in data:
            _extract_ph_leads(item, leads, max_results)


# ---------------------------------------------------------------------------
# Source 3: Wellfound (AngelList) startups
# ---------------------------------------------------------------------------

def scrape_wellfound(max_results: int = 30) -> list:
    """Scrape Wellfound for startups and their founders."""
    print("  [Leads] Scraping Wellfound startups...")
    leads = []

    url = "https://wellfound.com/startups"
    html = _fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")

    for card in soup.select("[class*='startup-card'], [data-test*='startup'], div[class*='CompanyCard']")[:max_results]:
        name_el = card.select_one("a[href*='/company/']")
        if not name_el:
            continue
        company_name = name_el.get_text(strip=True)
        company_url = "https://wellfound.com" + name_el.get("href", "")

        desc_el = card.select_one("p, [class*='description'], [class*='tagline']")
        desc = desc_el.get_text(strip=True) if desc_el else ""

        # Try to find founder info
        founder_el = card.select_one("[class*='founder'], [class*='ceo']")
        founder_name = founder_el.get_text(strip=True) if founder_el else ""

        leads.append(Lead(
            name=founder_name or company_name,
            title="Founder",
            company=company_name,
            company_url=company_url,
            company_description=desc,
            source="wellfound",
            source_url=company_url,
            lead_type="founder",
        ))

    print(f"  [Leads] Wellfound: found {len(leads)} leads")
    return leads


# ---------------------------------------------------------------------------
# Source 4: Freelancer.com — clients who left reviews
# ---------------------------------------------------------------------------

def scrape_freelancer_reviews(max_results: int = 30) -> list:
    """
    Scrape Freelancer.com for clients who have reviewed freelancers.
    These are high-value leads — they already hire freelancers and have budget.
    """
    print("  [Leads] Scraping Freelancer.com client reviews...")
    leads = []

    # Freelancer.com has public employer profiles with reviews
    # We search for employers who have recent reviews
    url = "https://www.freelancer.com/job/"
    html = _fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")

    # Parse job posts to find employers
    for item in soup.select("[class*='JobSearchCard-item'], [class*='project-item']")[:max_results]:
        # Employer name
        emp_el = item.select_one("[class*='employer'], [class*='client'], a[href*='/users/']")
        if not emp_el:
            continue

        emp_name = emp_el.get_text(strip=True)
        emp_url = emp_el.get("href", "")
        if emp_url and not emp_url.startswith("http"):
            emp_url = "https://www.freelancer.com" + emp_url

        # Job title / description
        title_el = item.select_one("a[class*='title'], [class*='project-title']")
        title = title_el.get_text(strip=True) if title_el else ""

        desc_el = item.select_one("p, [class*='description']")
        desc = desc_el.get_text(strip=True) if desc_el else ""

        leads.append(Lead(
            name=emp_name,
            title="Client / Employer",
            company=emp_name,
            company_url=emp_url,
            company_description=title,
            description=desc,
            source="freelancer",
            source_url=emp_url,
            lead_type="freelancer_client",
            freelancer_platform="freelancer.com",
        ))

    print(f"  [Leads] Freelancer.com: found {len(leads)} client leads")
    return leads


# ---------------------------------------------------------------------------
# Source 5: Indie Hackers
# ---------------------------------------------------------------------------

def scrape_indiehackers(max_results: int = 20) -> list:
    """Scrape Indie Hackers for founders looking for help."""
    print("  [Leads] Scraping Indie Hackers...")
    leads = []

    # Indie Hackers has a public API/feed
    url = "https://www.indiehackers.com/api/graphql"  # May need query
    html = _fetch("https://www.indiehackers.com/")
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")

    # Parse for founder/product info
    for item in soup.select("[class*='product'], [class*='milestone'], article")[:max_results]:
        name_el = item.select_one("a[href*='/products/'], a[href*='/users/']")
        if not name_el:
            continue
        name = name_el.get_text(strip=True)
        link = name_el.get("href", "")
        if link and not link.startswith("http"):
            link = "https://www.indiehackers.com" + link

        desc_el = item.select_one("p, [class*='description'], [class*='tagline']")
        desc = desc_el.get_text(strip=True) if desc_el else ""

        leads.append(Lead(
            name=name,
            title="Indie Founder",
            company=name,
            company_url=link,
            company_description=desc,
            source="indiehackers",
            source_url=link,
            lead_type="entrepreneur",
        ))

    print(f"  [Leads] Indie Hackers: found {len(leads)} leads")
    return leads


# ---------------------------------------------------------------------------
# Source 6: GitHub — open source project owners
# ---------------------------------------------------------------------------

def scrape_github_founders(max_results: int = 20, keywords: str = "startup saas platform") -> list:
    """
    Search GitHub for repos that look like startups/products.
    The repo owner is a potential lead (they're building something).
    """
    print(f"  [Leads] Searching GitHub for product repos (keywords: {keywords})...")
    leads = []

    query = urllib.parse.quote(keywords)
    url = f"https://api.github.com/search/repositories?q={query}&sort=updated&per_page={max_results}"
    data = _fetch_json(url)

    if not data or "items" not in data:
        return []

    for repo in data.get("items", [])[:max_results]:
        owner = repo.get("owner", {})
        owner_name = owner.get("login", "")
        owner_url = owner.get("html_url", "")
        owner_type = owner.get("type", "")

        if owner_type != "User":  # Skip organizations, focus on individual builders
            continue

        repo_name = repo.get("name", "")
        repo_url = repo.get("html_url", "")
        description = repo.get("description", "") or ""
        topics = repo.get("topics", [])
        stars = repo.get("stargazers_count", 0)
        homepage = repo.get("homepage", "") or ""

        # Skip if too many stars (well-funded) — keep low-star repos (early stage builders)
        if stars > 5000 or stars < 1:
            continue

        # Use homepage if available (actual website), otherwise repo URL
        website = homepage if homepage and homepage.startswith("http") else repo_url

        leads.append(Lead(
            name=owner_name,
            title="Developer / Founder",
            company=repo_name,
            company_url=repo_url,
            company_description=description,
            industry=", ".join(topics) if topics else "",
            website=website,
            source="github",
            source_url=owner_url,
            lead_type="entrepreneur",
            description=f"GitHub repo: {repo_name} ({stars} stars). {description[:200]}",
        ))

    print(f"  [Leads] GitHub: found {len(leads)} builder leads")
    return leads


# ---------------------------------------------------------------------------
# Source 7: RemoteOK / WeWorkRemotely — companies hiring (founders)
# ---------------------------------------------------------------------------

def scrape_job_boards_for_founders(max_results: int = 20) -> list:
    """Scrape job boards for companies hiring — the hiring company is a lead."""
    print("  [Leads] Scraping job boards for hiring companies...")
    leads = []

    # RemoteOK
    data = _fetch_json("https://remoteok.com/api")
    if data and isinstance(data, list):
        seen_companies = set()
        for job in data[1:][:max_results * 3]:  # Check more to find unique companies
            if not isinstance(job, dict):
                continue
            company = job.get("company", "")
            if not company or company in seen_companies:
                continue
            seen_companies.add(company)
            company_url = job.get("company_url", "")
            position = job.get("position", "")
            tags = job.get("tags", [])
            description = job.get("description", "")[:500]
            # Try to build a website URL from company name if not provided
            if not company_url:
                slug = company.lower().replace(" ", "").replace(",", "")
                company_url = f"https://{slug}.com"

            leads.append(Lead(
                name=company,
                title="Hiring Manager / Founder",
                company=company,
                company_url=company_url,
                company_description=f"Hiring for: {position}",
                industry=", ".join(tags) if tags else "",
                website=company_url if company_url.startswith("http") else "",
                source="remoteok",
                source_url=job.get("url", ""),
                lead_type="founder",
                description=description,
            ))
            if len(leads) >= max_results:
                break

    print(f"  [Leads] Job boards: found {len(leads)} hiring company leads")
    return leads


# ---------------------------------------------------------------------------
# Source 8: GitHub repos WITH homepages (actual product websites)
# ---------------------------------------------------------------------------

def scrape_github_with_homepages(max_results: int = 20, keywords: str = "saas platform") -> list:
    """
    Search GitHub for repos that have a homepage URL set — these are actual
    products/startups with real websites, not just code repos.
    """
    print(f"  [Leads] Searching GitHub for repos with homepages (keywords: {keywords})...")
    leads = []

    # Search for repos with homepages — add "has:homepage" isn't a GitHub filter,
    # but we can filter client-side. Use a more targeted search.
    query = urllib.parse.quote(f"{keywords} stars:1..2000")
    url = f"https://api.github.com/search/repositories?q={query}&sort=updated&per_page={max_results * 3}"
    data = _fetch_json(url)

    if not data or "items" not in data:
        return []

    count = 0
    for repo in data.get("items", []):
        if count >= max_results:
            break

        owner = repo.get("owner", {})
        owner_name = owner.get("login", "")
        owner_type = owner.get("type", "")
        if owner_type != "User":
            continue

        homepage = repo.get("homepage", "") or ""
        if not homepage or not homepage.startswith("http"):
            continue  # Skip repos without a real homepage

        repo_name = repo.get("name", "")
        repo_url = repo.get("html_url", "")
        description = repo.get("description", "") or ""
        topics = repo.get("topics", [])
        stars = repo.get("stargazers_count", 0)

        leads.append(Lead(
            name=owner_name,
            title="Developer / Founder",
            company=repo_name,
            company_url=repo_url,
            company_description=description,
            industry=", ".join(topics) if topics else "",
            website=homepage,
            source="github",
            source_url=owner.get("html_url", ""),
            lead_type="entrepreneur",
            description=f"GitHub repo: {repo_name} ({stars} stars, homepage: {homepage}). {description[:200]}",
        ))
        count += 1

    print(f"  [Leads] GitHub (homepages): found {len(leads)} leads with real websites")
    return leads


# ---------------------------------------------------------------------------
# Source 9: Hacker News "Who is Hiring" thread
# ---------------------------------------------------------------------------

def scrape_hn_who_is_hiring(max_results: int = 20) -> list:
    """
    Scrape Hacker News "Who is Hiring" thread for companies hiring.
    These are high-quality leads — companies actively looking for developers.
    """
    print("  [Leads] Scraping Hacker News Who is Hiring...")
    leads = []

    try:
        # Find the latest "Ask HN: Who is hiring" thread
        # Search HN Algolia API
        search_url = "https://hn.algolia.com/api/v1/search?query=Ask+HN+Who+is+hiring&tags=story&hitsPerPage=1"
        data = _fetch_json(search_url)
        if not data or "hits" not in data or not data["hits"]:
            return []

        thread = data["hits"][0]
        thread_id = thread.get("objectID", "")
        if not thread_id:
            return []

        # Get comments (job postings) from the thread
        comments_url = f"https://hn.algolia.com/api/v1/search?tags=comment,story_{thread_id}&hitsPerPage={max_results}"
        comments_data = _fetch_json(comments_url)
        if not comments_data or "hits" not in comments_data:
            return []

        for hit in comments_data["hits"][:max_results]:
            text = hit.get("comment_text", "") or hit.get("story_text", "") or ""
            author = hit.get("author", "")
            url = hit.get("url", "") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"

            # Parse the comment for company name and contact info
            # HN who-is-hiring posts typically start with: "Company Name | Location | Role"
            lines = text.split("\n")
            first_line = lines[0] if lines else ""
            # Strip HTML tags
            first_line = re.sub(r"<[^>]+>", "", first_line).strip()

            # Extract email from the comment
            email = _extract_emails_from_html(text)

            # Extract URLs from comment
            urls_in_text = re.findall(r'https?://[^\s<>"\']+', re.sub(r"<[^>]+>", " ", text))

            # Use first line as company/description
            company_name = first_line.split("|")[0].strip() if "|" in first_line else first_line[:50]
            if not company_name:
                company_name = f"HN User ({author})"

            website = ""
            for u in urls_in_text:
                if "ycombinator" not in u and "hn.algolia" not in u:
                    website = u
                    break

            leads.append(Lead(
                name=company_name,
                title="Hiring Company",
                company=company_name,
                company_url=website or url,
                company_description=first_line[:300],
                email=email,
                website=website,
                source="hackernews",
                source_url=url,
                lead_type="founder",
                description=re.sub(r"<[^>]+>", "", text)[:500],
            ))

    except Exception as e:
        print(f"  [Leads] HN error: {e}")

    print(f"  [Leads] Hacker News: found {len(leads)} hiring leads")
    return leads


# ---------------------------------------------------------------------------
# Source: Exa Web Search (via Agent-Reach / mcporter)
# Finds companies actively hiring freelance/contract developers
# ---------------------------------------------------------------------------

def scrape_exa_search(max_results: int = 15, keywords: str = "saas platform") -> list:
    """
    Use Exa AI web search (via mcporter) to find companies hiring freelance/contract developers.
    This is the highest-quality source — returns real job postings with company details.
    """
    leads = []
    try:
        import subprocess
        # Build search queries based on user's keywords
        queries = [
            f"companies hiring freelance developer {keywords} 2026",
            f"contract developer wanted {keywords} remote",
            f"looking for freelance backend API developer startup",
        ]

        seen_urls = set()
        for query in queries:
            try:
                result = subprocess.run(
                    ["mcporter", "call", f"exa.web_search_exa(query: \"{query}\", numResults: {max_results})"],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode != 0 or not result.stdout:
                    continue

                # Parse the Exa output format (Title: ...\nURL: ...\n---)
                blocks = result.stdout.split("---")
                for block in blocks:
                    block = block.strip()
                    if not block:
                        continue

                    title = ""
                    url = ""
                    for line in block.split("\n"):
                        line = line.strip()
                        if line.startswith("Title:"):
                            title = line[6:].strip()
                        elif line.startswith("URL:"):
                            url = line[4:].strip()

                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)

                    # Skip job boards we already scrape directly
                    skip_domains = ["remoteok.com", "wellfound.com", "ycombinator.com",
                                   "producthunt.com", "freelancer.com", "indeed.com",
                                   "glassdoor.com", "linkedin.com/jobs", "stackoverflow.com/jobs"]
                    if any(d in url.lower() for d in skip_domains):
                        continue

                    # Extract company name from title or URL
                    company_name = title.split(" - ")[0].split(" | ")[0].split(" at ")[-1].strip() if title else ""
                    if len(company_name) > 80:
                        company_name = company_name[:80]

                    lead = Lead(
                        id=f"exa_{hash(url) % 10000000}",
                        name=company_name or "Unknown",
                        company=company_name or "Unknown",
                        website=url,
                        source="exa_search",
                        source_url=url,
                        description=title,
                    )
                    leads.append(lead)

                    if len(leads) >= max_results:
                        break
                if len(leads) >= max_results:
                    break
            except subprocess.TimeoutExpired:
                print(f"  [Leads] Exa search timed out for query: {query[:40]}")
                continue
            except Exception as e:
                print(f"  [Leads] Exa search error: {e}")
                continue

    except ImportError:
        print("  [Leads] mcporter not available for Exa search")
    except Exception as e:
        print(f"  [Leads] Exa search error: {e}")

    print(f"  [Leads] Exa search: found {len(leads)} leads")
    return leads


# ---------------------------------------------------------------------------
# Enrichment helper: Jina Reader fallback
# ---------------------------------------------------------------------------

def _fetch_via_jina(url: str, timeout: int = 15) -> str:
    """
    Fallback: fetch a URL via Jina Reader API (https://r.jina.ai/URL).
    Returns clean Markdown text — useful when direct fetch fails (403, bot detection, etc).
    """
    try:
        jina_url = f"https://r.jina.ai/{url}"
        req = urllib.request.Request(jina_url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")[:200000]
    except Exception as e:
        print(f"  [Leads] Jina Reader fallback failed for {url}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Aggregate all sources
# ---------------------------------------------------------------------------

def discover_all_leads(max_per_source: int = 30, keywords: str = "startup saas platform") -> list:
    """
    Discover leads from all sources.
    Returns a deduplicated list of Lead objects.
    """
    all_leads = []
    seen_ids = set()

    sources = [
        ("exa_search", lambda: scrape_exa_search(max_per_source, keywords)),
        ("github_homepages", lambda: scrape_github_with_homepages(max_per_source, keywords)),
        ("github", lambda: scrape_github_founders(20, keywords)),
        ("remoteok", lambda: scrape_job_boards_for_founders(max_per_source)),
        ("hackernews", lambda: scrape_hn_who_is_hiring(max_per_source)),
        ("ycombinator", lambda: scrape_yc_companies(max_per_source)),
        ("producthunt", lambda: scrape_producthunt(max_per_source)),
        ("wellfound", lambda: scrape_wellfound(max_per_source)),
        ("freelancer", lambda: scrape_freelancer_reviews(max_per_source)),
        ("indiehackers", lambda: scrape_indiehackers(max_per_source)),
    ]

    for name, scraper in sources:
        try:
            results = scraper()
            for lead in results:
                if lead.id not in seen_ids:
                    lead.first_seen = datetime.now().isoformat()
                    all_leads.append(lead)
                    seen_ids.add(lead.id)
        except Exception as e:
            print(f"  [Leads] Error scraping {name}: {e}")

    print(f"  [Leads] Total unique leads discovered: {len(all_leads)}")
    return all_leads


def _extract_emails_from_html(html: str) -> str:
    """Extract real email addresses from HTML, filtering out generic ones."""
    # Strict email pattern — requires a valid TLD (2+ alpha chars only)
    email_pattern = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    emails = email_pattern.findall(html)
    # Filter out generic emails but KEEP contact@ and info@ (they're still valid for outreach)
    generic_block = {"noreply@", "admin@", "no-reply@", "sentry@", "wixpress@", "squarespace@", "checkout@", "noreply+"}
    # Also filter out placeholder/example emails
    placeholder_patterns = ["your-email", "example@", "test@", "demo@", "user@", "email@",
                           "you@", "changeme", "placeholder", "yourname"]
    real_emails = [e for e in emails
                   if not any(g in e.lower() for g in generic_block)
                   and not any(p in e.lower() for p in placeholder_patterns)]
    return real_emails[0] if real_emails else ""


def enrich_lead(lead: Lead) -> Lead:
    """
    Enrich a lead by fetching its source page and extracting more details:
    - Email (checks source page, then website, then /contact page)
    - LinkedIn profile (stored for reference, NOT used for DMs)
    - Twitter
    - Company description

    Contact method priority: email > website_form > twitter_dm > manual
    NO LinkedIn DMs — LinkedIn restricts automated DMs.
    """
    urls_to_check = []
    if lead.source_url:
        urls_to_check.append(lead.source_url)
    if lead.website and lead.website != lead.source_url:
        urls_to_check.append(lead.website)

    # Skip fetching github.com URLs — they won't have contact info
    urls_to_check = [u for u in urls_to_check if "github.com" not in u]

    # For GitHub leads, also check the user's public profile API for email
    if lead.source == "github" and lead.name:
        try:
            user_data = _fetch_json(f"https://api.github.com/users/{lead.name}")
            if user_data and user_data.get("email"):
                lead.email = user_data["email"]
            if user_data and user_data.get("blog") and not lead.website:
                blog = user_data["blog"]
                if not blog.startswith("http"):
                    blog = "https://" + blog
                lead.website = blog
                urls_to_check.append(blog)
            if user_data and user_data.get("twitter_username") and not lead.twitter:
                lead.twitter = f"https://twitter.com/{user_data['twitter_username']}"
        except Exception as e:
            print(f"  [Leads] GitHub user API error for {lead.name}: {e}")

    for url in urls_to_check:
        try:
            html = _fetch(url, timeout=15)
            if not html:
                # Fallback: try Jina Reader (handles 403/bot-blocked sites)
                html = _fetch_via_jina(url, timeout=15)
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")

            # Extract email from mailto: links first (most reliable)
            if not lead.email:
                mailto_links = soup.select("a[href^='mailto:']")
                for ml in mailto_links:
                    href = ml.get("href", "")
                    addr = href.replace("mailto:", "").split("?")[0].strip()
                    if addr and "@" in addr and not any(g in addr.lower() for g in {"noreply", "no-reply", "sentry", "wixpress", "squarespace"}):
                        lead.email = addr
                        break

            # Extract email from page text
            if not lead.email:
                lead.email = _extract_emails_from_html(html)

            # Look for contact page link and check it too
            if not lead.email:
                for contact_sel in [
                    "a[href*='contact']", "a[href*='Contact']",
                    "a[href*='/about']", "a[href*='about']",
                    "a[href*='/team']", "a[href*='team']",
                    "a[href*='/privacy']", "a[href*='privacy']",
                ]:
                    try:
                        link = soup.select_one(contact_sel)
                        if link:
                            contact_url = link.get("href", "")
                            if contact_url and not contact_url.startswith("http"):
                                # Make absolute URL
                                from urllib.parse import urljoin
                                contact_url = urljoin(url, contact_url)
                            if contact_url and contact_url not in urls_to_check:
                                contact_html = _fetch(contact_url, timeout=10)
                                if not contact_html:
                                    contact_html = _fetch_via_jina(contact_url, timeout=12)
                                if contact_html:
                                    # Check mailto: links on contact page
                                    contact_soup = BeautifulSoup(contact_html, "lxml")
                                    mailto_links = contact_soup.select("a[href^='mailto:']")
                                    for ml in mailto_links:
                                        href = ml.get("href", "")
                                        addr = href.replace("mailto:", "").split("?")[0].strip()
                                        if addr and "@" in addr and not any(g in addr.lower() for g in {"noreply", "no-reply", "sentry"}):
                                            lead.email = addr
                                            break
                                    if not lead.email:
                                        lead.email = _extract_emails_from_html(contact_html)
                                    if lead.email:
                                        break
                    except:
                        continue

            # Extract LinkedIn (for reference only, NOT for DMs)
            if not lead.linkedin:
                linkedin_el = soup.select_one("a[href*='linkedin.com/company/'], a[href*='linkedin.com/in/']")
                if linkedin_el:
                    lead.linkedin = linkedin_el.get("href", "")

            # Extract Twitter
            if not lead.twitter:
                twitter_el = soup.select_one("a[href*='twitter.com/'], a[href*='x.com/']")
                if twitter_el:
                    lead.twitter = twitter_el.get("href", "")

            # Better company description
            if not lead.company_description:
                meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
                if meta_desc:
                    lead.company_description = meta_desc.get("content", "")

            if lead.email:
                break  # Found email, no need to check more URLs

        except Exception as e:
            print(f"  [Leads] Enrichment error for {lead.name} on {url}: {e}")

    # If still no email but we have a website, try common email patterns
    # (e.g. founder@company.com, contact@company.com, info@company.com)
    if not lead.email and lead.website and lead.website.startswith("http"):
        try:
            from urllib.parse import urlparse
            domain = urlparse(lead.website).netloc.replace("www.", "")
            # Don't guess emails for github.com, vercel.app, etc.
            skip_domains = {"github.com", "vercel.app", "netlify.app", "herokuapp.com",
                           "gitlab.io", "github.io", "pages.dev", "fly.dev"}
            if domain and not any(domain.endswith(s) for s in skip_domains):
                # Try common business email patterns
                common_prefixes = ["contact", "info", "hello", "founders", "team"]
                for prefix in common_prefixes:
                    candidate = f"{prefix}@{domain}"
                    if not lead.email:
                        lead.email = candidate  # Best guess — may bounce but worth trying
                        break
        except Exception:
            pass

    # Determine best contact method — NO LinkedIn DMs
    # Priority: email > website_form > twitter_dm > manual
    if lead.email:
        lead.contact_method = "email"
    elif lead.website:
        lead.contact_method = "website_form"
    elif lead.twitter:
        lead.contact_method = "twitter_dm"
    else:
        lead.contact_method = "manual"

    return lead


if __name__ == "__main__":
    leads = discover_all_leads(max_per_source=10)
    for l in leads[:10]:
        print(json.dumps(asdict(l), indent=2))
