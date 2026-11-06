# Scout — Plan (reply to JOB_HUNTER_PROJECT.md §16)

Status: **Phase 1 approved and built** (see PROGRESS.md).

### Changes agreed on 2026-11-06 (after the first plan)

- **No manual company list.** Companies are discovered automatically: YC directory → company website →
  linked Greenhouse/Lever/Ashby board → board confirmed live via its API. Nothing is guessed from names.
  You can still add a "dream company" by pasting its board link.
- **Company trust check** added to Phase 1 (verified / unverified / caution / scam risk, with reasons).
- **Autofill** moves from optional Phase 6 to a required phase after resume parsing, built as a Chrome extension:
  fills forms from your saved profile, highlights every field for review, never clicks Submit, stops at CAPTCHA /
  OTP / login / legal declarations, and never touches LinkedIn Easy Apply or Wellfound forms.
- LinkedIn / Wellfound / Internshala / Instahyre / Cutshort jobs reach the dashboard through Gmail alerts
  (Phase 2), never by scraping or logging in. Clicking a job opens the original page.
- Migrations were consolidated into 3 files (extensions, schema, RLS) instead of 8; the tables are as below.
- `pg_trgm` dropped: fuzzy dedupe compares titles in Python among same-company candidates (indexed lookup).

---

## 1. Architecture summary

Monorepo:

```
scout/
  apps/web       Next.js (App Router, TS, Tailwind, shadcn/ui, TanStack Query, Motion)
  apps/api       FastAPI + async SQLAlchemy 2 + Alembic + Pydantic v2
  apps/worker    Celery workers + Celery Beat (same Python package as api, separate entrypoint)
  packages/shared  Shared Python domain code (normalize, dedupe, filters, schemas) + generated TS API types
  docs/          SETUP.md, EVALS.md, PLAN.md, ADRs
  infra/         docker-compose.yml, Dockerfiles, CI workflows
```

Data flow:

1. **Beat** schedules `ingest_source(source, company)` tasks (every 4h by default; value read from `settings`, not hardcoded).
2. **Ingestion worker** calls one `JobSource` plug-in (Greenhouse / Lever / Ashby in Phase 1). Raw response goes to Mongo `raw_jobs` as-is.
3. **Plain-code pipeline** (in `packages/shared`): `normalize -> dedupe -> hard filters (fresher / role) -> upsert into Postgres jobs`.
4. **Verify worker** (HTTP first, Playwright only if HTTP is inconclusive): sets `verified_at`, `is_open`.
5. **LangGraph pipeline** (Phase 3+): `parse_job -> score_match -> tailor_resume -> validate_resume -> draft_messages`, with a Postgres checkpointer so graph state survives restarts.
6. **FastAPI** serves the dashboard; **Telegram notifier** sends digests and alerts.

Interfaces (so pieces can be swapped):

- `JobSource.fetch() -> list[RawJob]`, `JobSource.parse(raw) -> list[NormalizedJob]`
- `LLMProvider` (single impl: Gemini via `google-genai`, model from `GEMINI_MODEL`)
- `Notifier` (Telegram first, Resend optional)
- `LinkVerifier` (HTTP impl, Playwright impl)

Reliability rules:

- Postgres is the source of truth. Redis only carries the queue, locks and rate-limit counters.
- Every task is idempotent and keyed by a natural id (`source:source_job_id`, `job_id:step`).
- On worker start, a `recover_pending` task re-enqueues anything Postgres says is unfinished (e.g. jobs with `status='discovered'` and no `verified_at`).
- Notifications are written to `notifications` with a unique `(type, dedupe_key)` *before* sending, so a retry can't double-send.
- Failures write an `agent_runs` row with `status='error'` and trigger a Telegram error alert (rate-limited).

### UI / UX direction (dashboard)

Goal: feels like a polished product (Linear / Vercel / Raycast tier), not a template.

