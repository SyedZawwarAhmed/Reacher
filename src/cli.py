"""CLI entry point for Reacher."""

from __future__ import annotations

from pathlib import Path

import click

from src.config import load_config
from src.db import (
    delete_draft,
    get_draft_by_id,
    get_drafts,
    get_pending_jobs,
    get_stats,
    init_db,
    update_draft_content,
    update_draft_status,
)


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=False),
    default="config.yaml",
    help="Path to config.yaml file.",
)
@click.pass_context
def cli(ctx, config_path: str):
    """Reacher - Automatically find and apply to jobs."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = Path(config_path)


@cli.command()
@click.option("--dry-run", is_flag=True, help="Generate emails but don't send them.")
@click.pass_context
def run(ctx, dry_run: bool):
    """Run the agent once: scrape jobs, generate emails, and send applications."""
    config = load_config(ctx.obj["config_path"])

    if dry_run:
        click.echo("=== DRY RUN MODE (no emails will be sent) ===\n")

    from src.agent import run_agent

    summary = run_agent(config, dry_run=dry_run)

    click.echo(f"\n--- Summary ---")
    click.echo(f"Jobs found:          {summary['jobs_found']}")
    click.echo(f"New jobs:            {summary['jobs_new']}")
    click.echo(f"  With email:        {summary['jobs_with_email']}")
    click.echo(f"  No email:          {summary.get('jobs_no_email', 0)}")
    click.echo(f"Applications sent:   {summary['applications_sent']}")
    click.echo(f"Applications failed: {summary['applications_failed']}")


@cli.command()
@click.option("--dry-run", is_flag=True, help="Generate emails but don't send them.")
@click.pass_context
def schedule(ctx, dry_run: bool):
    """Start the scheduler to run the agent automatically at a configured interval."""
    config = load_config(ctx.obj["config_path"])

    interval = config.schedule.interval_hours
    click.echo(f"Starting scheduler (every {interval} hours)...")
    click.echo("Press Ctrl+C to stop.\n")

    if dry_run:
        click.echo("=== DRY RUN MODE (no emails will be sent) ===\n")

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    from src.agent import run_agent

    scheduler = BlockingScheduler()

    def job_fn():
        click.echo(f"\n{'='*50}")
        click.echo(f"Scheduled run starting...")
        click.echo(f"{'='*50}")
        try:
            run_agent(config, dry_run=dry_run)
        except Exception as e:
            click.echo(f"Error during scheduled run: {e}")

    # Run immediately on start, then on schedule
    click.echo("Running initial pass now...")
    job_fn()

    scheduler.add_job(
        job_fn,
        trigger=IntervalTrigger(hours=interval),
        id="reacher",
        name="Reacher",
        replace_existing=True,
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        click.echo("\nScheduler stopped.")
        scheduler.shutdown()


@cli.command("send-pending")
@click.option("--dry-run", is_flag=True, help="Generate emails but don't send them.")
@click.pass_context
def send_pending(ctx, dry_run: bool):
    """Send applications for jobs already in the DB that haven't been applied to yet.

    This skips scraping and only processes existing jobs that have
    an email address but no application record.
    """
    config = load_config(ctx.obj["config_path"])

    if dry_run:
        click.echo("=== DRY RUN MODE (no emails will be sent) ===\n")

    from src.agent import send_pending as _send_pending

    summary = _send_pending(config, dry_run=dry_run)

    click.echo(f"\n--- Summary ---")
    click.echo(f"Pending jobs found:  {summary['pending_found']}")
    click.echo(f"Applications sent:   {summary['applications_sent']}")
    click.echo(f"Applications failed: {summary['applications_failed']}")


@cli.command("draft")
@click.pass_context
def generate_drafts_cmd(ctx):
    """Generate draft emails for pending jobs (does not send anything).

    Creates drafts for all jobs that have an email but haven't been
    drafted or applied to yet. Review with 'drafts', approve with 'approve'.
    """
    config = load_config(ctx.obj["config_path"])

    from src.agent import generate_drafts

    summary = generate_drafts(config)

    click.echo(f"\n--- Summary ---")
    click.echo(f"Pending jobs found:  {summary['pending_found']}")
    click.echo(f"Drafts created:      {summary['drafts_created']}")


@cli.command("drafts")
@click.option(
    "--status", "filter_status", default=None,
    type=click.Choice(["pending", "approved", "sent", "discarded"]),
    help="Filter drafts by status.",
)
@click.pass_context
def list_drafts_cmd(ctx, filter_status: str):
    """List all drafts with their status."""
    init_db()
    drafts = get_drafts(status=filter_status)

    if not drafts:
        label = f" with status '{filter_status}'" if filter_status else ""
        click.echo(f"No drafts found{label}.")
        return

    click.echo(f"\n=== Drafts ({len(drafts)}) ===\n")
    for d in drafts:
        status_icon = {
            "pending": "[?]",
            "approved": "[+]",
            "sent": "[v]",
            "discarded": "[x]",
        }.get(d["status"], "[?]")

        click.echo(
            f"  #{d['id']:<4} {status_icon} {d['job_title'][:35]:<35}  "
            f"{d['company'][:20]:<20}  -> {d['recipient_email']}"
        )
    click.echo(f"\nUse 'python -m src show-draft <id>' to view full email.")
    click.echo(f"Use 'python -m src approve <id>' to approve for sending.")


@cli.command("show-draft")
@click.argument("draft_id", type=int)
@click.pass_context
def show_draft_cmd(ctx, draft_id: int):
    """Show the full content of a draft email."""
    init_db()
    draft = get_draft_by_id(draft_id)

    if not draft:
        click.echo(f"Draft #{draft_id} not found.")
        return

    click.echo(f"\n{'='*60}")
    click.echo(f"Draft #{draft['id']}  |  Status: {draft['status']}")
    click.echo(f"{'='*60}")
    click.echo(f"Job:     {draft['job_title']} at {draft['company']}")
    click.echo(f"To:      {draft['recipient_email']}")
    click.echo(f"Source:  {draft['source_url']}")
    click.echo(f"Created: {draft['created_at'][:16]}")
    click.echo(f"{'='*60}")
    click.echo(f"Subject: {draft['subject']}")
    click.echo(f"{'-'*60}")
    click.echo(draft["body"])
    click.echo(f"{'='*60}")


@cli.command("approve")
@click.argument("draft_ids", type=int, nargs=-1, required=True)
@click.pass_context
def approve_drafts_cmd(ctx, draft_ids: tuple):
    """Approve one or more drafts for sending.

    Pass draft IDs as arguments: approve 1 2 3
    Use 'approve-all' to approve all pending drafts.
    """
    init_db()
    for draft_id in draft_ids:
        draft = get_draft_by_id(draft_id)
        if not draft:
            click.echo(f"  Draft #{draft_id}: not found, skipping.")
            continue
        if draft["status"] == "sent":
            click.echo(f"  Draft #{draft_id}: already sent, skipping.")
            continue
        update_draft_status(draft_id, "approved")
        click.echo(f"  Draft #{draft_id}: approved ({draft['job_title']} at {draft['company']})")

    click.echo(f"\nSend approved drafts with: python -m src send-drafts")


@cli.command("approve-all")
@click.pass_context
def approve_all_drafts_cmd(ctx):
    """Approve all pending drafts for sending."""
    init_db()
    pending = get_drafts(status="pending")
    if not pending:
        click.echo("No pending drafts to approve.")
        return

    for d in pending:
        update_draft_status(d["id"], "approved")

    click.echo(f"Approved {len(pending)} drafts.")
    click.echo(f"Send them with: python -m src send-drafts")


@cli.command("discard")
@click.argument("draft_ids", type=int, nargs=-1, required=True)
@click.pass_context
def discard_drafts_cmd(ctx, draft_ids: tuple):
    """Discard one or more drafts (won't be sent)."""
    init_db()
    for draft_id in draft_ids:
        draft = get_draft_by_id(draft_id)
        if not draft:
            click.echo(f"  Draft #{draft_id}: not found, skipping.")
            continue
        if draft["status"] == "sent":
            click.echo(f"  Draft #{draft_id}: already sent, can't discard.")
            continue
        update_draft_status(draft_id, "discarded")
        click.echo(f"  Draft #{draft_id}: discarded ({draft['job_title']} at {draft['company']})")


@cli.command("edit-draft")
@click.argument("draft_id", type=int)
@click.option("--subject", default=None, help="New subject line.")
@click.option("--body-file", default=None, type=click.Path(exists=True), help="Path to a text file with the new body.")
@click.pass_context
def edit_draft_cmd(ctx, draft_id: int, subject: str, body_file: str):
    """Edit a draft's subject or body.

    To edit the body, write the new body to a text file and pass it with --body-file.
    """
    init_db()
    draft = get_draft_by_id(draft_id)
    if not draft:
        click.echo(f"Draft #{draft_id} not found.")
        return
    if draft["status"] == "sent":
        click.echo(f"Draft #{draft_id} has already been sent, can't edit.")
        return

    new_subject = subject or draft["subject"]
    new_body = draft["body"]
    if body_file:
        new_body = Path(body_file).read_text().strip()

    if new_subject == draft["subject"] and new_body == draft["body"]:
        click.echo("No changes provided.")
        return

    update_draft_content(draft_id, new_subject, new_body)
    click.echo(f"Draft #{draft_id} updated.")
    if subject:
        click.echo(f"  New subject: {new_subject}")
    if body_file:
        click.echo(f"  Body updated from: {body_file}")


@cli.command("send-drafts")
@click.option("--all", "send_all", is_flag=True, help="Send all pending + approved drafts (not just approved).")
@click.option("--dry-run", is_flag=True, help="Preview without sending.")
@click.pass_context
def send_drafts_cmd(ctx, send_all: bool, dry_run: bool):
    """Send approved drafts. Use --all to include pending drafts too."""
    config = load_config(ctx.obj["config_path"])

    if dry_run:
        click.echo("=== DRY RUN MODE (no emails will be sent) ===\n")

    from src.agent import send_approved_drafts

    summary = send_approved_drafts(config, send_all=send_all, dry_run=dry_run)

    click.echo(f"\n--- Summary ---")
    click.echo(f"Drafts found:        {summary['drafts_found']}")
    click.echo(f"Applications sent:   {summary['applications_sent']}")
    click.echo(f"Applications failed: {summary['applications_failed']}")


@cli.command()
@click.pass_context
def status(ctx):
    """Show application stats and recent activity."""
    init_db()
    stats = get_stats()

    pending = get_pending_jobs()
    drafts_pending = get_drafts(status="pending")
    drafts_approved = get_drafts(status="approved")

    click.echo("=== Reacher Status ===\n")
    click.echo(f"Total jobs discovered:    {stats['total_jobs_discovered']}")
    click.echo(f"Total applications sent:  {stats['total_applications_sent']}")
    click.echo(f"Applications today:       {stats['applications_today']}")
    click.echo(f"Pending (no draft/app):   {len(pending)}")
    click.echo(f"Drafts pending review:    {len(drafts_pending)}")
    click.echo(f"Drafts approved to send:  {len(drafts_approved)}")

    recent = stats["recent_applications"]
    if recent:
        click.echo(f"\n--- Recent Applications (last {len(recent)}) ---")
        for app in recent:
            click.echo(
                f"  {app['sent_at'][:16]}  |  {app['job_title'][:30]:<30}  |  "
                f"{app['company'][:20]:<20}  |  {app['recipient_email']}"
            )
    else:
        click.echo("\nNo applications sent yet.")


def main():
    cli()


if __name__ == "__main__":
    main()
