"""Link verification against a real local HTTP server (pytest-httpserver)."""

import httpx
import respx

from scout_core.http import PoliteClient
from scout_core.verify import LinkStatus, classify_page, is_login_walled, verify_ats_api, verify_url

OPEN_PAGE = "<html><body><h1>Backend Engineer</h1><p>" + ("We build things. " * 30) + "</p><a>Apply</a></body></html>"


async def _verify(httpserver, path, **resp):
    httpserver.expect_request(path).respond_with_data(**resp)
    async with PoliteClient(min_interval_per_host=0, max_retries=0) as c:
        return await verify_url(c, httpserver.url_for(path))


async def test_open_page(httpserver):
    r = await _verify(httpserver, "/jobs/1", response_data=OPEN_PAGE, content_type="text/html")
    assert r.status == LinkStatus.OPEN and r.is_open is True and r.verified


async def test_404_is_closed(httpserver):
    r = await _verify(httpserver, "/jobs/2", response_data="gone", status=404, content_type="text/html")
    assert r.status == LinkStatus.CLOSED and r.is_open is False


async def test_closed_wording_is_closed(httpserver):
    page = "<html><body>" + ("Acme careers. " * 30) + "<p>This job is no longer accepting applications.</p></body></html>"
    r = await _verify(httpserver, "/jobs/3", response_data=page, content_type="text/html")
    assert r.status == LinkStatus.CLOSED


async def test_js_shell_is_unknown_not_open(httpserver):
    page = "<html><body><div id='root'></div><script src='/app.js'></script></body></html>"
    r = await _verify(httpserver, "/jobs/4", response_data=page, content_type="text/html")
    assert r.status == LinkStatus.UNKNOWN and r.is_open is None and not r.verified


async def test_403_is_login_required(httpserver):
    r = await _verify(httpserver, "/jobs/5", response_data="no", status=403, content_type="text/html")
    assert r.status == LinkStatus.LOGIN_REQUIRED and not r.verified


async def test_redirect_to_generic_careers_is_closed(httpserver):
    httpserver.expect_request("/careers/jobs/99").respond_with_data("", status=302, headers={"Location": "/careers"})
    httpserver.expect_request("/careers").respond_with_data(OPEN_PAGE, content_type="text/html")
    async with PoliteClient(min_interval_per_host=0, max_retries=0) as c:
        r = await verify_url(c, httpserver.url_for("/careers/jobs/99"))
    assert r.status == LinkStatus.CLOSED


async def test_unreachable_host():
    async with PoliteClient(min_interval_per_host=0, max_retries=0) as c:
        r = await verify_url(c, "http://127.0.0.1:9/never")
    assert r.status == LinkStatus.UNREACHABLE and not r.verified


async def test_login_walled_sites_are_never_fetched():
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*linkedin\.com.*").mock(return_value=httpx.Response(200))
        async with PoliteClient(min_interval_per_host=0) as c:
            r = await verify_url(c, "https://www.linkedin.com/jobs/view/123")
        assert route.call_count == 0
    assert r.status == LinkStatus.LOGIN_REQUIRED
    assert is_login_walled("https://wellfound.com/jobs/1")
    assert not is_login_walled("https://jobs.lever.co/acme/1")


@respx.mock
async def test_ats_api_check():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs/1").mock(return_value=httpx.Response(200, json={}))
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs/2").mock(return_value=httpx.Response(404))
    async with PoliteClient(min_interval_per_host=0, max_retries=0) as c:
        assert (await verify_ats_api(c, "greenhouse", "acme", "1")).status == LinkStatus.OPEN
        assert (await verify_ats_api(c, "greenhouse", "acme", "2")).status == LinkStatus.CLOSED
        assert await verify_ats_api(c, "ashby", "acme", "3") is None


def test_stray_404_text_does_not_close():
    page = "<html><body>" + ("Ticket 404 in our tracker is about onboarding. " * 10) + "</body></html>"
    assert classify_page(200, "https://a.com/jobs/1", "https://a.com/jobs/1", page).status == LinkStatus.OPEN