- **Design system:** shadcn/ui primitives, custom tokens (dark-first, with a light theme), Geist Sans/Mono, 4px grid, subtle glass surfaces and grain, one accent colour.
- **Motion** (the `motion` library, formerly Framer Motion), used where it helps understanding:
  - staggered card entrance on the Today feed; layout animations when filters change or a job is skipped (the card collapses and the list reflows)
  - an animated score ring that counts up to the score, coloured by band
  - shared-element transition from a job card to the job detail drawer
  - drag-and-drop Kanban (dnd-kit) with spring physics on the Pipeline page
  - number tickers and sparklines on Agent health; a "live" pulse on running workers
  - skeleton shimmer while loading, optimistic updates with an undo toast ("Skipped · Undo")
  - a command palette (⌘K) to search jobs, jump between pages and run actions
  - keyboard shortcuts on Today: `j/k` move, `o` open, `s` skip, `a` mark applied
- **Accessibility:** respects `prefers-reduced-motion` (animations become instant), visible focus rings, WCAG AA contrast, full keyboard use.
- **Performance:** animations use only transform and opacity; list virtualization for long feeds; target Lighthouse ≥ 90.
- Responsive down to mobile (Today works one-handed: swipe right to open, left to skip).

---

## 2. Schema as an Alembic migration plan

Conventions: UUID PKs (`gen_random_uuid()`), `timestamptz` everywhere, `created_at`/`updated_at` on every table, snake_case. Enums are Postgres enums created in the migration that first uses them.

### 0001_extensions_and_enums
- `CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;`
- enums: `ats_type(greenhouse, lever, ashby, other)`, `application_status(discovered, qualified, resume_ready, applied, interview, rejected, offer, skipped)`, `run_status(running, success, error, partial)`, `notification_status(pending, sent, failed, suppressed)`, `outreach_channel(email, linkedin, other)`

### 0002_profile_settings
- `profile(id, name text, contact jsonb, preferences jsonb, mandatory_flags jsonb, created_at, updated_at)`
- `settings(key text PK, value jsonb, updated_at)`
- `saved_searches(id, site text, label text, url text, position int, created_at)`

### 0003_companies
- `companies(id, name, normalized_name, website, normalized_domain, description, founded_year int NULL, founders jsonb NULL, funding_stage text NULL, funding_info jsonb NULL, hq text NULL, size text NULL, career_url, linkedin_url, source, discovered_at)`
  - `UNIQUE(normalized_name)`, partial `UNIQUE(normalized_domain) WHERE normalized_domain IS NOT NULL`
- `company_watchlist(id, company_id FK, ats_type ats_type, board_token text, active bool default true, last_fetched_at, last_etag)`
  - `UNIQUE(ats_type, board_token)`

### 0004_jobs
- `jobs(id, company_id FK NULL, source text, source_job_id text, title, normalized_title, description, required_skills jsonb, preferred_skills jsonb, experience_min numeric NULL, experience_max numeric NULL, experience_unclear bool, fresher_confidence text, education text, location text, remote bool NULL, salary jsonb NULL, employment_type text, posted_at timestamptz NULL, deadline timestamptz NULL, apply_url text, canonical_apply_url text, verified_at timestamptz NULL, is_open bool NULL, dedupe_key text, duplicate_of uuid NULL FK jobs, embedding vector(N) NULL, status application_status default 'discovered', filter_reasons jsonb, first_seen_at, last_seen_at)`
  - `UNIQUE(source, source_job_id)`, index on `dedupe_key`, `posted_at DESC`, `status`, `canonical_apply_url`
  - trigram index on `normalized_title` for fuzzy repost detection
  - `N` (embedding dimension) set from the chosen Gemini embedding model in Phase 3 (column added then, not guessed now)

### 0005_scoring_and_resumes (schema created in Phase 1, used from Phase 3/4)
- `master_resume(id, file_url, parsed jsonb, version int, uploaded_at, is_active bool)`
- `job_scores(id, job_id FK, score int CHECK 0..100, breakdown jsonb, reasons jsonb, matched_skills jsonb, missing_skills jsonb, red_flags jsonb, model text, prompt_version text, created_at)` index `(job_id, created_at DESC)`
- `resumes_generated(id, job_id FK, master_resume_id FK, file_url_pdf, file_url_docx, content jsonb, validation_result jsonb, created_at)`

