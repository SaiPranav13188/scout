# Progress

## Phase 1: Foundation + discovery (in progress, code complete locally)

Last updated: 2026-11-06

### Done and tested

| Item | Evidence |
|---|---|
| Monorepo (`apps/web`, `apps/api`, `apps/worker`, `packages/shared`, `docs`) | uv workspace + Next.js 16 app |
| Schema: 3 Alembic migrations (extensions, all tables, RLS on every table) | upgrade → downgrade → upgrade clean; `alembic check` reports no drift |
| Greenhouse / Lever / Ashby plug-ins | parsers tested on real payloads recorded from Razorpay, FamPay, Bolna |
| Automatic company discovery (YC directory → company website → linked board → board confirmed live) | real run: 800 companies scanned, 321 boards confirmed, 24 site errors recorded |
| Normalize + dedupe (source id, canonical URL, fuzzy company/title/location, reposts) | unit + DB tests; real run 119 duplicates |
| Fresher filter, role relevance, location eligibility (India / worldwide remote) | 44 fresher tests incl. all spec tricky cases; real-run review in docs/EVALS.md |
| Company trust check (ATS board, YC, domain age via RDAP, scam phrases) | tests incl. real false-positive regressions; 0/3,593 false alarms after fixes |
| Link verification (HTTP + ATS API; login-walled sites never fetched; Playwright fallback written) | tests against a real local HTTP server |
| Ingestion: idempotent, closes jobs that leave a board, raw payloads kept | real run: 3,593 jobs from 227 boards in ~2.5 min; re-run adds 0 |
| Scheduler: editable times, slots tracked in Postgres, missed slots catch up, retries capped | tests |
| Telegram: digest, error alerts, `/today` `/stats` `/pause` `/resume`, Skip/Applied buttons, chat allowlist, webhook secret | tests with mocked Telegram API; never double-sends (tested incl. failure + retry) |
| API: Supabase JWT (JWKS) auth, owner-only, rate limit, audit log | tests with real ES256 tokens (expired, wrong issuer, wrong user rejected) |
| Dashboard: Today feed, details panel, ⌘K palette, keyboard shortcuts, undo, swipe, Startups, Saved searches, Agent health, Settings, login | lint + typecheck + production build pass; screenshots reviewed desktop + mobile; 0 console errors |

**Test totals:** 240 Python tests passing (Phase 1; 265 as of 2026-11-07), `ruff` clean, web lint/typecheck/build clean.

### Not done / not verified yet (honest list)

- **MongoDB Atlas** not connected (raw payloads live only for the duration of a run). Supabase, Telegram,
  Render and Vercel are live (see "Hosting, verified 2026-11-08" below).
