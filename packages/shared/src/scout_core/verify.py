"""Apply-link verification (spec §6.2).

A job is only "open" when we actually saw a live page (or the ATS API still lists it).
Sites behind a login wall are never fetched with automation; they get LOGIN_REQUIRED.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit

import httpx

from scout_core.http import PoliteClient
from scout_core.normalize import registered_domain
from scout_core.text import fold, html_to_text


class LinkStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    LOGIN_REQUIRED = "login_required"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"  # page loaded but we could not tell (e.g. JS-only page)


# Never fetched automatically (spec §1 non-goals).
LOGIN_WALLED_DOMAINS = {
    "linkedin.com", "wellfound.com", "angel.co", "internshala.com", "instahyre.com",
    "cutshort.io", "naukri.com", "glassdoor.com", "glassdoor.co.in", "indeed.com",
    "workatastartup.com",
}

_CLOSED_PHRASES = re.compile(
    r"(no longer (accepting|available|open|active)|position (has been|is) (filled|closed)|"
    r"job (is|has been) (closed|filled|removed|expired)|this (job|posting|role|position) (has )?"
    r"(expired|closed|is closed)|posting (has )?(expired|closed)|not accepting applications|"
    r"page (you are looking for|not found)|job not found|the job you('| a)re looking for|"
    r"error 404|404 not found)"
)
_GENERIC_CAREER_PATHS = re.compile(r"^/?(careers?|jobs|join(-us)?|work-with-us|openings)?/?$")


@dataclass
class VerifyResult:
    status: LinkStatus
    checked_at: datetime
    http_status: int | None = None
    final_url: str | None = None
    reason: str = ""

    @property
    def is_open(self) -> bool | None:
        if self.status == LinkStatus.OPEN:
            return True
        if self.status == LinkStatus.CLOSED:
            return False
        return None

    @property
    def verified(self) -> bool:
        """True when we reached a definite answer (open or closed)."""
        return self.status in (LinkStatus.OPEN, LinkStatus.CLOSED)


def is_login_walled(url: str) -> bool:
    return (registered_domain(url) or "") in LOGIN_WALLED_DOMAINS


def classify_page(status_code: int, final_url: str, original_url: str, body: str | None) -> VerifyResult:
    now = datetime.now(UTC)
    if status_code in (404, 410):
        return VerifyResult(LinkStatus.CLOSED, now, status_code, final_url, f"HTTP {status_code}")
    if status_code in (401, 403):
        return VerifyResult(LinkStatus.LOGIN_REQUIRED, now, status_code, final_url,
                            f"HTTP {status_code}: page needs login or blocks bots")
    if status_code >= 500:
        return VerifyResult(LinkStatus.UNREACHABLE, now, status_code, final_url, f"HTTP {status_code}")
    if status_code != 200:
        return VerifyResult(LinkStatus.UNKNOWN, now, status_code, final_url, f"HTTP {status_code}")

    orig, final = urlsplit(original_url), urlsplit(final_url)
    if is_login_walled(final_url) and not is_login_walled(original_url):
        return VerifyResult(LinkStatus.LOGIN_REQUIRED, now, status_code, final_url, "redirected to a login site")
    if re.search(r"/(login|signin|sign-in|auth)(/|$|\?)", final.path):
        return VerifyResult(LinkStatus.LOGIN_REQUIRED, now, status_code, final_url, "redirected to a login page")
    # A specific job URL that redirects to the generic careers page usually means the job is gone.
    if orig.path.strip("/") and final.path != orig.path and _GENERIC_CAREER_PATHS.match(final.path):
        return VerifyResult(LinkStatus.CLOSED, now, status_code, final_url,
                            "job link now redirects to the general careers page")

    text = fold(html_to_text(body) or "")[:20000]
    if _CLOSED_PHRASES.search(text):
        return VerifyResult(LinkStatus.CLOSED, now, status_code, final_url, "page says the job is closed")
    if len(text) < 200:
        return VerifyResult(LinkStatus.UNKNOWN, now, status_code, final_url,
                            "page has almost no text (likely rendered by JavaScript)")
    return VerifyResult(LinkStatus.OPEN, now, status_code, final_url, "page loaded and looks open")


async def verify_ats_api(client: PoliteClient, source: str, board_token: str, job_id: str) -> VerifyResult | None:
    """Ask the ATS API directly whether one job still exists. None if the source has no such call."""
    now = datetime.now(UTC)
    if source == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}"
    elif source == "lever":
        url = f"https://api.lever.co/v0/postings/{board_token}/{job_id}?mode=json"
    else:
        return None
    try:
        r = await client.get(url)
    except httpx.HTTPError as exc:
        return VerifyResult(LinkStatus.UNREACHABLE, now, None, url, f"network error: {type(exc).__name__}")
    if r.status_code == 200:
        return VerifyResult(LinkStatus.OPEN, now, 200, url, f"{source} API still lists the job")
    if r.status_code == 404:
        return VerifyResult(LinkStatus.CLOSED, now, 404, url, f"{source} API no longer has the job")
    return VerifyResult(LinkStatus.UNKNOWN, now, r.status_code, url, f"{source} API returned {r.status_code}")


async def verify_url(client: PoliteClient, url: str) -> VerifyResult:
    now = datetime.now(UTC)
    if is_login_walled(url):
        return VerifyResult(LinkStatus.LOGIN_REQUIRED, now, None, url,
                            "login-walled site: not checked automatically, open it yourself")
    try:
        r = await client.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
    except httpx.HTTPError as exc:
        return VerifyResult(LinkStatus.UNREACHABLE, now, None, url, f"network error: {type(exc).__name__}")
    content_type = r.headers.get("content-type", "")
    body = r.text if "html" in content_type or "text" in content_type else None
    return classify_page(r.status_code, str(r.url), url, body)


async def verify_with_browser(url: str, timeout_ms: int = 20000) -> VerifyResult:
    """Playwright fallback for JavaScript-rendered pages. Requires `playwright install chromium`."""
    from playwright.async_api import async_playwright

    from scout_core.config import get_settings

    now = datetime.now(UTC)
    if is_login_walled(url):
        return VerifyResult(LinkStatus.LOGIN_REQUIRED, now, None, url, "login-walled site")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(user_agent=get_settings().http_user_agent)
            response = await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            html = await page.content()
            status = response.status if response else 0
            return classify_page(status, page.url, url, html)
        except Exception as exc:  # noqa: BLE001 - any browser failure means "could not verify"
            return VerifyResult(LinkStatus.UNREACHABLE, now, None, url, f"browser error: {type(exc).__name__}")
        finally:
            await browser.close()
