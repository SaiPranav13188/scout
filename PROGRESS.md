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

- **Not run with real credentials:** Supabase login, Telegram sending, MongoDB Atlas. All code paths are tested
  with mocks or local equivalents. Waiting for your accounts (docs/SETUP.md).
- **Docker Compose and CI** files are written but **not executed** (Docker isn't installed on this machine and
  the repo isn't on GitHub yet).
- **Playwright browser fallback** for verification is written but not exercised in tests.
- **Playwright Test for the dashboard** (spec §4) not written yet; UI was checked with scripted screenshots.
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

**Not yet:** dashboard and API are not hosted (they run on the laptop), so Telegram buttons and
`/today` etc. don't respond yet; Gmail alerts (Phase 2); MongoDB Atlas for raw payloads (runs on GitHub
currently keep raw payloads only for the duration of the run).

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
| Cloud Gmail runs | waiting for the ENCRYPTION_KEY GitHub secret (adding it was blocked by Claude Code's safety rules; command given to the user) |
| Aggregator APIs (Adzuna etc.) | not started |

## Phase 3: Resume parsing + matching (in progress)

| Item | Status |
|---|---|
| Gemini provider (structured JSON, untrusted-data wrapping, injection detection, retries, per-model fallback) | done; models chosen from the live model list for this key |
| Master resume v1: PDF text, contact details redacted locally, Gemini structuring, plain-code fact check | done; 41 skills, 3 projects, 0 unsupported items |
| Match score (skills overlap + embeddings + Gemini rubric), shown in dashboard, Best-fit sort, Telegram | done; 33 jobs scored so far |
| Labelling (apply/maybe/no) for the eval set | UI live; waiting for ~40 labels (0 so far) |
| Eval: precision/recall vs labels | built: `cli match-eval` (precision/recall/false positives per score band, agreement, AUC vs the plain role-fit baseline); waiting for labels |
| Resume Manager page (review/edit, upload PDF, versions) | done; checked in a browser against a local copy of resume v1 |
| Re-scoring when the resume changes | done: each score stores the profile fingerprint it used; outdated scores are refreshed after new jobs, within the per-run limit |
| Cloud scoring | waiting for GEMINI_API_KEY GitHub secret |
| Live database migration 0005 | **waiting for you** (blocked for me by Claude Code's safety rules; commands in "Next steps for you") |

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

## Phase 4: Tailoring + validation (not started)
## Phase 4b: Autofill Chrome extension (added by you on 2026-11-06; not started)
## Phase 5: Tracker, follow-ups, health, polish (not started)
