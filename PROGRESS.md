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

**Test totals:** 163 Python tests passing, `ruff` clean, web lint/typecheck/build clean.

### Not done / not verified yet (honest list)

- **Not run with real credentials:** Supabase login, Telegram sending, MongoDB Atlas. All code paths are tested
  with mocks or local equivalents. Waiting for your accounts (docs/SETUP.md).
- **Docker Compose and CI** files are written but **not executed** (Docker isn't installed on this machine and
  the repo isn't on GitHub yet).
- **Playwright browser fallback** for verification is written but not exercised in tests.
- **Playwright Test for the dashboard** (spec §4) not written yet; UI was checked with scripted screenshots.
- **Adzuna plug-in** not built (settings toggle exists; moves to Phase 2 with the key).
- Changing filter settings applies to **new** jobs only; already-seen jobs aren't re-evaluated yet.

### Key finding from the real run

YC-company boards give **high-quality but low-volume** results for a fresher in India: of 3,593 open roles,
only 103 are located in India and **5** are relevant, fresher-level and workable from India. Most YC startups
hire in the US and for experienced roles. Getting thousands of relevant jobs needs the Phase 2 sources
(Gmail alerts from LinkedIn / Naukri / Instahyre / Wellfound / Internshala / Cutshort, and India aggregator APIs)
and Indian company boards. Discovery currently finds boards on Greenhouse, Lever and Ashby only; many Indian
companies use Keka, Zoho Recruit or Darwinbox, which have no documented public job API.

## Phase 2: Gmail alerts + more sources (not started)
## Phase 3: Resume parsing + matching (not started)
## Phase 4: Tailoring + validation (not started)
## Phase 4b: Autofill Chrome extension (added by you on 2026-11-06; not started)
## Phase 5: Tracker, follow-ups, health, polish (not started)