- **Docker Compose** is written but **not executed** (Docker isn't installed on this machine). Production
  doesn't use it, so it stays a local convenience. CI runs on GitHub (see below).
- **Playwright browser fallback** for verification is written but not exercised in tests.
- **Adzuna plug-in** not built (settings toggle exists; moves to Phase 2 with the key).
- Changing filter settings applies to **new** jobs only; already-seen jobs aren't re-evaluated yet.

### Live setup (2026-11-06, evening)

| Item | Status |
|---|---|
| Supabase project `scout` (Mumbai, free) | created via MCP; schema 0003 applied; RLS on all 17 tables; public REST API returns nothing (checked); sign-ups disabled; 1 owner user |
| Telegram bot `@scout_pranav_jobs_bot` | live; test message, evening summary and instant alerts delivered |
| Cloud schedule (free) | public repo github.com/SaiPranav13188/scout; GitHub Actions `Scout tick` every ~10 min, `Scout discovery` weekly, monthly keepalive; secrets stored encrypted |
| CI on GitHub | passing (Python tests against real Postgres + dashboard lint/typecheck/build) |
| Instant alerts | every new relevant job, grouped, sent once (marked before send, unmarked on failure); quiet hours 22:30-07:30 |

**Decisions you made:** everything free; public repo (spec file with personal details kept out of it);
instant alerts for every new relevant job; quiet hours kept. Production uses GitHub Actions instead of an
always-on Celery worker (Celery remains for local/Docker use).

**Timing, measured:** a check of 227 boards takes ~2.5 min from India and ~5.5 min on GitHub's US runners
(database round trips to Mumbai). GitHub may delay scheduled runs when busy, so alerts arrive within roughly
10-20 minutes of a job appearing on a company board, not seconds.

**Bug found in the live run and fixed:** vector.co's website links to 97 other companies' job boards, and
discovery credited all of them to "Vector". Discovery now ignores sites linking to more than 2 boards and
checks the Greenhouse board owner's name. The wrong rows were removed and jobs rebuilt (with your approval).
Also fixed: remote jobs whose title names another country ("US Remote", "Spain") were shown as worldwide.

**Not yet:** MongoDB Atlas for raw payloads (runs keep raw payloads only for the duration of the run).

### Hosting, verified 2026-11-08

| Piece | Where | Checked |
|---|---|---|
| API | Render free web service `scout-api-bxpx.onrender.com` (Singapore) | `/healthz` 200; production env vars set; CORS allows only the Vercel dashboard and localhost |
| Dashboard | Vercel `scout-ten-bice.vercel.app` | page 200; `NEXT_PUBLIC_API_URL` and Supabase keys set |
| Telegram | webhook → Render `/telegram/webhook` | no delivery errors; a `/stats` update through the live webhook returned 200; a wrong secret returns 403 |
| Schedule | Supabase pg_cron → `POST /internal/tick` every 10 min (also keeps Render awake); GitHub Actions as backup | running |

### Key finding from the real run

YC-company boards give **high-quality but low-volume** results for a fresher in India: of 3,593 open roles,
only 103 are located in India and **5** are relevant, fresher-level and workable from India. Most YC startups
hire in the US and for experienced roles. Getting thousands of relevant jobs needs the Phase 2 sources
(Gmail alerts from LinkedIn / Naukri / Instahyre / Wellfound / Internshala / Cutshort, and India aggregator APIs)
and Indian company boards. Discovery currently finds boards on Greenhouse, Lever and Ashby only; many Indian
companies use Keka, Zoho Recruit or Darwinbox, which have no documented public job API.

## Phase 2: Gmail alerts + more sources (in progress)

| Item | Status |
|---|---|
| Gmail OAuth, read-only (`gmail.readonly`), token + client stored encrypted (Fernet) in Postgres | done; connected 2026-11-06 |
| Parsers: LinkedIn, Wellfound, Internshala, Instahyre (one per sender) | done; tested on 34 real alert emails (local-only, never published) + format tests in CI |
| Gmail check every 10 min (only emails newer than the last processed), same filters/dedupe/alerts | done; first run: 26 alert emails -> 153 jobs, feed 5 -> 59 relevant jobs |
| Naukri | no parser: the inbox has no Naukri job alerts (only quizzes and reminders) |
| Cloud Gmail runs | ENCRYPTION_KEY GitHub secret set (checked 2026-11-08) |
| Aggregator APIs (Adzuna etc.) | not started |

## Phase 3: Resume parsing + matching (in progress)

| Item | Status |
|---|---|
| Gemini provider (structured JSON, untrusted-data wrapping, injection detection, retries, per-model fallback) | done; models chosen from the live model list for this key |
| Master resume v1: PDF text, contact details redacted locally, Gemini structuring, plain-code fact check | done; 41 skills, 3 projects, 0 unsupported items |
| Match score (skills overlap + embeddings + Gemini rubric), shown in dashboard, Best-fit sort, Telegram | done; 33 jobs scored so far |
| Labelling (apply/maybe/no) for the eval set | dashboard, Telegram 👍🤔👎, or Claude via the MCP server (`jobs_to_label`); waiting for ~40 labels (0 so far). 79 jobs scored, only 4 with full descriptions: label those first |
| Eval: precision/recall vs labels | built: `cli match-eval` (precision/recall/false positives per score band, agreement, AUC vs the plain role-fit baseline, and an ablation: skills-only, embeddings-only, rubric-only and each pair, rebuilt offline from stored score parts); waiting for labels |
| Resume Manager page (review/edit, upload PDF, versions) | done; checked in a browser against a local copy of resume v1 |
| Re-scoring when the resume changes | done: each score stores the profile fingerprint it used; outdated scores are refreshed after new jobs, within the per-run limit |
| Cloud scoring | GEMINI_API_KEY set on GitHub and Render (checked 2026-11-08) |
| Live database migration 0005 | applied (live database at 0005, checked 2026-11-08) |

**Resume Manager (2026-11-07):** every section of the parsed resume is editable (contact, headline,
skills by group, projects with tech and bullets, experience, education, achievements, certifications).
Saving creates a new version; earlier versions are never changed and any of them can be made active again.
A live fact check marks every skill, tool, number and date that isn't in the uploaded PDF (amber), and
saving is still allowed because I'm the source of truth. The redacted PDF text is stored with each version
for this check (and for the Phase 4 validator); contact details stay out of it and are never sent to Gemini.
Uploading a new PDF from the page parses it with Gemini (5 MB limit; scanned/encrypted PDFs get a clear
error). An edit started from an older version is refused instead of overwriting newer work.

Fixed along the way: (1) the feed joined every score row of a job, so a re-scored job would have appeared
twice; the feed, digests and alerts now use the newest score. (2) The fact check matched numbers as
substrings, so an invented "3" passed because "8.3" was in the PDF; numbers now match whole. (3) The phone
pattern dropped an opening bracket ("+91) 9030…") and kept the same number twice in two formats; future
uploads keep one. Your v1 still shows both; delete the extra one when you review.

**Found while running it:** the free tier has a daily request limit per model (gemini-3.5-flash hit it after
~60 calls); gemini-3.8-flash was often overloaded (503); the SDK's hidden retries turned one call into ~9
minutes (now off). LinkedIn alert jobs are title-only, so their scores (43-60) are rough; jobs with full
descriptions score far more meaningfully.

### Feed: only confirmed entry-level jobs (2026-11-07)

The default feed, stats, Telegram digest and instant alerts now exclude jobs whose experience level is
unclear (title-only alert emails); an "Experience unclear" chip shows them on request. Alert emails no
longer stamp the email's arrival time as the posting time ("Posted date not given" unless the site states
it). LinkedIn alerts from a search filtered to Entry level / Internship are trusted as entry-level.

