"""Scrape LinkedIn posts for job openings via Brave Search.

LinkedIn posts (linkedin.com/posts/) are where people directly share
"we're hiring" with contact emails. These are more valuable than the
jobs section because they often include a direct email address.

Strategy:
1. Search Brave for LinkedIn posts containing hiring keywords
2. Fetch the LinkedIn post pages (publicly accessible)
3. Extract job info, company name, and contact emails
"""

from __future__ import annotations

import re
import time
import random
from typing import Optional
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

from src.email_finder import _extract_emails_from_text, _is_valid_email
from src.models import Job, JobSource, SearchConfig

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)


def _get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _search_brave(query: str, max_results: int = 15) -> list[str]:
    """Search Brave for LinkedIn post URLs.

    Returns a deduplicated list of linkedin.com/posts/ URLs.
    """
    urls: list[str] = []
    seen: set[str] = set()

    try:
        time.sleep(random.uniform(1.0, 2.0))
        search_url = f"https://search.brave.com/search?q={quote(query)}"
        resp = requests.get(search_url, headers=_get_headers(), timeout=15)
        if resp.status_code != 200:
            print(f"    [Posts] Brave search returned status {resp.status_code}")
            return urls

        soup = BeautifulSoup(resp.text, "html.parser")

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if _is_linkedin_post_url(href):
                clean = href.split("?")[0].rstrip("/")
                if clean not in seen:
                    seen.add(clean)
                    urls.append(href)
                    if len(urls) >= max_results:
                        break

    except Exception as e:
        print(f"    [Posts] Search error: {e}")

    return urls


def _is_linkedin_post_url(url: str) -> bool:
    """Check if a URL is a LinkedIn post or feed update."""
    lower = url.lower()
    return (
        "linkedin.com/posts/" in lower
        or "linkedin.com/feed/update/" in lower
    )


def _fetch_linkedin_post(url: str) -> Optional[dict]:
    """Fetch a LinkedIn post page and extract content.

    Returns dict with text, author, emails, or None on failure.
    """
    try:
        time.sleep(random.uniform(0.8, 1.8))
        resp = requests.get(url, headers=_get_headers(), timeout=15, allow_redirects=True)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        post_text = ""
        author = ""

        # Try meta description (usually contains the post text)
        meta_desc = soup.find("meta", {"name": "description"})
        if meta_desc and meta_desc.get("content"):
            post_text = meta_desc["content"]

        # Try og:description for more text
        og_desc = soup.find("meta", {"property": "og:description"})
        if og_desc and og_desc.get("content"):
            og_text = og_desc["content"]
            if len(og_text) > len(post_text):
                post_text = og_text

        # Try the actual post body elements
        for selector in [
            "div.feed-shared-update-v2__description",
            "div.attributed-text-segment-list__content",
            "div.update-components-text",
            "article",
        ]:
            el = soup.select_one(selector)
            if el:
                body_text = el.get_text(separator="\n", strip=True)
                if len(body_text) > len(post_text):
                    post_text = body_text

        # Get full page text for email extraction
        full_page_text = soup.get_text(separator=" ", strip=True)

        # Extract author
        og_title = soup.find("meta", {"property": "og:title"})
        if og_title and og_title.get("content"):
            author = _clean_author(og_title["content"])

        if not author:
            title_tag = soup.find("title")
            if title_tag:
                author = _clean_author(title_tag.get_text(strip=True))

        # Extract emails from post text AND full page
        emails = _extract_emails_from_text(post_text)
        if not emails:
            emails = _extract_emails_from_text(full_page_text)

        # Check mailto links
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if href.startswith("mailto:"):
                addr = href[7:].split("?")[0].strip()
                if _is_valid_email(addr) and addr not in emails:
                    emails.append(addr)

        if not post_text and not emails:
            return None

        return {
            "text": post_text,
            "author": author,
            "emails": emails,
            "url": resp.url,
        }

    except Exception as e:
        print(f"    [Posts] Error fetching {url[:60]}: {e}")
        return None


