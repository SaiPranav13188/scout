"""Company discovery: find each company's public job board without guessing.

A board is added to the watchlist only when
  1. the company's own website links to it (respecting robots.txt), and
  2. the board's public API answers 200.
Nothing is inferred from the company name alone.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from rapidfuzz import fuzz
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from scout_core.http import PoliteClient
from scout_core.normalize import normalize_company, registered_domain
from scout_core.sources.ats import SOURCES, ats_from_url
from scout_core.sources.base import SourceError

YC_DATASET_URL = "https://yc-oss.github.io/api/companies/all.json"
CAREER_PATHS = ("", "/careers", "/jobs", "/careers/", "/company/careers")
MAX_BOARDS_PER_SITE = 2
_CAREER_LINK = re.compile(r"career|jobs|join|hiring|work-with-us|openings", re.IGNORECASE)


@dataclass
class CompanySeed:
    """A company found by a discovery source. Unknown fields stay None."""

    name: str
    website: str | None
    source: str
    description: str | None = None
    founded_year: int | None = None
    hq: str | None = None
    size: str | None = None
    funding_stage: str | None = None
    funding_info: dict | None = None
    linkedin_url: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class BoardFound:
    ats: str
    token: str
    evidence_url: str
    job_count: int | None = None


def yc_seeds(dataset: list[dict], *, only_hiring: bool = True) -> list[CompanySeed]:
    """Map the open YC directory dataset to seeds. Only fields the dataset really has are used."""
    seeds = []
    for c in dataset:
        if c.get("status") != "Active":
            continue
        if only_hiring and not c.get("isHiring"):
            continue
        if not c.get("name"):
            continue
        seeds.append(CompanySeed(
            name=c["name"].strip(),
            website=c.get("website") or None,
            source="yc_directory",
            description=c.get("one_liner") or c.get("long_description") or None,
            hq=c.get("all_locations") or None,
            size=str(c["team_size"]) if c.get("team_size") else None,
            funding_stage=c.get("stage") or None,
            funding_info={"accelerator": "Y Combinator", "batch": c.get("batch"),
                          "yc_url": c.get("url")},
            tags=list(c.get("tags") or []) + list(c.get("regions") or []),
        ))
    return seeds


class RobotsCache:
    def __init__(self, client: PoliteClient, user_agent: str) -> None:
        self._client = client
        self._ua = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._parsers:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                r = await self._client.get(f"{origin}/robots.txt")
                if r.status_code == 200:
                    parser.parse(r.text.splitlines())
                elif r.status_code in (401, 403):
                    parser.disallow_all = True
                else:
                    parser = None  # no robots.txt: allowed
            except httpx.HTTPError:
                parser = None
            self._parsers[origin] = parser
        p = self._parsers[origin]
        return True if p is None else p.can_fetch(self._ua, url)


def find_board_links(html: str, base_url: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Return (ATS boards linked from the page, same-site career page links to follow)."""
    boards: list[tuple[str, str]] = []
    follow: list[str] = []
    base_domain = registered_domain(base_url)
    tree = HTMLParser(html)
    candidates = [a.attributes.get("href") or "" for a in tree.css("a[href]")]
    candidates += [f.attributes.get("src") or "" for f in tree.css("iframe[src], script[src]")]
    # Embedded boards are sometimes configured in inline scripts.
    candidates += re.findall(r"https?://[A-Za-z0-9.-]*(?:greenhouse\.io|lever\.co|ashbyhq\.com)[^\s\"'<>]*", html)
    for href in candidates:
        if not href:
            continue
        absolute = urljoin(base_url, href)
        found = ats_from_url(absolute)
        if found and found not in boards:
            boards.append(found)
        elif (registered_domain(absolute) == base_domain and _CAREER_LINK.search(urlsplit(absolute).path)
              and absolute not in follow):
            follow.append(absolute)
    return boards, follow[:3]


async def discover_boards(client: PoliteClient, robots: RobotsCache, website: str,
                          company_name: str | None = None) -> list[BoardFound]:
    """Scan a company website for linked ATS boards, then confirm each board via its API."""
    if not website.startswith(("http://", "https://")):
        website = "https://" + website
    base = website.rstrip("/")
    to_visit = [base + p for p in CAREER_PATHS]
    to_visit_first = to_visit[0]
    visited: set[str] = set()
    found: dict[tuple[str, str], str] = {}

    while to_visit and len(visited) < 7:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.add(url)
        if not await robots.allowed(url):
            continue
        try:
            r = await client.get(url, headers={"Accept": "text/html"})
        except httpx.ConnectError:
            if url == to_visit_first:
                return []  # site unreachable (dead domain): don't try its other pages
            continue
        except httpx.HTTPError:
            continue
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            continue
        boards, follow = find_board_links(r.text, str(r.url))
        for b in boards:
            found.setdefault(b, url)
        to_visit.extend(f for f in follow if f not in visited)

    if len(found) > MAX_BOARDS_PER_SITE:
        # A site linking to many different boards is a job list or portfolio page (e.g. vector.co
        # linked 97 other companies' boards), not this company's own careers page.
        return []

    confirmed = []
    for (ats, token), evidence in found.items():
        source = SOURCES.get(ats)
        if source is None:
            continue
        try:
            raw = await source.fetch(client, token)
        except (SourceError, httpx.HTTPError):
            continue
        if company_name and not board_matches_company(raw.payload, company_name):
            continue
        count = len(raw.payload) if isinstance(raw.payload, list) else len(raw.payload.get("jobs", []))
        confirmed.append(BoardFound(ats, token, evidence, count))
    return confirmed


def board_matches_company(payload, company_name: str) -> bool:
    """Greenhouse reports the board owner's name on each job; it must resemble the company.

    Lever and Ashby responses carry no company name, so they pass (the website link plus the
    MAX_BOARDS_PER_SITE rule are the evidence there).
    """
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    owner = next((j.get("company_name") for j in jobs if j.get("company_name")), None)
    if not owner:
        return True
    a, b = normalize_company(owner), normalize_company(company_name)
    return bool(a and b) and fuzz.token_set_ratio(a, b) >= 80


def now() -> datetime:
    return datetime.now(UTC)
