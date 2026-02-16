"""Core agent orchestrator: scrape, filter, generate emails, send."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import re

from src.config import get_resume_text, load_config
from src.db import (
    get_applications_today,
    get_contacted_companies,
    get_draft_by_id,
    get_drafts,
    get_pending_jobs,
    get_sent_companies,
    init_db,
    is_job_seen,
    log_application,
    mark_job_seen,
    save_draft,
    update_draft_status,
)
from src.emailer import send_application_email
from src.llm import generate_application_email
from src.models import AppConfig, Application, Job, JobSource
from src.scrapers.linkedin import scrape_linkedin_jobs
from src.scrapers.linkedin_posts import scrape_linkedin_posts
from src.scrapers.twitter import scrape_twitter_jobs


def _job_priority_score(job: Job) -> int:
    """Score a job by role preference (higher = better).

    Priority: JS/TS roles > full stack > frontend > backend > other.
    """
    title = job.title.lower()
    desc = job.description.lower()
    combined = f"{title} {desc}"

    score = 0

    # Language preference: JS/TS strongly preferred
    if any(kw in combined for kw in ["javascript", "typescript", "js ", "ts ", "react", "node.js", "nodejs", "next.js", "nextjs", "nestjs"]):
        score += 100

    # Role type preference: full stack > frontend > backend
    if any(kw in title for kw in ["full stack", "fullstack", "full-stack"]):
        score += 50
    elif any(kw in title for kw in ["frontend", "front-end", "front end"]):
        score += 30
    elif any(kw in title for kw in ["backend", "back-end", "back end"]):
        score += 20

    # Bonus for React Native (your specialty)
    if "react native" in combined:
        score += 40

    return score


def _pick_best_per_company(jobs: list, contacted_companies: set) -> list:
    """From a list of jobs (or DB rows), pick the best one per company.

    Skips companies already contacted. When multiple jobs exist for the
    same company, picks the one with the highest priority score.

    Args:
        jobs: List of Job objects or dicts with 'title', 'company', 'description'.
        contacted_companies: Set of lowercase company names already contacted.

    Returns:
        Filtered list with at most one entry per company.
    """
    by_company: dict = {}

    for job in jobs:
        if isinstance(job, dict):
            company_key = job["company"].strip().lower()
            title = job.get("title", "")
            desc = job.get("description", "")
            temp_job = Job(
                title=title, company=job["company"],
                description=desc, source=JobSource.LINKEDIN,
            )
            score = _job_priority_score(temp_job)
        else:
            company_key = job.company.strip().lower()
            score = _job_priority_score(job)

        if company_key in contacted_companies:
            continue

        if company_key not in by_company or score > by_company[company_key][1]:
            by_company[company_key] = (job, score)

    return [entry[0] for entry in by_company.values()]


def run_agent(config: AppConfig, dry_run: bool = False) -> dict:
    """Execute a single agent run: scrape -> filter -> apply.

    Args:
        config: Validated application configuration.
        dry_run: If True, generate emails but don't send them.

    Returns:
        Summary dict with counts and details.
    """
    init_db()

    summary = {
        "started_at": datetime.utcnow().isoformat(),
        "jobs_found": 0,
        "jobs_new": 0,
        "jobs_with_email": 0,
        "jobs_no_email": 0,
        "applications_sent": 0,
        "applications_failed": 0,
        "dry_run": dry_run,
    }

    apps_today = get_applications_today()
    remaining_today = config.limits.max_applications_per_day - apps_today
    if remaining_today <= 0:
        print(
            f"\nDaily limit reached ({config.limits.max_applications_per_day} applications). "
            f"Skipping this run."
        )
        return summary

    max_this_run = min(config.limits.max_applications_per_run, remaining_today)

    # --- Step 1: Scrape jobs from all sources ---
    # LinkedIn posts are searched first (higher email hit rate)
    print("\n=== Scraping job listings ===")
    all_jobs: list[Job] = []

    print("\n[1/3] LinkedIn Posts (hiring posts with emails)...")
    post_jobs = scrape_linkedin_posts(config.search)
    all_jobs.extend(post_jobs)

    print("\n[2/3] LinkedIn Jobs...")
    linkedin_jobs = scrape_linkedin_jobs(config.search)
    all_jobs.extend(linkedin_jobs)

    print("\n[3/3] X / Twitter...")
    twitter_jobs = scrape_twitter_jobs(config.search, config.twitter.bearer_token)
    all_jobs.extend(twitter_jobs)

    summary["jobs_found"] = len(all_jobs)
    print(f"\nTotal jobs found: {len(all_jobs)}")

    # --- Step 2: Filter new jobs ---
    print("\n=== Filtering jobs ===")
    new_with_email: list[Job] = []
    new_no_email: list[Job] = []

    for job in all_jobs:
        if is_job_seen(job):
            continue
        mark_job_seen(job)
        if job.application_email:
            new_with_email.append(job)
        else:
            new_no_email.append(job)

    total_new = len(new_with_email) + len(new_no_email)
    summary["jobs_new"] = total_new
    summary["jobs_with_email"] = len(new_with_email)
    summary["jobs_no_email"] = len(new_no_email)

    print(f"New jobs total:          {total_new}")
    print(f"  With email found:      {len(new_with_email)}")
    print(f"  No email (skipped):    {len(new_no_email)}")

    if new_no_email:
        print(f"\n  Companies where no email was found:")
        for job in new_no_email[:10]:
            print(f"    - {job.title} at {job.company} ({job.location})")
        if len(new_no_email) > 10:
            print(f"    ... and {len(new_no_email) - 10} more")

    if not new_with_email:
        print("\nNo new jobs with emails to apply to this run.")
        return summary

    # --- Step 2b: One-per-company dedup, pick best role ---
    contacted = get_contacted_companies()
    best_jobs = _pick_best_per_company(new_with_email, contacted)
    skipped_dupes = len(new_with_email) - len(best_jobs)
    if skipped_dupes:
        print(f"  Skipped {skipped_dupes} duplicate-company / already-contacted jobs")
    print(f"  Jobs to apply to:      {len(best_jobs)}")

    if not best_jobs:
        print("\nNo new companies to apply to this run.")
        return summary

    # --- Step 3: Generate and send application emails ---
    print("\n=== Generating and sending applications ===")
    resume_text = get_resume_text(config)
    resume_path = Path(config.profile.resume_pdf)
    if not resume_path.exists():
        resume_path = None

    applied_count = 0
    for job in best_jobs:
        if applied_count >= max_this_run:
            print(f"\nReached limit for this run ({max_this_run}). Stopping.")
            break

        print(f"\n--- Applying to: {job.title} at {job.company} ---")
        print(f"    Email: {job.application_email}")
        print(f"    Location: {job.location}")
        print(f"    Source: {job.source.value} | {job.source_url}")

        subject, body = generate_application_email(job, resume_text, config)

        if dry_run:
            print(f"  [DRY RUN] Would send to {job.application_email}")
            print(f"  Subject: {subject}")
            print(f"  Body preview: {body[:200]}...")
            applied_count += 1
            summary["applications_sent"] += 1
            continue

        success = send_application_email(
            to_email=job.application_email,
            subject=subject,
            body=body,
            config=config,
            resume_path=resume_path,
        )

        if success:
            application = Application(
                job_title=job.title,
                company=job.company,
                recipient_email=job.application_email,
                subject=subject,
                body=body,
                source=job.source,
                source_url=job.source_url,
            )
            log_application(application)
            applied_count += 1
            summary["applications_sent"] += 1
        else:
            summary["applications_failed"] += 1

    print(f"\n=== Run complete ===")
    print(f"Applications sent: {summary['applications_sent']}")
    print(f"Applications failed: {summary['applications_failed']}")

    return summary


def send_pending(config: AppConfig, dry_run: bool = False) -> dict:
    """Send applications for jobs already in the DB that haven't been applied to.

    This does NOT scrape for new jobs. It only processes existing jobs
    in the database that have an email but no application record.

    Args:
        config: Validated application configuration.
        dry_run: If True, generate emails but don't send them.

    Returns:
        Summary dict with counts.
    """
    init_db()

    summary = {
        "pending_found": 0,
        "applications_sent": 0,
        "applications_failed": 0,
        "dry_run": dry_run,
    }

    apps_today = get_applications_today()
    remaining_today = config.limits.max_applications_per_day - apps_today
    if remaining_today <= 0:
        print(
            f"\nDaily limit reached ({config.limits.max_applications_per_day} applications). "
            f"Skipping."
        )
        return summary

    max_this_run = min(config.limits.max_applications_per_run, remaining_today)

    pending = get_pending_jobs()
    summary["pending_found"] = len(pending)

    if not pending:
        print("\nNo pending jobs to apply to. All jobs with emails have been applied to.")
        return summary

    # One-per-company dedup
    contacted = get_contacted_companies()
    best_rows = _pick_best_per_company(pending, contacted)
    skipped = len(pending) - len(best_rows)
    if skipped:
        print(f"  Skipped {skipped} duplicate-company / already-contacted jobs")

    if not best_rows:
        print("\nNo new companies to apply to.")
        return summary

    print(f"\n=== Sending to {len(best_rows)} companies ===")

    resume_text = get_resume_text(config)
    resume_path = Path(config.profile.resume_pdf)
    if not resume_path.exists():
        resume_path = None

    applied_count = 0
    for row in best_rows:
        if applied_count >= max_this_run:
            print(f"\nReached limit for this run ({max_this_run}). Stopping.")
            break

        job = Job(
            title=row["title"],
            company=row["company"],
            location=row["location"],
            description=row["description"],
            application_email=row["application_email"],
            source=JobSource(row["source"]),
            source_id=row["source_id"],
            source_url=row["source_url"],
        )

        print(f"\n--- Applying to: {job.title} at {job.company} ---")
        print(f"    Email: {job.application_email}")
        print(f"    Location: {job.location}")
        print(f"    Source: {job.source.value} | {job.source_url}")

        subject, body = generate_application_email(job, resume_text, config)

        if dry_run:
            print(f"  [DRY RUN] Would send to {job.application_email}")
            print(f"  Subject: {subject}")
            print(f"  Body preview: {body[:200]}...")
            applied_count += 1
            summary["applications_sent"] += 1
            continue

        success = send_application_email(
            to_email=job.application_email,
            subject=subject,
            body=body,
            config=config,
            resume_path=resume_path,
        )

        if success:
            application = Application(
                job_title=job.title,
                company=job.company,
                recipient_email=job.application_email,
                subject=subject,
                body=body,
                source=job.source,
                source_url=job.source_url,
            )
            log_application(application)
            applied_count += 1
            summary["applications_sent"] += 1
        else:
            summary["applications_failed"] += 1

    print(f"\n=== Send pending complete ===")
    print(f"Pending jobs found:  {summary['pending_found']}")
    print(f"Applications sent:   {summary['applications_sent']}")
    print(f"Applications failed: {summary['applications_failed']}")

    return summary


def generate_drafts(config: AppConfig) -> dict:
    """Generate draft emails for pending jobs (jobs with emails, not yet drafted or applied).

    Does NOT send anything. Saves drafts to DB for review.

    Args:
        config: Validated application configuration.

    Returns:
        Summary dict with counts.
    """
    init_db()

    summary = {
        "pending_found": 0,
        "drafts_created": 0,
    }

    pending = get_pending_jobs()
    summary["pending_found"] = len(pending)

    if not pending:
        print("\nNo pending jobs to draft. All jobs with emails have been drafted or applied to.")
        return summary

    # One-per-company dedup, pick best role
    contacted = get_contacted_companies()
    best_rows = _pick_best_per_company(pending, contacted)
    skipped = len(pending) - len(best_rows)
    if skipped:
        print(f"  Skipped {skipped} duplicate-company / already-contacted jobs")

    if not best_rows:
        print("\nNo new companies to draft for.")
        return summary

    print(f"\n=== Generating drafts for {len(best_rows)} companies ===")

    resume_text = get_resume_text(config)

    for row in best_rows:
        job = Job(
            title=row["title"],
            company=row["company"],
            location=row["location"],
            description=row["description"],
            application_email=row["application_email"],
            source=JobSource(row["source"]),
            source_id=row["source_id"],
            source_url=row["source_url"],
        )

        print(f"\n  Drafting: {job.title} at {job.company} -> {job.application_email}")

        subject, body = generate_application_email(job, resume_text, config)

        draft_id = save_draft(
            job_unique_key=row["unique_key"],
            job_title=job.title,
            company=job.company,
            location=job.location,
            recipient_email=job.application_email,
            subject=subject,
            body=body,
            source=job.source.value,
            source_url=job.source_url,
        )

        print(f"  Saved as draft #{draft_id}")
        summary["drafts_created"] += 1

    print(f"\n=== Drafts generated: {summary['drafts_created']} ===")
    print(f"Use 'python -m src drafts' to review them.")
    print(f"Use 'python -m src approve <id>' to approve, or 'python -m src send-drafts' to send approved drafts.")

    return summary


def send_approved_drafts(config: AppConfig, send_all: bool = False, dry_run: bool = False) -> dict:
    """Send drafts that have been approved (or all pending drafts if send_all=True).

    Args:
        config: Validated application configuration.
        send_all: If True, send all pending+approved drafts. If False, only approved.
        dry_run: If True, preview without sending.

    Returns:
        Summary dict with counts.
    """
    init_db()

    summary = {
        "drafts_found": 0,
        "applications_sent": 0,
        "applications_failed": 0,
        "dry_run": dry_run,
    }

    apps_today = get_applications_today()
    remaining_today = config.limits.max_applications_per_day - apps_today
    if remaining_today <= 0:
        print(
            f"\nDaily limit reached ({config.limits.max_applications_per_day} applications). "
            f"Skipping."
        )
        return summary

    max_this_run = min(config.limits.max_applications_per_run, remaining_today)

    drafts = get_drafts(status="approved")
    if send_all:
        drafts += get_drafts(status="pending")

    summary["drafts_found"] = len(drafts)

    if not drafts:
        status_label = "approved or pending" if send_all else "approved"
        print(f"\nNo {status_label} drafts to send.")
        if not send_all:
            pending_count = len(get_drafts(status="pending"))
            if pending_count:
                print(f"  You have {pending_count} pending drafts. Use --all to send them,")
                print(f"  or approve them first with: python -m src approve <id>")
        return summary

    # Dedup: only one draft per company, skip companies already sent to
    seen_companies: set = set()
    already_sent = get_sent_companies()
    unique_drafts = []
    for draft in drafts:
        company_key = draft["company"].strip().lower()
        if company_key in seen_companies or company_key in already_sent:
            continue
        seen_companies.add(company_key)
        unique_drafts.append(draft)

    if len(unique_drafts) < len(drafts):
        print(f"  Deduped to {len(unique_drafts)} drafts (one per company)")

    summary["drafts_found"] = len(unique_drafts)

    if not unique_drafts:
        print("\nAll draft companies have already been contacted.")
        return summary

    print(f"\n=== Sending {len(unique_drafts)} drafts ===")

    resume_path = Path(config.profile.resume_pdf)
    if not resume_path.exists():
        resume_path = None

    sent_count = 0
    for draft in unique_drafts:
        if sent_count >= max_this_run:
            print(f"\nReached limit for this run ({max_this_run}). Stopping.")
            break

        print(f"\n--- Draft #{draft['id']}: {draft['job_title']} at {draft['company']} ---")
        print(f"    To: {draft['recipient_email']}")
        print(f"    Subject: {draft['subject']}")

        if dry_run:
            print(f"  [DRY RUN] Would send to {draft['recipient_email']}")
            print(f"  Body preview: {draft['body'][:200]}...")
            sent_count += 1
            summary["applications_sent"] += 1
            continue

        success = send_application_email(
            to_email=draft["recipient_email"],
            subject=draft["subject"],
            body=draft["body"],
            config=config,
            resume_path=resume_path,
        )

        if success:
            application = Application(
                job_title=draft["job_title"],
                company=draft["company"],
                recipient_email=draft["recipient_email"],
                subject=draft["subject"],
                body=draft["body"],
                source=JobSource(draft["source"]),
                source_url=draft["source_url"],
            )
            log_application(application)
            update_draft_status(draft["id"], "sent")
            sent_count += 1
            summary["applications_sent"] += 1
        else:
            summary["applications_failed"] += 1

    print(f"\n=== Send drafts complete ===")
    print(f"Drafts processed:    {summary['drafts_found']}")
    print(f"Applications sent:   {summary['applications_sent']}")
    print(f"Applications failed: {summary['applications_failed']}")

    return summary
