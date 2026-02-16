"""Multi-strategy email discovery for job applications.

Tries multiple approaches to find a contact/application email:
1. Scan the job description text
2. Follow the LinkedIn apply URL for mailto: links or emails
3. Find company website from LinkedIn, then scrape it for emails
4. Check common pages (/careers, /jobs, /contact, /about)
5. Generate common HR email patterns from the company domain
"""

from __future__ import annotations

import re
import time
import random
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

SKIP_DOMAINS = {
    "example.com", "test.com", "linkedin.com", "licdn.com",
    "facebook.com", "twitter.com", "x.com", "google.com",
    "googleapis.com", "github.com", "githubusercontent.com",
    "sentry.io", "gravatar.com", "wp.com", "wordpress.com",
    "w3.org", "schema.org", "cloudflare.com", "amazonaws.com",
    "gstatic.com", "bootstrapcdn.com", "jquery.com",
}

SKIP_PREFIXES = {
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "webmaster", "admin",
    "support", "abuse", "security", "privacy",
}

COMMON_HR_PREFIXES = [
    "hr", "careers", "jobs", "hiring", "recruiting",
    "recruitment", "talent", "apply", "career", "people",
]

CAREER_PATHS = [
    "/careers", "/jobs", "/contact", "/about",
    "/contact-us", "/about-us", "/work-with-us",
    "/join-us", "/join", "/hiring",
]

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def _get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _is_valid_email(email: str) -> bool:
    """Check if an email looks like a real contact address."""
    lower = email.lower()
    domain = lower.split("@")[-1] if "@" in lower else ""
    prefix = lower.split("@")[0] if "@" in lower else ""

    if domain in SKIP_DOMAINS:
        return False
    if prefix in SKIP_PREFIXES:
        return False
    if domain.endswith(".png") or domain.endswith(".jpg") or domain.endswith(".gif"):
        return False
    if len(email) > 80:
        return False

    return True


def _extract_emails_from_text(text: str) -> list[str]:
    """Extract all valid emails from a block of text."""
    raw = EMAIL_PATTERN.findall(text)
    return [e for e in raw if _is_valid_email(e)]


def _fetch_page(url: str, timeout: int = 12) -> Optional[BeautifulSoup]:
    """Fetch a page and return parsed soup, or None on failure."""
    try:
        time.sleep(random.uniform(0.8, 1.8))
        resp = requests.get(url, headers=_get_headers(), timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return BeautifulSoup(resp.text, "html.parser")
    except Exception:
        pass
    return None


def _extract_emails_from_soup(soup: BeautifulSoup) -> list[str]:
    """Extract emails from a parsed HTML page (text + mailto links)."""
    emails = set()

    # Check mailto: links
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if href.startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if _is_valid_email(addr):
                emails.add(addr.lower())

    # Check page text
    page_text = soup.get_text(separator=" ", strip=True)
    for email in _extract_emails_from_text(page_text):
        emails.add(email.lower())

    return list(emails)


def _get_company_website_from_linkedin(company_name: str, job_page_soup: Optional[BeautifulSoup] = None) -> str:
    """Try to find the company's website from the LinkedIn job page."""
    if not job_page_soup:
        return ""

    # Look for company link in the job page
    for a_tag in job_page_soup.find_all("a", href=True):
        href = a_tag["href"]
        text = a_tag.get_text(strip=True).lower()

        # Direct website links in the job description
        if any(kw in text for kw in ["website", "our site", "company site", "visit us"]):
            if "linkedin.com" not in href:
                return href

    # Look for links that appear to be company websites in the description
    desc_div = job_page_soup.find("div", class_="show-more-less-html__markup")
    if desc_div:
        for a_tag in desc_div.find_all("a", href=True):
            href = a_tag["href"]
            parsed = urlparse(href)
            if parsed.scheme in ("http", "https") and "linkedin.com" not in href:
                return f"{parsed.scheme}://{parsed.netloc}"

    return ""


def _scrape_website_for_emails(base_url: str, max_pages: int = 4) -> list[str]:
    """Scrape a company website for contact emails.

    Checks the homepage + common career/contact pages.
    """
    emails = set()
    visited = set()
    parsed_base = urlparse(base_url)
    base_domain = f"{parsed_base.scheme}://{parsed_base.netloc}"

    pages_to_check = [base_domain] + [
        base_domain + path for path in CAREER_PATHS
    ]

    checked = 0
    for page_url in pages_to_check:
        if checked >= max_pages:
            break
        if page_url in visited:
            continue
        visited.add(page_url)

        soup = _fetch_page(page_url)
        if not soup:
            continue
        checked += 1

        found = _extract_emails_from_soup(soup)
        for email in found:
            emails.add(email)

        if emails:
            break

    return list(emails)


def _rank_emails(emails: list[str]) -> list[str]:
    """Rank emails by likelihood of being an HR/application email.

    HR-like prefixes get priority, then generic ones.
    """
    if not emails:
        return []

    hr_emails = []
    other_emails = []

    for email in emails:
        prefix = email.split("@")[0].lower()
        if any(hr in prefix for hr in COMMON_HR_PREFIXES):
            hr_emails.append(email)
        else:
            other_emails.append(email)

    return hr_emails + other_emails


def _generate_common_patterns(domain: str) -> list[str]:
    """Generate common HR email addresses for a domain."""
    return [f"{prefix}@{domain}" for prefix in COMMON_HR_PREFIXES[:6]]


def find_application_email(
    job_description: str,
    job_url: str = "",
    company_name: str = "",
    job_page_soup: Optional[BeautifulSoup] = None,
) -> str:
    """Try multiple strategies to find a contact email for a job.

    Strategy order:
    1. Scan job description text for emails
    2. Scan the LinkedIn job page HTML for mailto: / emails
    3. Find company website and scrape it
    4. If we found a company domain, generate common patterns

    Returns the best email found, or empty string.
    """
    # Strategy 1: Job description text
    desc_emails = _extract_emails_from_text(job_description)
    if desc_emails:
        ranked = _rank_emails(desc_emails)
        if ranked:
            print(f"    [Email Finder] Found in job description: {ranked[0]}")
            return ranked[0]

    # Strategy 2: LinkedIn job page HTML (mailto links, embedded emails)
    if job_page_soup:
        page_emails = _extract_emails_from_soup(job_page_soup)
        if page_emails:
            ranked = _rank_emails(page_emails)
            if ranked:
                print(f"    [Email Finder] Found on job page: {ranked[0]}")
                return ranked[0]

    # Strategy 3: Company website
    company_website = _get_company_website_from_linkedin(company_name, job_page_soup)

    if not company_website and company_name:
        # Try a simple Google-style guess: company name -> domain
        clean_name = re.sub(r"[^a-zA-Z0-9]", "", company_name.lower())
        if clean_name:
            guessed = f"https://www.{clean_name}.com"
            soup = _fetch_page(guessed)
            if soup:
                company_website = guessed

    if company_website:
        print(f"    [Email Finder] Checking company website: {company_website}")
        site_emails = _scrape_website_for_emails(company_website)
        if site_emails:
            ranked = _rank_emails(site_emails)
            if ranked:
                print(f"    [Email Finder] Found on company website: {ranked[0]}")
                return ranked[0]

        # Strategy 4: Common patterns from the company domain
        parsed = urlparse(company_website)
        domain = parsed.netloc.lstrip("www.")
        if domain and "." in domain:
            patterns = _generate_common_patterns(domain)
            print(f"    [Email Finder] Using pattern-based email: {patterns[0]}")
            return patterns[0]

    print(f"    [Email Finder] No email found.")
    return ""
