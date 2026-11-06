"""Domain types shared by sources, the pipeline, the API and the workers."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AtsType(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    OTHER = "other"


class ApplicationStatus(StrEnum):
    DISCOVERED = "discovered"
    QUALIFIED = "qualified"
    RESUME_READY = "resume_ready"
    APPLIED = "applied"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    SKIPPED = "skipped"


# Statuses that mean "I already acted on this": a repost of such a job must never resurface.
TERMINAL_FOR_FEED = {
    ApplicationStatus.SKIPPED,
    ApplicationStatus.APPLIED,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.REJECTED,
    ApplicationStatus.OFFER,
}


class Salary(BaseModel):
    currency: str | None = None
    min: float | None = None
    max: float | None = None
    interval: str | None = None  # "year", "month", "hour" ...
    text: str | None = None
    is_estimate: bool = False


class NormalizedJob(BaseModel):
    """One job, as produced by a source parser. Missing details stay None (never guessed)."""

    source: str
    source_job_id: str
    company_name: str
    title: str
    description_text: str | None = None
    location: str | None = None
    locations: list[str] = Field(default_factory=list)
    remote: bool | None = None
    employment_type: str | None = None
    department: str | None = None
    posted_at: datetime | None = None
    posted_at_kind: str | None = None  # "published" | "updated" | "created": how reliable posted_at is
    apply_url: str
    salary: Salary | None = None
    board_token: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class RawFetch(BaseModel):
    """A source response kept verbatim for the raw store."""

    source: str
    board_token: str | None
    url: str
    status_code: int
    fetched_at: datetime
    payload: Any
