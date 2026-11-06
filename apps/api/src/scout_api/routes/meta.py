from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from scout_api.deps import db
from scout_api.schemas import BoardIn, SavedSearchIn, SavedSearchOut, SavedSearchUpdate
from scout_core.db.models import AgentRun, Company, CompanyWatchlist, Job, SavedSearch
from scout_core.http import PoliteClient
from scout_core.pipeline.ingest import get_or_create_company
from scout_core.runs import audit
from scout_core.services import today_stats, visible_feed_query
from scout_core.settings_store import DEFAULTS, get_all, get_setting, set_setting
from scout_core.sources.ats import SOURCES, ats_from_url
from scout_core.sources.base import SourceError
from scout_core.types import ApplicationStatus, AtsType

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/stats")
async def stats(session: Annotated[AsyncSession, Depends(db)]):
    out: dict[str, Any] = await today_stats(session)
    out["boards_active"] = await session.scalar(
        select(func.count()).select_from(CompanyWatchlist).where(CompanyWatchlist.active.is_(True)))
    out["companies"] = await session.scalar(select(func.count()).select_from(Company))
    out["jobs_seen_total"] = await session.scalar(select(func.count()).select_from(Job))
    out["last_ingest_at"] = await session.scalar(select(func.max(AgentRun.finished_at)).where(
        AgentRun.worker == "ingest", AgentRun.status.in_(["success", "partial"])))
    return out


@router.post("/visit")
async def mark_visit(session: Annotated[AsyncSession, Depends(db)]):
    """Called when I open Today: returns the previous visit time and stores now."""
    previous = await get_setting(session, "last_visit_at")
    await set_setting(session, "last_visit_at", datetime.now(UTC).isoformat())
    await session.commit()
    return {"previous_visit_at": previous}


# ------------------------------------------------------------------ settings

@router.get("/settings")
async def read_settings(session: Annotated[AsyncSession, Depends(db)]):
    return await get_all(session)


@router.put("/settings/{key}")
async def write_setting(key: str, session: Annotated[AsyncSession, Depends(db)], value: Any = Body(...)):
    if key not in DEFAULTS or key == "last_visit_at":
        raise HTTPException(404, "unknown setting")
    default = DEFAULTS[key]
    if isinstance(default, dict):
        if not isinstance(value, dict):
            raise HTTPException(422, "expected an object")
        unknown = set(value) - set(default)
        if unknown:
            raise HTTPException(422, f"unknown fields: {', '.join(sorted(unknown))}")
    elif isinstance(default, bool) and not isinstance(value, bool):
        raise HTTPException(422, "expected true/false")
    before = await get_setting(session, key)
    await set_setting(session, key, value)
    audit(session, actor="me", action="update_setting", entity="settings", entity_id=key,
          before={"value": before}, after={"value": value})
    await session.commit()
    return await get_setting(session, key)


# ------------------------------------------------------------------ saved searches

@router.get("/saved-searches", response_model=list[SavedSearchOut])
async def list_saved(session: Annotated[AsyncSession, Depends(db)]):
    rows = (await session.scalars(select(SavedSearch).order_by(SavedSearch.position, SavedSearch.created_at))).all()
    return [SavedSearchOut(id=r.id, site=r.site, label=r.label, url=r.url, position=r.position) for r in rows]


@router.post("/saved-searches", response_model=SavedSearchOut, status_code=201)
async def add_saved(body: SavedSearchIn, session: Annotated[AsyncSession, Depends(db)]):
    pos = (await session.scalar(select(func.max(SavedSearch.position)))) or 0
    row = SavedSearch(site=body.site, label=body.label, url=str(body.url), position=pos + 1)
    session.add(row)
    await session.commit()
    return SavedSearchOut(id=row.id, site=row.site, label=row.label, url=row.url, position=row.position)


