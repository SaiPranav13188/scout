"""PostgreSQL schema (spec §10). Postgres is the source of truth for everything shown in the UI."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from scout_core.types import ApplicationStatus, AtsType


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


ats_enum = Enum(AtsType, name="ats_type", values_callable=lambda e: [x.value for x in e])
status_enum = Enum(ApplicationStatus, name="application_status", values_callable=lambda e: [x.value for x in e])
run_status_enum = Enum("running", "success", "error", "partial", name="run_status")
notification_status_enum = Enum("pending", "sent", "failed", "suppressed", name="notification_status")
outreach_channel_enum = Enum("email", "linkedin", "other", name="outreach_channel")
label_enum = Enum("apply", "maybe", "no", name="job_label")


# ------------------------------------------------------------------ profile & settings

class Profile(Base):
    __tablename__ = "profile"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text)
    contact: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    preferences: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    mandatory_flags: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = _updated()


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    id: Mapped[uuid.UUID] = _pk()
    site: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = _created()


# ------------------------------------------------------------------ companies

class Company(Base):
    __tablename__ = "companies"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text, unique=True)
    website: Mapped[str | None] = mapped_column(Text)
    normalized_domain: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    founded_year: Mapped[int | None] = mapped_column(Integer)
    founders: Mapped[list[Any] | None] = mapped_column(JSONB)
    funding_stage: Mapped[str | None] = mapped_column(Text)
    funding_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    hq: Mapped[str | None] = mapped_column(Text)
    size: Mapped[str | None] = mapped_column(Text)
    career_url: Mapped[str | None] = mapped_column(Text)
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    in_yc_directory: Mapped[bool] = mapped_column(Boolean, server_default="false")
    domain_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trust: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    discovered_at: Mapped[datetime] = _created()
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("uq_companies_domain", "normalized_domain", unique=True,
              postgresql_where=text("normalized_domain IS NOT NULL")),
    )


class CompanyWatchlist(Base):
    __tablename__ = "company_watchlist"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    ats_type: Mapped[AtsType] = mapped_column(ats_enum)
    board_token: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    discovery_method: Mapped[str] = mapped_column(Text)  # "website_link" | "manual"
    evidence_url: Mapped[str | None] = mapped_column(Text)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("ats_type", "board_token", name="uq_watchlist_board"),)


# ------------------------------------------------------------------ jobs

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    company_name: Mapped[str] = mapped_column(Text)
    company_norm: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    source_job_id: Mapped[str] = mapped_column(Text)
    board_token: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    title_norm: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text)
    required_skills: Mapped[list[Any] | None] = mapped_column(JSONB)
    preferred_skills: Mapped[list[Any] | None] = mapped_column(JSONB)
    experience_min: Mapped[float | None] = mapped_column(Numeric(4, 2))
    experience_max: Mapped[float | None] = mapped_column(Numeric(4, 2))
    experience_unclear: Mapped[bool] = mapped_column(Boolean, server_default="false")
    experience_corrected_by_me: Mapped[bool] = mapped_column(Boolean, server_default="false")
    fresher_confidence: Mapped[str | None] = mapped_column(Text)
    fresher_reasons: Mapped[list[Any] | None] = mapped_column(JSONB)
    education: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    locations: Mapped[list[Any] | None] = mapped_column(JSONB)
    location_bucket: Mapped[str] = mapped_column(Text)
    location_key: Mapped[str] = mapped_column(Text, server_default="")
    remote: Mapped[bool | None] = mapped_column(Boolean)
    salary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    employment_type: Mapped[str | None] = mapped_column(Text)
    role_family: Mapped[str] = mapped_column(Text)
    relevance_score: Mapped[int] = mapped_column(Integer)
    relevance_matched: Mapped[list[Any] | None] = mapped_column(JSONB)
    trust_level: Mapped[str] = mapped_column(Text)
    trust: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_at_kind: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    apply_url: Mapped[str] = mapped_column(Text)
    canonical_apply_url: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verify_status: Mapped[str | None] = mapped_column(Text)
    verify_reason: Mapped[str | None] = mapped_column(Text)
    is_open: Mapped[bool | None] = mapped_column(Boolean)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str] = mapped_column(Text)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    duplicate_reason: Mapped[str | None] = mapped_column(Text)
    passed_filters: Mapped[bool] = mapped_column(Boolean)
    filter_reasons: Mapped[list[Any] | None] = mapped_column(JSONB)
    status: Mapped[ApplicationStatus] = mapped_column(status_enum, server_default=ApplicationStatus.DISCOVERED.value)
    first_seen_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _updated()
    # embedding vector(N) is added in Phase 3, once the Gemini embedding model (and N) is chosen.
    __table_args__ = (
        UniqueConstraint("source", "source_job_id", name="uq_jobs_source_id"),
        Index("ix_jobs_dedupe_key", "dedupe_key"),
        Index("ix_jobs_posted_at", "posted_at"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_canonical_url", "canonical_apply_url"),
        Index("ix_jobs_company_norm", "company_norm"),
        Index("ix_jobs_feed", "passed_filters", "first_seen_at",
              postgresql_where=text("duplicate_of IS NULL")),
    )


# ------------------------------------------------------------------ scoring & resumes (used from Phase 3/4)

class MasterResume(Base):
    __tablename__ = "master_resume"
    id: Mapped[uuid.UUID] = _pk()
    file_url: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="false")
    uploaded_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("version", name="uq_master_resume_version"),
        Index("uq_master_resume_one_active", "is_active", unique=True, postgresql_where=text("is_active")),
    )


class JobScore(Base):
    __tablename__ = "job_scores"
    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    score: Mapped[int] = mapped_column(Integer)
    breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reasons: Mapped[list[Any] | None] = mapped_column(JSONB)
    matched_skills: Mapped[list[Any] | None] = mapped_column(JSONB)
    missing_skills: Mapped[list[Any] | None] = mapped_column(JSONB)
    red_flags: Mapped[list[Any] | None] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    # Fingerprint of the profile (resume + target roles) the score was computed from; a new
    # fingerprint means the score is out of date and the job gets re-scored.
    profile_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_score_range"),
        Index("ix_job_scores_job_created", "job_id", "created_at"),
    )


class ResumeGenerated(Base):
    __tablename__ = "resumes_generated"
    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    master_resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("master_resume.id"))
    file_url_pdf: Mapped[str | None] = mapped_column(Text)
    file_url_docx: Mapped[str | None] = mapped_column(Text)
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validation_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class JobLabel(Base):
    """My judgement of a job, for the matching eval set (spec §11 Phase 3, §13.1)."""

    __tablename__ = "job_labels"
    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    label: Mapped[str] = mapped_column(label_enum)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# ------------------------------------------------------------------ applications & outreach

class Application(Base):
    __tablename__ = "applications"
    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    status: Mapped[ApplicationStatus] = mapped_column(status_enum)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    platform: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resumes_generated.id"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Recruiter(Base):
    __tablename__ = "recruiters"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    profile_url: Mapped[str | None] = mapped_column(Text)
    email_if_public: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)


class Outreach(Base):
    __tablename__ = "outreach"
    id: Mapped[uuid.UUID] = _pk()
    recruiter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recruiters.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(outreach_channel_enum)
    draft: Mapped[str] = mapped_column(Text)
    sent_by_me: Mapped[bool] = mapped_column(Boolean, server_default="false")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response: Mapped[str | None] = mapped_column(Text)
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("recruiter_id", "job_id", name="uq_outreach_once"),)


# ------------------------------------------------------------------ ops

class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = _pk()
    type: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(notification_status_enum, server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("type", "dedupe_key", name="uq_notification_once"),)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = _pk()
    worker: Mapped[str] = mapped_column(Text)
    task_key: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(run_status_enum, server_default="running")
    counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    retries: Mapped[int] = mapped_column(Integer, server_default="0")
    __table_args__ = (Index("ix_agent_runs_worker_started", "worker", "started_at"),)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(Text)  # "me" | "worker:<name>" | "telegram"
    action: Mapped[str] = mapped_column(Text)
    entity: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class EncryptedSecret(Base):
    __tablename__ = "secrets_encrypted"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = _updated()
