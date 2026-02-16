"""Scrape LinkedIn's public job search pages for job listings."""

from __future__ import annotations

import re
import time
import random
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from src.email_finder import find_application_email
from src.models import Job, JobSource, SearchConfig

BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]

EXPERIENCE_LEVEL_MAP = {
    "junior": "2",   # Entry level
    "mid": "3",      # Associate
    "senior": "4",   # Mid-Senior level
}

# Location keywords that indicate a job is remote-friendly
REMOTE_KEYWORDS = {
    "remote", "worldwide", "anywhere", "work from home", "wfh",
    "distributed", "global", "fully remote", "100% remote",
    "work from anywhere", "location flexible", "remote-friendly",
}

# Locations that indicate the job is NOT available remotely
# (when combined with absence of remote keywords)
LOCATION_LOCKED_INDICATORS = {
    "on-site", "onsite", "on site", "in-office", "in office",
    "hybrid", "office-based",
}


def _get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }


def _build_search_url(keyword: str, location: str, experience_level: str, start: int = 0) -> str:
    """Build LinkedIn job search API URL."""
    params = {
        "keywords": keyword,
        "location": location,
        "start": start,
        "f_TPR": "r604800",  # Past week
        "f_WT": "2",         # Remote work type filter
    }
    exp_code = EXPERIENCE_LEVEL_MAP.get(experience_level)
    if exp_code:
        params["f_E"] = exp_code

    return f"{BASE_URL}?{urlencode(params)}"


def is_remote_friendly(location: str, description: str = "") -> bool:
    """Check if a job is likely remote-friendly based on location and description.

    Returns True if the job appears to be remote or location-flexible.
    """
    combined = f"{location} {description}".lower()

    # Check for explicit remote indicators
    for keyword in REMOTE_KEYWORDS:
        if keyword in combined:
            return True

    # Accept jobs in Pakistan/Karachi
    location_lower = location.lower()
    if any(loc in location_lower for loc in ["pakistan", "karachi", "sindh"]):
        return True

    return False


def _parse_job_card(card) -> Optional[Job]:
    """Parse a single LinkedIn job card into a Job model."""
    try:
        title_el = card.find("h3", class_="base-search-card__title")
        company_el = card.find("h4", class_="base-search-card__subtitle")
        location_el = card.find("span", class_="job-search-card__location")
        link_el = card.find("a", class_="base-card__full-link")
        time_el = card.find("time")

        if not title_el or not company_el:
            return None

        title = title_el.get_text(strip=True)
        company = company_el.get_text(strip=True)
        location = location_el.get_text(strip=True) if location_el else ""
        job_url = link_el["href"].split("?")[0] if link_el and link_el.get("href") else ""
        posted_at = None
        if time_el and time_el.get("datetime"):
            try:
                posted_at = datetime.fromisoformat(time_el["datetime"])
            except (ValueError, TypeError):
                pass

        source_id = ""
        if job_url:
            parts = job_url.rstrip("/").split("-")
            if parts:
                candidate = parts[-1]
                if candidate.isdigit():
                    source_id = candidate

        return Job(
            title=title,
            company=company,
            location=location,
            description="",
            application_email="",
            source=JobSource.LINKEDIN,
            source_id=source_id,
            source_url=job_url,
            posted_at=posted_at,
        )
    except Exception:
        return None


def _fetch_job_page(job_url: str) -> tuple[str, Optional[BeautifulSoup]]:
    """Fetch full job page and return (description_text, soup).

    Returns both the extracted description text and the full page soup
    so the email finder can use it.
    """
    if not job_url:
        return "", None
    try:
        time.sleep(random.uniform(1.0, 2.5))
        resp = requests.get(job_url, headers=_get_headers(), timeout=15)
        if resp.status_code != 200:
            return "", None
        soup = BeautifulSoup(resp.text, "html.parser")
        desc_div = soup.find("div", class_="show-more-less-html__markup")
        description = ""
        if desc_div:
            description = desc_div.get_text(separator="\n", strip=True)
        return description, soup
    except Exception:
        return "", None


def scrape_linkedin_jobs(
    search_config: SearchConfig,
    max_results_per_query: int = 25,
) -> list[Job]:
    """Scrape LinkedIn public job search for matching listings.

    Args:
        search_config: Search preferences (keywords, locations, experience).
        max_results_per_query: Max job cards to fetch per keyword+location combo.

    Returns:
        List of Job objects with descriptions and emails where found.
    """
    jobs: list[Job] = []
    seen_keys: set[str] = set()
    skipped_location = 0

    for keyword in search_config.keywords:
        for location in search_config.locations:
            url = _build_search_url(
                keyword, location, search_config.experience_level.value
            )
            print(f"  [LinkedIn] Searching: '{keyword}' in '{location}'...")

            try:
                time.sleep(random.uniform(1.5, 3.0))
                resp = requests.get(url, headers=_get_headers(), timeout=15)
                if resp.status_code != 200:
                    print(f"  [LinkedIn] Got status {resp.status_code}, skipping.")
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("div", class_="base-card")

                for card in cards[:max_results_per_query]:
                    job = _parse_job_card(card)
                    if not job:
                        continue
                    if job.unique_key in seen_keys:
                        continue
                    seen_keys.add(job.unique_key)

                    # Fetch the full job page
                    description, job_soup = _fetch_job_page(job.source_url)
                    if description:
                        job.description = description

                    # Location filter: skip non-remote jobs
                    if not is_remote_friendly(job.location, job.description):
                        skipped_location += 1
                        continue

                    # Multi-strategy email discovery
                    email = find_application_email(
                        job_description=job.description,
                        job_url=job.source_url,
                        company_name=job.company,
                        job_page_soup=job_soup,
                    )
                    if email:
                        job.application_email = email

                    jobs.append(job)

                print(f"  [LinkedIn] Found {len(cards)} cards for this query.")

            except requests.RequestException as e:
                print(f"  [LinkedIn] Request error: {e}")
                continue

    print(f"  [LinkedIn] Total unique jobs: {len(jobs)} (skipped {skipped_location} non-remote)")
    print(f"  [LinkedIn] Jobs with emails: {sum(1 for j in jobs if j.application_email)}")
    return jobs
