"""Pydantic models for Reacher."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobSource(str, Enum):
    LINKEDIN = "linkedin"
    TWITTER = "twitter"


class ExperienceLevel(str, Enum):
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"


class Job(BaseModel):
    """A normalized job posting from any source."""

    title: str
    company: str
    location: str = ""
    description: str = ""
    application_email: str = ""
    application_url: str = ""
    source: JobSource
    source_id: str = ""
    source_url: str = ""
    posted_at: Optional[datetime] = None
    discovered_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def unique_key(self) -> str:
        """Dedup key: source + source_id, or fallback to title+company hash."""
        if self.source_id:
            return f"{self.source.value}:{self.source_id}"
        return f"{self.source.value}:{self.title}:{self.company}".lower()


class Application(BaseModel):
    """A record of an application sent."""

    job_title: str
    company: str
    recipient_email: str
    subject: str
    body: str
    source: JobSource
    source_url: str = ""
    sent_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "sent"


# --- Configuration models ---


class ProfileConfig(BaseModel):
    name: str
    email: str
    phone: str = ""
    location: str = ""
    resume_pdf: str = "resume.pdf"


class SearchConfig(BaseModel):
    keywords: list[str] = [
        "full stack developer",
        "software engineer",
        "react developer",
        "node.js developer",
    ]
    locations: list[str] = ["remote"]
    experience_level: ExperienceLevel = ExperienceLevel.MID


class EmailConfig(BaseModel):
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    address: str
    app_password: str
    sender_name: str


class GeminiConfig(BaseModel):
    api_key: str
    model: str = "gemini-2.0-flash"


class TwitterConfig(BaseModel):
    bearer_token: str


class ScheduleConfig(BaseModel):
    interval_hours: int = 6


class LimitsConfig(BaseModel):
    max_applications_per_run: int = 10
    max_applications_per_day: int = 30


class AppConfig(BaseModel):
    """Top-level application configuration."""

    profile: ProfileConfig
    search: SearchConfig = SearchConfig()
    email: EmailConfig
    gemini: GeminiConfig
    twitter: TwitterConfig
    schedule: ScheduleConfig = ScheduleConfig()
    limits: LimitsConfig = LimitsConfig()