def _strip_hashtags(text: str) -> str:
    """Remove all #hashtags from text and clean up whitespace."""
    cleaned = re.sub(r"#\w+", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _clean_author(raw: str) -> str:
    """Clean up a LinkedIn og:title / page title into a person's name.

    Handles formats like:
    - '#hiring #fullstack | Paula Mateo on LinkedIn'
    - 'John Smith posted on the topic of...'
    - 'Jane Doe | LinkedIn'
    """
    # Remove " on LinkedIn", " posted on...", etc.
    for sep in [" on LinkedIn", " posted on", " | LinkedIn"]:
        raw = raw.split(sep)[0].strip()

    # If there's a pipe, the actual name is usually the last segment
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|")]
        # Pick the part that looks most like a name (no hashtags, 2-4 words)
        for part in reversed(parts):
            clean = _strip_hashtags(part)
            words = clean.split()
            if 1 <= len(words) <= 5 and not clean.startswith("#"):
                return clean
        # Fallback to last part
        return _strip_hashtags(parts[-1])

    return _strip_hashtags(raw)


def _guess_job_title_from_post(text: str) -> str:
    """Try to extract a job title from a LinkedIn post."""
    # Strip hashtags from text before matching
    clean_text = _strip_hashtags(text)

    patterns = [
        r"(?:hiring|looking for|seeking|need)\s+(?:a\s+|an\s+)?(.+?)(?:\.|,|!|\n|to join|with \d|who)",
        r"(?:role|position|opening)\s*[:\-]?\s*(.+?)(?:\.|,|!|\n)",
        r"(?:join us as|join .{0,20} as)\s+(?:a\s+|an\s+)?(.+?)(?:\.|,|!|\n)",
    ]
    for pattern in patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            title = re.sub(r"\s+", " ", title).strip()
            if 3 < len(title) < 80:
                return title

    # Try extracting from the original text with hashtags as keywords
    hashtags = re.findall(r"#(\w+)", text.lower())
    role_hashtags = [
        h for h in hashtags
        if any(kw in h for kw in [
            "developer", "engineer", "fullstack", "frontend", "backend",
            "react", "node", "python", "devops", "designer", "manager",
        ])
    ]
    if role_hashtags:
        return role_hashtags[0].replace("developer", " Developer").replace("engineer", " Engineer").title()

    return "Software Developer"


def _guess_company_from_post(text: str, author: str) -> str:
    """Try to extract a company name from the post or author."""
    clean_text = _strip_hashtags(text)

    patterns = [
        r"(?:at|@)\s+([A-Z][A-Za-z0-9\s&.]+?)(?:\.|,|!|\n|is hiring|are hiring)",
        r"([A-Z][A-Za-z0-9\s&.]+?)\s+is\s+(?:hiring|looking|seeking)",
    ]
    for pattern in patterns:
        match = re.search(pattern, clean_text)
        if match:
            company = match.group(1).strip()
            if 2 < len(company) < 60:
                return company

    if author:
        return author

    return "Unknown Company"


def scrape_linkedin_posts(
    search_config: SearchConfig,
    max_posts_per_query: int = 10,
) -> list[Job]:
    """Search for LinkedIn posts containing job openings with emails.

    Uses Brave Search to find LinkedIn posts, then fetches each post
    to extract job details and contact emails.

    Args:
        search_config: Search preferences.
        max_posts_per_query: Max posts to check per keyword.

    Returns:
        List of Job objects from LinkedIn posts.
    """
    jobs: list[Job] = []
    seen_urls: set[str] = set()

    for keyword in search_config.keywords:
        query = f'site:linkedin.com/posts "{keyword}" (hiring OR "looking for" OR "join") (email OR apply OR resume) remote'
        print(f"  [LinkedIn Posts] Searching: '{keyword}'...")

        post_urls = _search_brave(query, max_results=max_posts_per_query)

        if not post_urls:
            print(f"  [LinkedIn Posts] No posts found for '{keyword}'.")
            continue

        print(f"  [LinkedIn Posts] Found {len(post_urls)} posts, fetching...")

        for url in post_urls:
            clean_url = url.split("?")[0].rstrip("/")
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)

            post = _fetch_linkedin_post(url)
            if not post:
                continue

            emails = post["emails"]
            if not emails:
                continue

            title = _guess_job_title_from_post(post["text"])
            company = _guess_company_from_post(post["text"], post["author"])

            job = Job(
                title=title,
                company=company,
                location="Remote",
                description=post["text"],
                application_email=emails[0],
                source=JobSource.LINKEDIN,
                source_id=f"post:{clean_url.split('/')[-1][:40]}",
                source_url=post["url"],
            )
            jobs.append(job)
            print(f"    Found: {title[:50]} at {company[:30]} -> {emails[0]}")

    print(f"  [LinkedIn Posts] Total jobs from posts: {len(jobs)}")
    return jobs
