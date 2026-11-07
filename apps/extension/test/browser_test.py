"""Run the real content script on test pages in Chromium (Playwright). `chrome.*` is stubbed.

  uv run --with playwright python apps/extension/test/browser_test.py
"""
import base64
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
SRC = HERE.parent / "src"
PROFILE = {"version": 1, "first_name": "Jane", "last_name": "Example", "full_name": "Jane Example", "email": "jane@example.com",
           "phone": "+91 98765 43210", "linkedin": "https://linkedin.com/in/jane", "github": "https://github.com/jane",
           "portfolio": "https://jane.dev", "location": "Bengaluru", "school": "Bennett University", "degree": "B.Tech",
           "discipline": "CSE", "graduation_year": "2026", "gpa": "8.3", "years_experience": "0"}
PDF = {"name": "Jane_Resume.pdf", "base64": base64.b64encode(b"%PDF-1.4 test").decode()}
STORED = json.dumps({"profile": PROFILE, "resumeFile": PDF})
STUB = ("window.__results = [];\n"
        f"window.chrome = {{ storage: {{ local: {{ get: async () => ({STORED}) }} }},\n"
        "  runtime: { sendMessage: (m) => window.__results.push(m.result) } };")


def run(page, url_or_html, *, html=None):
    page.add_init_script(STUB)
    if html is not None:
        page.route(url_or_html, lambda r: r.fulfill(status=200, content_type="text/html", body=html))
    page.goto(url_or_html)
    page.add_script_tag(path=str(SRC / "fields.js"))
    page.add_script_tag(path=str(SRC / "content.js"))
    page.wait_for_function("window.__results.length > 0")
    return page.evaluate("window.__results[0]")


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else None
    fails = []
    check = lambda ok, what: (print(("PASS " if ok else "FAIL ") + what), ok or fails.append(what))  # noqa: E731
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = b.new_page()
        res = run(page, "https://boards.greenhouse.io/acme/jobs/1", html=(HERE / "fixture_form.html").read_text())
        v = lambda sel: page.eval_on_selector(sel, "e => e.value")  # noqa: E731
        check(res["status"] == "filled", f"form filled ({res})")
        check(v("#first_name") == "Jane" and v("#last_name") == "Example" and v("#email") == "jane@example.com", "name and email")
        check(v("#phone") == "already typed", "never overwrites what is already there")
        check(page.eval_on_selector("#resume", "e => e.files.length && e.files[0].name") == "Jane_Resume.pdf", "resume attached")
        check(page.eval_on_selector("#cover", "e => e.files.length") == 0, "cover letter left for me")
        check(v("input[name='urls[LinkedIn]']") == PROFILE["linkedin"] and v("input[name='urls[Portfolio]']") == PROFILE["portfolio"], "links")
        check(v("#school") == "77", "school chosen in select")
        check(v("#degree") == "", "degree left empty when no option really matches (B.Tech is not 'Bachelor of Arts')")
        check(v("#grad") == "2026", "graduation year")
        check(v("#auth") == "" and v("#ctc") == "" and v("#why") == "", "work authorisation, salary, written answer untouched")
        check(page.eval_on_selector("#consent", "e => e.checked") is False, "consent box untouched")
        check(page.evaluate("window.__cityState") == "Bengaluru", "React-style controlled input received the value")
        check(page.evaluate("!window.__submitted"), "form NOT submitted")
        check("dashed" in page.eval_on_selector("#ctc", "e => e.style.outline"), "my-call fields outlined amber")
        check("solid" in page.eval_on_selector("#email", "e => e.style.outline"), "filled fields outlined green")
        check(page.is_visible("#scout-autofill-panel"), "review panel shown")
        page.click("#scout-autofill-panel button:has-text('Undo fill')")
        check(v("#first_name") == "" and page.eval_on_selector("#resume", "e => e.files.length") == 0, "undo restores the form")

        page2 = b.new_page()
        res = run(page2, "https://jobs.lever.co/acme/2/apply", html="<form><label for=e>Email</label><input id=e type=email>"
                  "<iframe src='https://www.google.com/recaptcha/api2/anchor'></iframe></form>")
        check(res["status"] == "stopped" and page2.eval_on_selector("#e", "e => e.value") == "", "stops on CAPTCHA, fills nothing")
        page3 = b.new_page()
        res = run(page3, "https://www.linkedin.com/jobs/view/1", html="<form><label for=e>Email</label><input id=e type=email></form>")
        check(res["status"] == "blocked" and page3.eval_on_selector("#e", "e => e.value") == "", "never runs on LinkedIn")
        page4 = b.new_page()
        res = run(page4, "https://jobs.ashbyhq.com/acme/login", html="<form><label for=e>Email</label><input id=e type=email><input type=password></form>")
        check(res["status"] == "stopped", "stops on a login page")
        b.close()
    print(f"\n{'ALL PASSED' if not fails else f'{len(fails)} FAILED'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
