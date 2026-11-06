from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from scout_core.db.models import Company, Job
from scout_core.services import salary_text, why_line


class TrustOut(BaseModel):
    level: str
    score: int
    signals: list[dict[str, Any]]


class CompanyBrief(BaseModel):
    id: UUID | None
    name: str
    website: str | None = None
    description: str | None = None
    funding_stage: str | None = None
    size: str | None = None
    hq: str | None = None
    in_yc_directory: bool = False
    yc_batch: str | None = None


class JobOut(BaseModel):
    id: UUID
    title: str
    company: CompanyBrief
    location: str | None
    locations: list[str]
    location_bucket: str
    remote: bool | None
    salary_text: str | None
    employment_type: str | None
    department: str | None
    source: str
    apply_url: str
    posted_at: datetime | None
    posted_at_kind: str | None
    first_seen_at: datetime
    is_new: bool
    fresher_confidence: str | None
    fresher_reasons: list[str]
    experience_unclear: bool
    experience_min: float | None
    experience_max: float | None
    role_family: str
    relevance_score: int
    relevance_matched: list[str]
    trust: TrustOut
    verify_status: str | None
    verify_reason: str | None
    verified_at: datetime | None
    is_open: bool | None
    status: str
    why: str


class JobDetail(JobOut):
    description: str | None
    filter_reasons: list[str]


def company_brief(job: Job, company: Company | None) -> CompanyBrief:
    if company is None:
        return CompanyBrief(id=None, name=job.company_name)
    return CompanyBrief(
        id=company.id, name=company.name, website=company.website, description=company.description,
        funding_stage=company.funding_stage, size=company.size, hq=company.hq,
        in_yc_directory=company.in_yc_directory,
        yc_batch=(company.funding_info or {}).get("batch") if company.in_yc_directory else None,
    )


def job_out(job: Job, company: Company | None, last_visit: datetime | None, detail: bool = False):
    data = dict(
        id=job.id, title=job.title, company=company_brief(job, company), location=job.location,
        locations=job.locations or [], location_bucket=job.location_bucket, remote=job.remote,
        salary_text=salary_text(job), employment_type=job.employment_type, department=job.department,
        source=job.source, apply_url=job.apply_url, posted_at=job.posted_at,
        posted_at_kind=job.posted_at_kind, first_seen_at=job.first_seen_at,
        is_new=last_visit is None or job.first_seen_at > last_visit,
        fresher_confidence=job.fresher_confidence, fresher_reasons=job.fresher_reasons or [],
        experience_unclear=job.experience_unclear,
        experience_min=float(job.experience_min) if job.experience_min is not None else None,
        experience_max=float(job.experience_max) if job.experience_max is not None else None,
        role_family=job.role_family, relevance_score=job.relevance_score,
        relevance_matched=job.relevance_matched or [],
        trust=TrustOut(**(job.trust or {"level": job.trust_level, "score": 0, "signals": []})),
        verify_status=job.verify_status, verify_reason=job.verify_reason, verified_at=job.verified_at,
        is_open=job.is_open, status=job.status.value, why=why_line(job),
    )
    if detail:
        return JobDetail(**data, description=job.description, filter_reasons=job.filter_reasons or [])
    return JobOut(**data)


class JobPage(BaseModel):
    items: list[JobOut]
    total: int
    next_offset: int | None
    facets: dict[str, dict[str, int]]


class StatusIn(BaseModel):
    status: Literal["discovered", "skipped", "applied", "interview", "rejected", "offer"]
    notes: str | None = Field(default=None, max_length=5000)


class ExperienceCorrectionIn(BaseModel):
    entry_level: bool


class SavedSearchIn(BaseModel):
    site: Literal["linkedin", "wellfound", "internshala", "instahyre", "cutshort", "yc", "naukri", "other"]
    label: str = Field(min_length=1, max_length=120)
    url: HttpUrl


class SavedSearchOut(BaseModel):
    id: UUID
    site: str
    label: str
    url: str
    position: int


class BoardIn(BaseModel):
    url: HttpUrl
    company_name: str = Field(min_length=1, max_length=200)
    website: HttpUrl | None = None
