"""Which scheduled tasks are due right now (plain code).

Every due task gets a slot key, e.g. "ingest:2026-11-06T08" or "digest:2026-11-06". A slot runs
at most once: the worker records its key in agent_runs, and the tick skips slots already done.
Because slots are derived from Postgres state, a Redis restart or downtime never loses work:
a missed slot is simply still due on the next tick.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DueTask:
    task: str
    slot_key: str


def _hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _interval_slot(now: datetime, hours: float, name: str) -> DueTask:
    seconds = max(1, int(hours * 3600))
    start = datetime.fromtimestamp(int(now.timestamp()) // seconds * seconds, UTC)
    return DueTask(name, f"{name}:{start.strftime('%Y-%m-%dT%H:%M')}")


def due_tasks(now: datetime, schedule: dict, *, paused: bool = False) -> list[DueTask]:
    """All slots that should have run by `now`. Caller filters out slots already done."""
    tz = ZoneInfo(schedule.get("timezone", "Asia/Kolkata"))
    local = now.astimezone(tz)
    due: list[DueTask] = []

    if not paused:
        minutes = schedule.get("ingest_interval_minutes")
        hours = minutes / 60 if minutes else schedule.get("ingest_interval_hours", 4)
        due.append(_interval_slot(now, hours, "ingest"))
        due.append(_interval_slot(now, schedule["verify_interval_hours"], "verify"))

        disc_day = int(schedule.get("discovery_weekday", 6))
        days_since = (local.weekday() - disc_day) % 7
        disc_date = local.date() - timedelta(days=days_since)
        disc_at = datetime.combine(disc_date, _hhmm(schedule["discovery_time"]), tz)
        if local >= disc_at:
            due.append(DueTask("discovery", f"discovery:{disc_date.isoformat()}"))
        else:
            prev = disc_date - timedelta(days=7)
            due.append(DueTask("discovery", f"discovery:{prev.isoformat()}"))

    # Notifications still go out while paused? No: /pause stops alerts and digests too.
    if not paused:
        digest_at = datetime.combine(local.date(), _hhmm(schedule["daily_digest_time"]), tz)
        evening_at = datetime.combine(local.date(), _hhmm(schedule["evening_summary_time"]), tz)
        # A digest missed by downtime is still sent later that day, but not after the evening summary.
        if digest_at <= local < evening_at:
            due.append(DueTask("daily_digest", f"daily_digest:{local.date().isoformat()}"))
        if local >= evening_at:
            due.append(DueTask("evening_summary", f"evening_summary:{local.date().isoformat()}"))
    return due


def in_quiet_hours(now: datetime, quiet: dict, tz_name: str = "Asia/Kolkata") -> bool:
    local = now.astimezone(ZoneInfo(tz_name)).time()
    start, end = _hhmm(quiet["start"]), _hhmm(quiet["end"])
    if start <= end:
        return start <= local < end
    return local >= start or local < end
