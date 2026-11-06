"""Greenhouse, Lever and Ashby public job board APIs.

Endpoints (checked against the official docs on 2026-11-06):
  Greenhouse  GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
  Lever       GET https://api.lever.co/v0/postings/{site}?mode=json
  Ashby       GET https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true
All three are public and need no authentication.
"""

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from scout_core.http import PoliteClient
from scout_core.sources.base import BoardNotFound, JobSource, SourceError
from scout_core.text import html_to_text
from scout_core.types import NormalizedJob, RawFetch, Salary


async def _get_json(client: PoliteClient, source: str, token: str, url: str) -> RawFetch:
    response = await client.get(url)
    if response.status_code == 404:
        raise BoardNotFound(f"{source} board '{token}' not found")
    if response.status_code != 200:
        raise SourceError(f"{source} board '{token}' returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceError(f"{source} board '{token}' returned invalid JSON") from exc
    return RawFetch(source=source, board_token=token, url=url, status_code=response.status_code,
                    fetched_at=datetime.now(UTC), payload=payload)


def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class GreenhouseSource(JobSource):
    name = "greenhouse"

    async def fetch(self, client: PoliteClient, board_token: str) -> RawFetch:
        url = f"https://boards-api.greenhouse.io/v1/boards/{quote(board_token)}/jobs?content=true"
        raw = await _get_json(client, self.name, board_token, url)
        if not isinstance(raw.payload, dict) or "jobs" not in raw.payload:
            raise SourceError("greenhouse response has no 'jobs' list")
        return raw

    def parse(self, raw: RawFetch) -> list[NormalizedJob]:
        out = []
        for j in raw.payload.get("jobs", []):
            loc = (j.get("location") or {}).get("name")
            locations = [loc] if loc else []
            for office in j.get("offices") or []:
                office_loc = office.get("location") or office.get("name")
                if office_loc and office_loc not in locations and office.get("location"):
                    locations.append(office_loc)
            published = _iso(j.get("first_published"))
            out.append(NormalizedJob(
                source=self.name,
                source_job_id=str(j["id"]),
                company_name=j.get("company_name") or raw.board_token or "",
                title=(j.get("title") or "").strip(),
                description_text=html_to_text(j.get("content")),
                location=loc,
                locations=locations,
                department=", ".join(d["name"] for d in j.get("departments") or [] if d.get("name")) or None,
                posted_at=published or _iso(j.get("updated_at")),
                posted_at_kind="published" if published else "updated",
                apply_url=j["absolute_url"],
                board_token=raw.board_token,
                extra={"requisition_id": j.get("requisition_id"),
                       "application_deadline": j.get("application_deadline")},
            ))
        return out


class LeverSource(JobSource):
    name = "lever"

    async def fetch(self, client: PoliteClient, board_token: str) -> RawFetch:
        url = f"https://api.lever.co/v0/postings/{quote(board_token)}?mode=json"
        raw = await _get_json(client, self.name, board_token, url)
        if not isinstance(raw.payload, list):
            raise SourceError("lever response is not a list of postings")
        return raw

    def parse(self, raw: RawFetch) -> list[NormalizedJob]:
        out = []
        for p in raw.payload:
            cats = p.get("categories") or {}
            locations = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
            parts = [p.get("openingPlain"), p.get("descriptionBodyPlain") or p.get("descriptionPlain")]
            for block in p.get("lists") or []:
                parts.append(f"{block.get('text', '')}\n{html_to_text(block.get('content')) or ''}")
            parts.append(p.get("additionalPlain"))
            description = "\n\n".join(x.strip() for x in parts if x and x.strip()) or None
            created = p.get("createdAt")
            sr = p.get("salaryRange") or None
            workplace = p.get("workplaceType")
            out.append(NormalizedJob(
                source=self.name,
                source_job_id=p["id"],
                company_name=raw.board_token or "",
                title=(p.get("text") or "").strip(),
                description_text=description,
                location=cats.get("location"),
                locations=locations,
                remote=True if workplace == "remote" else (False if workplace in ("on-site", "onsite") else None),
                employment_type=cats.get("commitment"),
                department=cats.get("team") or cats.get("department"),
                posted_at=datetime.fromtimestamp(created / 1000, UTC) if isinstance(created, (int, float)) else None,
                posted_at_kind="created" if created else None,
                apply_url=p.get("hostedUrl") or p.get("applyUrl"),
                salary=Salary(currency=sr.get("currency"), min=sr.get("min"), max=sr.get("max"),
                              interval=sr.get("interval"), text=p.get("salaryDescriptionPlain")) if sr else None,
                board_token=raw.board_token,
                extra={"country": p.get("country"), "workplace_type": workplace},
            ))
        return out


_ASHBY_EMPLOYMENT = {"FullTime": "Full Time", "PartTime": "Part Time", "Intern": "Intern",
                     "Contract": "Contract", "Temporary": "Temporary"}


class AshbySource(JobSource):
    name = "ashby"

    async def fetch(self, client: PoliteClient, board_token: str) -> RawFetch:
        url = (f"https://api.ashbyhq.com/posting-api/job-board/{quote(board_token)}"
               "?includeCompensation=true")
        raw = await _get_json(client, self.name, board_token, url)
        if not isinstance(raw.payload, dict) or "jobs" not in raw.payload:
            raise SourceError("ashby response has no 'jobs' list")
        return raw

    def parse(self, raw: RawFetch) -> list[NormalizedJob]:
        out = []
        for j in raw.payload.get("jobs", []):
            if j.get("isListed") is False:
                continue  # unlisted jobs are direct-link only; do not surface them
            locations = [j["location"]] if j.get("location") else []
            countries: list[str] = []
            addr = ((j.get("address") or {}).get("postalAddress") or {})
            if addr.get("addressCountry"):
                countries.append(addr["addressCountry"])
            for sec in j.get("secondaryLocations") or []:
                if sec.get("location"):
                    locations.append(sec["location"])
                sec_country = (((sec.get("address") or {}).get("postalAddress") or {}).get("addressCountry"))
                if sec_country:
                    countries.append(sec_country)
            comp = j.get("compensation") or {}
            summary = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
            out.append(NormalizedJob(
                source=self.name,
                source_job_id=j["id"],
                company_name=raw.board_token or "",
                title=(j.get("title") or "").strip(),
                description_text=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                location=j.get("location"),
                locations=locations + [c for c in countries if c not in locations],
                remote=j.get("workplaceType") == "Remote" if j.get("workplaceType") else j.get("isRemote"),
                employment_type=_ASHBY_EMPLOYMENT.get(j.get("employmentType") or "", j.get("employmentType")),
                department=j.get("department") or j.get("team"),
                posted_at=_iso(j.get("publishedAt")),
                posted_at_kind="published" if j.get("publishedAt") else None,
                apply_url=j.get("jobUrl") or j.get("applyUrl"),
                salary=Salary(text=summary) if summary else None,
                board_token=raw.board_token,
                extra={"workplace_type": j.get("workplaceType")},
            ))
        return out


SOURCES: dict[str, JobSource] = {
    "greenhouse": GreenhouseSource(),
    "lever": LeverSource(),
    "ashby": AshbySource(),
}


def country_codes(job: NormalizedJob) -> list[str]:
    """Country hints from source-specific fields (Lever gives ISO codes, Ashby gives names)."""
    codes: list[str] = []
    c = job.extra.get("country")
    if isinstance(c, str):
        codes.append(c)
    return codes


def ats_from_url(url: str) -> tuple[str, str] | None:
    """Recognise a public board link and return (ats_type, board_token)."""
    import re

    patterns: list[tuple[str, Any]] = [
        ("greenhouse", re.compile(r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:embed/job_board\?for=|v1/boards/)?([A-Za-z0-9_-]+)")),
        ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)")),
        ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
    ]
    for ats, pat in patterns:
        m = pat.search(url)
        if m:
            token = m.group(1)
            if token.lower() in {"embed", "v1", "jobs", "api"}:
                return None
            return ats, token
    return None