@router.patch("/saved-searches/{search_id}", response_model=SavedSearchOut)
async def update_saved(search_id: UUID, body: SavedSearchUpdate, session: Annotated[AsyncSession, Depends(db)]):
    row = await session.get(SavedSearch, search_id)
    if row is None:
        raise HTTPException(404, "saved search not found")
    if body.site is not None:
        row.site = body.site
    if body.label is not None:
        row.label = body.label.strip()
    if body.url is not None:
        row.url = str(body.url)
    await session.commit()
    return SavedSearchOut(id=row.id, site=row.site, label=row.label, url=row.url, position=row.position)


@router.delete("/saved-searches/{search_id}", status_code=204)
async def delete_saved(search_id: UUID, session: Annotated[AsyncSession, Depends(db)]):
    row = await session.get(SavedSearch, search_id)
    if row:
        await session.delete(row)
        await session.commit()


# ------------------------------------------------------------------ companies & boards

@router.get("/companies")
async def list_companies(session: Annotated[AsyncSession, Depends(db)], limit: int = 200):
    visible = visible_feed_query().subquery()
    open_jobs = (select(visible.c.company_id, func.count().label("n"))
                 .where(visible.c.status == ApplicationStatus.DISCOVERED.value)
                 .group_by(visible.c.company_id).subquery())
    rows = (await session.execute(
        select(Company, CompanyWatchlist, func.coalesce(open_jobs.c.n, 0))
        .join(CompanyWatchlist, CompanyWatchlist.company_id == Company.id)
        .outerjoin(open_jobs, open_jobs.c.company_id == Company.id)
        .order_by(func.coalesce(open_jobs.c.n, 0).desc(), Company.name).limit(limit))).all()
    return [{
        "id": c.id, "name": c.name, "website": c.website, "description": c.description,
        "funding_stage": c.funding_stage, "size": c.size, "hq": c.hq, "career_url": c.career_url,
        "in_yc_directory": c.in_yc_directory, "trust": c.trust,
        "board": {"ats": w.ats_type.value, "token": w.board_token, "active": w.active,
                  "discovery_method": w.discovery_method, "last_fetched_at": w.last_fetched_at,
                  "last_error": w.last_error},
        "open_fresher_jobs": n,
    } for c, w, n in rows]


@router.post("/companies/boards", status_code=201)
async def add_board(body: BoardIn, session: Annotated[AsyncSession, Depends(db)]):
    """Add a 'dream company' by pasting its Greenhouse/Lever/Ashby board link. Confirmed live first."""
    found = ats_from_url(str(body.url))
    if not found:
        raise HTTPException(422, "Not a Greenhouse, Lever or Ashby job board link.")
    ats, token = found
    try:
        async with PoliteClient(max_retries=1) as client:
            raw = await SOURCES[ats].fetch(client, token)
    except SourceError as exc:
        raise HTTPException(422, f"Board could not be confirmed: {exc}") from exc
    company = await get_or_create_company(session, name=body.company_name,
                                          website=str(body.website) if body.website else None, source="manual")
    exists = await session.scalar(select(CompanyWatchlist).where(
        CompanyWatchlist.ats_type == AtsType(ats), CompanyWatchlist.board_token == token))
    if exists:
        exists.active = True
    else:
        session.add(CompanyWatchlist(company_id=company.id, ats_type=AtsType(ats), board_token=token,
                                     discovery_method="manual", evidence_url=str(body.url)))
    audit(session, actor="me", action="add_board", entity="company_watchlist", entity_id=f"{ats}/{token}")
    await session.commit()
    count = len(raw.payload) if isinstance(raw.payload, list) else len(raw.payload.get("jobs", []))
    return {"ats": ats, "token": token, "jobs_listed": count, "company_id": company.id}


# ------------------------------------------------------------------ agent health (basic; full page in Phase 5)

@router.get("/agent-runs")
async def agent_runs(session: Annotated[AsyncSession, Depends(db)], limit: int = 50):
    rows = (await session.scalars(select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit))).all()
    return [{"id": r.id, "worker": r.worker, "task_key": r.task_key, "status": r.status,
             "started_at": r.started_at, "finished_at": r.finished_at, "counts": r.counts,
             "error": r.error, "retries": r.retries} for r in rows]