### Stricter fresher filter + India boards (2026-11-07, afternoon)

Your rule: never show a job that needs experience. Changes, each checked on real data:
- A requirement marked "preferred" above 1 year now drops the job (unless the posting also invites freshers).
- "3–6 years in data science", "2+ years building…" and "8-year experience" are now read as requirements
  (before, the word "experience" had to be nearby). Replayed over 19,063 saved real postings: 627 more
  experienced jobs dropped; a random sample of 30 were all real requirements.
- Titles: "SDE3", "Intermediate" (GitLab's mid-level grade), "mid-level", "experienced", "level 2/3" drop;
  "Engineer I", "Developer I" count as entry level; intern-to-full-time roles are shown (were hidden).
- Jobs the rules still can't decide (no years stated) are read by Gemini, which must quote the exact sentence;
  plain code checks the quote is in the posting and actually says what Gemini claims. First run: 22 of 27
  dropped with quotes like "4–6 years of building and operating production backend systems", 5 stay hidden.
- `cli recheck-feed` re-applies the rules to jobs already stored (20 experienced jobs removed today).
- 52 India-hiring companies' public boards added (Paytm, Meesho, Databricks, Stripe, Okta, MongoDB, Sarvam AI,
  CRED, Groww, Zeta, HighRadius, InMobi, Hevo Data…; `scout_core/seeds_india.py`, `cli add-india-boards`):
  279 boards, 12,673 jobs scanned.

Honest result: company boards post few explicit fresher roles; 3 open India roles are confirmed for
freshers today. Most fresher hiring is on LinkedIn / Naukri / Internshala. LinkedIn alert emails carry only
a title, so Scout can only trust them when the alert's search is filtered to Entry level / Internship.
Seven such searches are now on the Saved searches page (LinkedIn and Naukri, India, last 24 h).

### Next steps for you

1. Apply migration 0005 to the live database (adds `job_scores.profile_hash`; one active resume version):
   `cd packages/shared && set -a && source ../../.env && set +a && uv run alembic upgrade head`