### 0006_applications_outreach
- `applications(id, job_id FK UNIQUE, status application_status, applied_at, platform, notes, follow_up_at, resume_id FK NULL)`
- `recruiters(id, company_id FK, name, role, profile_url, email_if_public, source)`
- `outreach(id, recruiter_id FK, job_id FK, channel outreach_channel, draft text, sent_by_me bool default false, sent_at, response, follow_up_at)` with `UNIQUE(recruiter_id, job_id)`

### 0007_ops
- `notifications(id, type, dedupe_key, payload jsonb, sent_at, status notification_status)` with `UNIQUE(type, dedupe_key)`
- `agent_runs(id, worker, task_id, started_at, finished_at, status run_status, counts jsonb, error text, retries int)`
- `audit_log(id, actor, action, entity, entity_id, before jsonb, after jsonb, created_at)` (spec §12)
- `secrets_encrypted(name PK, ciphertext bytea, updated_at)` for Gmail refresh token / Telegram token (Fernet key from env)

### 0008_rls
- Enable RLS on every table; a single `owner` role policy. The API connects as that role; the anon role gets nothing.

### MongoDB (no migrations; indexes created at startup)
- `raw_jobs`: index `(source, fetched_at)`
- `raw_alert_emails`: unique index on `gmail_message_id`
- `agent_traces`: index `(run_id, node)`, TTL index on `created_at` (e.g. 90 days, configurable)

---

## 3. Phase 1 tasks with estimates

| # | Task | Est. |
|---|---|---|
| 1 | Monorepo scaffold, uv (Python) + pnpm (web), lint/format (ruff, mypy, eslint, prettier) | 0.5 d |
| 2 | Docker Compose: api, worker, beat, redis, postgres+pgvector, mongo; `.env.example` | 0.5 d |
| 3 | GitHub Actions CI: lint + pytest (with service containers) + web typecheck/build | 0.5 d |
| 4 | SQLAlchemy models + Alembic migrations 0001–0008; seed script for `profile`/`settings` from a YAML I fill in from your data (no fake jobs) | 1 d |
| 5 | Single-user auth (Supabase Auth or a signed-session + password, see Q4) on every API route; rate limiting | 0.5 d |
| 6 | `JobSource` interface + Greenhouse, Lever, Ashby plug-ins (endpoints checked against official docs at implementation time); polite client (honest User-Agent, backoff, ETag where supported) | 1.5 d |
| 7 | Normalize + dedupe (source id, canonical URL, fuzzy company/title/location key, repost detection) | 1 d |
| 8 | Fresher/entry-level filter + role relevance (rules from `settings`, `experience_unclear` flag) | 1 d |
| 9 | Link verification (HTTP + ATS "closed" detection; Playwright fallback) | 1 d |
| 10 | Celery tasks + Beat schedule from `settings`, idempotency, `agent_runs`, startup recovery | 1 d |
| 11 | Web: design system, app shell, sidebar, ⌘K palette, theme, motion primitives | 1.5 d |
| 12 | Web: Today page (unscored): cards, filters, search, Open / Skip / Mark applied, undo, keyboard shortcuts | 1.5 d |
| 13 | Telegram bot: chat-id allowlist, daily digest of raw new jobs, `/today` `/pause` `/resume` | 1 d |
| 14 | Tests: dedupe, filters (incl. the tricky cases in §13.2), parsers on recorded real payloads, link verification; Playwright Test smoke for Today | 1.5 d |
| 15 | `docs/SETUP.md`, `PROGRESS.md`, first numbers in `docs/EVALS.md` (filter + dedupe) | 0.5 d |
| | **Total** | **~14 working days** |

---

## 4. Questions

See the chat reply. Answers will be recorded in `docs/DECISIONS.md`.

## 5. Risks

See the chat reply.
