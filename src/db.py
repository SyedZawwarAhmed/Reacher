"""SQLite database layer for tracking jobs and applications."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from src.models import Application, Job

DB_PATH = Path("jobs.db")


def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Create tables if they don't exist."""
    conn = _get_connection(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS seen_jobs (
            unique_key TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT DEFAULT '',
            description TEXT DEFAULT '',
            application_email TEXT DEFAULT '',
            source TEXT NOT NULL,
            source_id TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            discovered_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_title TEXT NOT NULL,
            company TEXT NOT NULL,
            recipient_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            source TEXT NOT NULL,
            source_url TEXT DEFAULT '',
            sent_at TEXT NOT NULL,
            status TEXT DEFAULT 'sent'
        );

        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_unique_key TEXT NOT NULL,
            job_title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT DEFAULT '',
            recipient_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            source TEXT NOT NULL,
            source_url TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
        );
        """
    )
    conn.commit()
    conn.close()


def is_job_seen(job: Job, db_path: Optional[Path] = None) -> bool:
    """Check if we've already seen this job."""
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT 1 FROM seen_jobs WHERE unique_key = ?", (job.unique_key,)
    ).fetchone()
    conn.close()
    return row is not None


def mark_job_seen(job: Job, db_path: Optional[Path] = None) -> None:
    """Record a job as seen."""
    conn = _get_connection(db_path)
    conn.execute(
        """
        INSERT OR IGNORE INTO seen_jobs
            (unique_key, title, company, location, description,
             application_email, source, source_id, source_url, discovered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job.unique_key,
            job.title,
            job.company,
            job.location,
            job.description,
            job.application_email,
            job.source.value,
            job.source_id,
            job.source_url,
            job.discovered_at.isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def log_application(app: Application, db_path: Optional[Path] = None) -> None:
    """Log a sent application."""
    conn = _get_connection(db_path)
    conn.execute(
        """
        INSERT INTO applications
            (job_title, company, recipient_email, subject, body,
             source, source_url, sent_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app.job_title,
            app.company,
            app.recipient_email,
            app.subject,
            app.body,
            app.source.value,
            app.source_url,
            app.sent_at.isoformat(),
            app.status,
        ),
    )
    conn.commit()
    conn.close()


def get_applications_today(db_path: Optional[Path] = None) -> int:
    """Count how many applications were sent today."""
    conn = _get_connection(db_path)
    today_start = datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM applications WHERE sent_at >= ?",
        (today_start.isoformat(),),
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def get_stats(db_path: Optional[Path] = None) -> dict:
    """Get overall stats for the status command."""
    conn = _get_connection(db_path)

    total_jobs = conn.execute("SELECT COUNT(*) as cnt FROM seen_jobs").fetchone()["cnt"]
    total_apps = conn.execute("SELECT COUNT(*) as cnt FROM applications").fetchone()[
        "cnt"
    ]
    today_apps = get_applications_today(db_path)

    recent = conn.execute(
        """
        SELECT job_title, company, recipient_email, sent_at, status
        FROM applications ORDER BY sent_at DESC LIMIT 10
        """
    ).fetchall()

    conn.close()

    return {
        "total_jobs_discovered": total_jobs,
        "total_applications_sent": total_apps,
        "applications_today": today_apps,
        "recent_applications": [dict(r) for r in recent],
    }


def get_recent_applications(
    limit: int = 10, db_path: Optional[Path] = None
) -> list[dict]:
    """Get the most recent applications."""
    conn = _get_connection(db_path)
    rows = conn.execute(
        """
        SELECT job_title, company, recipient_email, sent_at, status
        FROM applications ORDER BY sent_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_contacted_companies(db_path: Optional[Path] = None) -> set:
    """Get all company names we've already sent an application or created a draft for."""
    conn = _get_connection(db_path)
    app_companies = conn.execute(
        "SELECT DISTINCT LOWER(company) FROM applications"
    ).fetchall()
    draft_companies = conn.execute(
        "SELECT DISTINCT LOWER(company) FROM drafts WHERE status != 'discarded'"
    ).fetchall()
    conn.close()
    companies = set()
    for row in app_companies:
        companies.add(row[0])
    for row in draft_companies:
        companies.add(row[0])
    return companies


def get_sent_companies(db_path: Optional[Path] = None) -> set:
    """Get company names we've already sent an actual application to (not drafts)."""
    conn = _get_connection(db_path)
    rows = conn.execute(
        "SELECT DISTINCT LOWER(company) FROM applications"
    ).fetchall()
    conn.close()
    return {row[0] for row in rows}


def get_pending_jobs(db_path: Optional[Path] = None) -> list[dict]:
    """Get jobs that have an email but haven't been applied to or drafted yet."""
    conn = _get_connection(db_path)
    rows = conn.execute(
        """
        SELECT s.unique_key, s.title, s.company, s.location, s.description,
               s.application_email, s.source, s.source_id, s.source_url, s.discovered_at
        FROM seen_jobs s
        WHERE s.application_email != ''
          AND NOT EXISTS (
              SELECT 1 FROM applications a
              WHERE a.job_title = s.title AND a.company = s.company
          )
          AND NOT EXISTS (
              SELECT 1 FROM drafts d
              WHERE d.job_unique_key = s.unique_key
          )
        ORDER BY s.discovered_at DESC
        """,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Drafts ---


def save_draft(
    job_unique_key: str,
    job_title: str,
    company: str,
    location: str,
    recipient_email: str,
    subject: str,
    body: str,
    source: str,
    source_url: str,
    db_path: Optional[Path] = None,
) -> int:
    """Save a draft email. Returns the draft ID."""
    conn = _get_connection(db_path)
    cursor = conn.execute(
        """
        INSERT INTO drafts
            (job_unique_key, job_title, company, location, recipient_email,
             subject, body, source, source_url, created_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            job_unique_key,
            job_title,
            company,
            location,
            recipient_email,
            subject,
            body,
            source,
            source_url,
            datetime.utcnow().isoformat(),
        ),
    )
    draft_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return draft_id


def get_drafts(status: Optional[str] = None, db_path: Optional[Path] = None) -> list[dict]:
    """Get drafts, optionally filtered by status ('pending', 'approved', 'sent', 'discarded')."""
    conn = _get_connection(db_path)
    if status:
        rows = conn.execute(
            "SELECT * FROM drafts WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM drafts ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_draft_by_id(draft_id: int, db_path: Optional[Path] = None) -> Optional[dict]:
    """Get a single draft by ID."""
    conn = _get_connection(db_path)
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_draft_status(draft_id: int, status: str, db_path: Optional[Path] = None) -> bool:
    """Update a draft's status. Returns True if the draft existed."""
    conn = _get_connection(db_path)
    cursor = conn.execute(
        "UPDATE drafts SET status = ? WHERE id = ?", (status, draft_id)
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def update_draft_content(
    draft_id: int, subject: str, body: str, db_path: Optional[Path] = None
) -> bool:
    """Update a draft's subject and body. Returns True if the draft existed."""
    conn = _get_connection(db_path)
    cursor = conn.execute(
        "UPDATE drafts SET subject = ?, body = ? WHERE id = ?",
        (subject, body, draft_id),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def delete_draft(draft_id: int, db_path: Optional[Path] = None) -> bool:
    """Delete a draft. Returns True if the draft existed."""
    conn = _get_connection(db_path)
    cursor = conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0