2. Upgrade resume v1 (stores its redacted text for the fact check; marks the 38 existing scores as made from
   v1 so they aren't re-scored). Dry run first, then apply:
   `uv run python -m scout_worker.cli resume-backfill private/resume.pdf` and again with `--apply`
3. Then push (`git push`): the cloud schedule runs the code on GitHub, and the new scoring code needs that column.
4. Open Resume in the dashboard, fix what the parser got wrong (e.g. the duplicate phone, "redis with BullMQ
   queue." as a tool), and save once.
5. Label ~40 jobs (apply / maybe / no), then `uv run python -m scout_worker.cli match-eval`.

## Phase 4: Tailoring + validation (done 2026-11-07)

| Item | Status |
|---|---|
| `tailor_resume`: Gemini may only reorder skills/projects, choose up to 4 bullets each, lightly reword | done (`scout_core/tailor.py`) |
| `validate_resume` (plain code): skills, project/role names, tools in bullets, numbers, achievements, certifications must be in the master data; job keywords that aren't true for me fail it | done; 7 planted-fake-claim tests |
| Retry once with the validator's errors, then fall back to the untailored master resume | done, tested |
| PDF (ATS-safe, single column, standard fonts, plain-text links) + DOCX; one-page fitting drops least important lines | done (`render_resume.py`) |
| Versioning: every generation is a new `resumes_generated` row; files rendered from the stored version | done |
| Dashboard: "Tailored resume" in each job (make / PDF / DOCX / fact-check result); history on the Resume page | done |
| Automatic for Top matches (85+) after scoring, 3 per run | done |
| Telegram "Resume" button sends the PDF (made on the spot if needed) | done, tested |
| Eval on real data (`cli tailor-eval`) | 6 real AI jobs: 6 tailored first try, 0 unsupported claims, 6/6 one page, 6/6 text extraction OK |

**Message drafts (spec §6.4 draft_messages):** a cover note (≤120 words) and a recruiter message (≤60 words) per
job, on request from the job panel, with the same fact check as tailored resumes (a draft with a tool, number or
claim not in my resume is retried once, then not shown). Drafts only; logged in the audit log; I send them myself.
Real run: Observe.AI "AI Agent Engineer", passed on the first try.

## Phase 4: Tailoring + validation (spec heading kept for reference)
## Phase 4b: Autofill Chrome extension (done 2026-11-07)

`apps/extension` (Manifest V3; install steps in its README). Fills plain facts from a profile exported from my
reviewed resume (Resume page → "Autofill profile"), attaches the chosen resume PDF, outlines every filled field
green and every "your call" question amber (work authorisation, visa, salary, notice period, demographics,
declarations, consent, cover letters, written answers: never filled). Never overwrites, never clicks or submits,
stops on CAPTCHA / login / OTP, never runs on LinkedIn or Wellfound, runs only when I click (activeTab, no host
permissions), profile stays in the browser, Undo restores the form.
Tests: 7 rule tests (`node --test`, in CI) and 20 real-Chromium checks on Greenhouse-style, CAPTCHA, login and
LinkedIn test pages (`apps/extension/test/browser_test.py`): all pass; the unpacked extension loads in Chromium.
## Phase 5: Tracker, follow-ups, health, polish (2026-11-07)

| Item | Status |
|---|---|
| Pipeline page: Applied / Interview / Offer / Rejected, notes, follow-up date, move, undo, open job, "resume you used" | done; checked in a browser |
| Follow-up reminders: 7 days after "applied" (setting `notifications.follow_up_days`), due ones named in the evening summary and on the page | done, tested |
| Agent health: per worker last success, next run, 24 h runs / failures / retries, last error; Gemini calls and tokens (24 h) | done; checked in a browser |
| Settings, evening summary, error alerts (one per worker per hour), restart-safe slots, no duplicate notifications | done earlier (Phase 1-2), tested |
| Langfuse tracing | built (every Gemini call); keys set on GitHub and Render |
| Load / worker-crash tests (spec §13.8) | partly: retry-the-same-slot and failed-send-retry tests exist; no Redis restart test (production runs without Redis) |

## Dashboard end-to-end test (Playwright Test, 2026-11-08)

`cd apps/web && npx playwright test` (spec: `apps/web/e2e/dashboard.spec.ts`). Its webServer,
`e2e/stack.py`, builds a throwaway Postgres (`scout_e2e`, emptied and re-seeded each run), the API and the
dashboard, so real data is never touched. 11 tests: Today, the job panel (tailored resume PDF download, drafts),
Pipeline (move a card, saved in the API), Resume (review → new version, autofill profile), Agent health,
Settings (score bands), and no sideways scroll at phone width on all five pages; every test also fails on an
uncaught page error. 11/11 pass locally (twice in a row; ports freed afterwards). Runs in CI as the `e2e` job,
with trace and screenshots uploaded on failure. (Ported from the earlier Python Playwright script, which found
the Agent health phone-overflow bug on 2026-11-07.)

## MCP server (2026-11-08)

`apps/mcp` lets Claude (Claude Code in this repo via `.mcp.json`, or Claude Desktop) use Scout: `top_jobs`,
`job_details`, `stats`, `pipeline`, `jobs_to_label` (read) and `set_job_status`, `label_job` (write, audited as
`claude-mcp`). Same feed rules and services as the dashboard and Telegram; posting text comes back inside
untrusted markers. Local stdio only, nothing exposed. 5 tests on real Postgres; checked over stdio against the
live database (stats, top jobs, label picker, bad-id error). Writing the tests found a bug that was fixed:
relabelling through MCP wiped the saved note.

## Phase 7 (AWS / Terraform): removed

Removed from the plan on 2026-11-07 at your request: you already have a project deployed on AWS, and Scout runs fully in the cloud for free (GitHub Actions, Render, Vercel, Supabase).
