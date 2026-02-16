"""Search X/Twitter for job postings using the v2 API via tweepy."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import tweepy

from src.models import Job, JobSource, SearchConfig

EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

URL_PATTERN = re.compile(r"https?://\S+")


def _build_queries(search_config: SearchConfig) -> list[str]:
    """Build Twitter search queries from config.

    Combines job keywords with hiring-related terms and requires
    an email address to be present (so we can actually apply).
    """
    queries = []
    for keyword in search_config.keywords:
        query = f'("{keyword}") (hiring OR "job opening" OR "we are looking" OR "apply") (@ OR "email")'
        if len(query) <= 512:
            queries.append(query)
        else:
            queries.append(
                f'("{keyword}") (hiring OR "job opening") (@ OR "email")'
            )
    return queries


def _extract_emails(text: str) -> list[str]:
    """Extract email addresses from tweet text, filtering noise."""
    emails = EMAIL_PATTERN.findall(text)
    filtered = []
    for email in emails:
        lower = email.lower()
        if any(
            skip in lower
            for skip in [
                "example.com", "test.com", "twitter.com", "x.com",
                "noreply", "no-reply", "t.co",
            ]
        ):
            continue
        filtered.append(email)
    return filtered


def _parse_tweet_to_job(tweet, author_map: dict) -> Optional[Job]:
    """Convert a tweet into a Job if it contains an email address."""
    text = tweet.text or ""

    emails = _extract_emails(text)
    if not emails:
        return None

    author_id = tweet.author_id
    author_name = ""
    if author_map and author_id in author_map:
        author_name = author_map[author_id]

    title_hint = _guess_title(text)
    company = author_name or "Unknown (via X)"

    tweet_url = f"https://x.com/i/status/{tweet.id}"

    posted_at = None
    if tweet.created_at:
        posted_at = tweet.created_at

    return Job(
        title=title_hint,
        company=company,
        location="",
        description=text,
        application_email=emails[0],
        source=JobSource.TWITTER,
        source_id=str(tweet.id),
        source_url=tweet_url,
        posted_at=posted_at,
    )


def _guess_title(text: str) -> str:
    """Attempt to extract a job title from tweet text."""
    patterns = [
        r"(?:hiring|looking for|seeking)\s+(?:a\s+)?(.+?)(?:\.|,|!|\n|to join|with)",
        r"(?:role|position|opening)\s*[:\-]?\s*(.+?)(?:\.|,|!|\n)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            if 3 < len(title) < 80:
                return title

    return "Job Opportunity (via X)"


def scrape_twitter_jobs(
    search_config: SearchConfig,
    bearer_token: str,
    max_results_per_query: int = 20,
) -> list[Job]:
    """Search X/Twitter for recent job-related tweets with email addresses.

    Args:
        search_config: Search preferences.
        bearer_token: X API v2 bearer token.
        max_results_per_query: Max tweets per search query.

    Returns:
        List of Job objects extracted from tweets.
    """
    if not bearer_token or "YOUR_" in bearer_token:
        print("  [Twitter] Skipping: no valid bearer token configured.")
        return []

    try:
        client = tweepy.Client(bearer_token=bearer_token, wait_on_rate_limit=True)
    except Exception as e:
        print(f"  [Twitter] Failed to initialize client: {e}")
        return []

    queries = _build_queries(search_config)
    jobs: list[Job] = []
    seen_ids: set[str] = set()

    for query in queries:
        print(f"  [Twitter] Searching: {query[:60]}...")
        try:
            response = client.search_recent_tweets(
                query=query,
                max_results=min(max_results_per_query, 100),
                tweet_fields=["created_at", "author_id", "text"],
                user_fields=["name", "username"],
                expansions=["author_id"],
            )

            author_map = {}
            if response.includes and "users" in response.includes:
                for user in response.includes["users"]:
                    author_map[user.id] = f"{user.name} (@{user.username})"

            if not response.data:
                print(f"  [Twitter] No results for this query.")
                continue

            for tweet in response.data:
                if str(tweet.id) in seen_ids:
                    continue
                seen_ids.add(str(tweet.id))

                job = _parse_tweet_to_job(tweet, author_map)
                if job:
                    jobs.append(job)

            print(f"  [Twitter] Found {len(response.data)} tweets, {len(jobs)} with emails so far.")

        except tweepy.TooManyRequests:
            print("  [Twitter] Rate limited. Will retry on next run.")
            break
        except tweepy.TwitterServerError as e:
            print(f"  [Twitter] Server error: {e}")
            continue
        except Exception as e:
            print(f"  [Twitter] Error: {e}")
            continue

    print(f"  [Twitter] Total jobs with emails: {len(jobs)}")
    return jobs
