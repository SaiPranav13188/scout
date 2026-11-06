# Setup

Everything Scout needs, in the order you'll need it. Each step says which environment variable it fills.
Values go in `.env` at the repo root (server side) and `apps/web/.env.local` (dashboard). Never commit either file.

Prices and free-tier limits change. Check each provider's pricing page before relying on it; this guide does not
quote numbers it hasn't verified.

---

## 0. Run locally right now (no accounts needed)

This runs the real pipeline against real public job boards on your laptop, with an embedded Postgres.

```bash
# Python 3.12 + deps (uv installs Python itself if needed)
uv sync

# Start the local database and create the schema
uv run python scripts/devdb.py --db scout          # prints the DATABASE_URL to use
cp .env.example .env                                # then set DATABASE_URL to the printed value
                                                    # and set DEV_AUTH_TOKEN to any long random string
(cd packages/shared && uv run alembic upgrade head)

# Find companies and their job boards (YC directory -> company websites -> confirmed boards)
set -a; source .env; set +a
uv run python -m scout_worker.cli discovery        # ~25 min for 800 companies
uv run python -m scout_worker.cli ingest           # ~2-3 min for ~300 boards

# API
uv run uvicorn scout_api.main:app --port 8000 --reload

# Dashboard (second terminal)
cd apps/web
cp .env.example .env.local    # set NEXT_PUBLIC_DEV_AUTH_TOKEN to the same value as DEV_AUTH_TOKEN
npm install
npm run dev                   # http://localhost:3000
```

`DEV_AUTH_TOKEN` is a local shortcut that skips Supabase login. The API refuses to start if it is set while
`ENV=production`, and production web builds ignore it.

---

## 1. Supabase (database, login, file storage)

Fills: `DATABASE_URL`, `SUPABASE_URL`, `OWNER_EMAIL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`

1. Sign up at https://supabase.com and create a project. Pick the region closest to India (e.g. Mumbai, if offered).
   Save the database password you choose.
2. **Project URL and publishable key:** in the project's API settings, copy the Project URL into both
   `SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_URL`, and the **publishable** key into
   `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`. Never put the secret/service-role key in the web app.
3. **JWT signing keys:** the API verifies login tokens with the project's public keys (JWKS). Make sure the
   project uses the asymmetric JWT signing keys (see https://supabase.com/docs/guides/auth/jwts). If the JWKS
   endpoint `https://<project>.supabase.co/auth/v1/.well-known/jwks.json` returns no keys, switch to signing keys.
4. **Your login:** in Authentication, add a user with your email and a strong password. Then **turn off
   new sign-ups** so nobody else can create an account. Put that email in `OWNER_EMAIL`; the API rejects every
   other account even if one existed.
5. **Database connection string:** from the project's Connect panel, copy the Postgres connection string and
   change the scheme to `postgresql+asyncpg://`. Put it in `DATABASE_URL`.
6. Run the migrations against it:
   `cd packages/shared && DATABASE_URL=... uv run alembic upgrade head`
   This enables `pgvector` and turns on Row Level Security for every table, so Supabase's automatic REST API
   cannot read your data.
7. Note: Supabase may pause inactive free projects. The workers use the database every few hours, but check the
   current policy on their pricing page.

## 2. Telegram bot

Fills: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET`

1. In Telegram, open **@BotFather**, send `/newbot`, choose a name and username. Copy the token into
   `TELEGRAM_BOT_TOKEN`. Treat it like a password.
2. Send any message to your new bot. Then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy `message.chat.id` into
   `TELEGRAM_CHAT_ID`. Only this chat is ever messaged or obeyed.
3. Make up a random secret (letters, digits, `_` and `-` only, up to 256 characters) for
   `TELEGRAM_WEBHOOK_SECRET`.
4. Once the API is deployed at a public HTTPS URL, register the webhook:
   ```bash
   curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
     -d "url=https://<your-api>/telegram/webhook" \
     -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
   ```
   Telegram then sends that secret in the `X-Telegram-Bot-Api-Secret-Token` header, and the API rejects
   requests without it. Commands: `/today`, `/stats`, `/pause`, `/resume`.
5. Digests work without the webhook (they are outgoing). The webhook is only needed for buttons and commands.

## 3. Redis

Fills: `REDIS_URL`

Locally, `docker compose up redis` (or any Redis). In the cloud, use a managed Redis or run it next to the worker.
Celery polls Redis constantly; on per-command-priced Redis plans this can use up the quota quickly, so check the
plan's limits or run Redis on the worker machine.

## 4. MongoDB Atlas (raw payloads and logs)

Fills: `MONGO_URL`

1. Create a free cluster at https://www.mongodb.com/atlas. Create a database user and allow access from your
   server's IP.
2. Copy the connection string into `MONGO_URL`.
Without `MONGO_URL`, raw payloads are written to `.devdata/raw/` (fine locally, not for the server).

## 5. Encryption key

Fills: `ENCRYPTION_KEY` (used from Phase 2 to encrypt the Gmail refresh token)

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 6. Hosting (decide before deploying)

- **Dashboard:** Vercel. Import the repo, set the root directory to `apps/web`, and add the `NEXT_PUBLIC_*`
  variables (without `NEXT_PUBLIC_DEV_AUTH_TOKEN`).
- **API + worker + beat:** they must run around the clock, which free tiers that sleep cannot do. Options:
  a small paid Render instance (web service for the API plus a background worker), or one small AWS EC2/
  Lightsail VM running `docker compose up -d api worker beat redis`. Confirm current prices first.
- Run **exactly one** `beat` process.

## Later phases

| Phase | What | Variables |
|---|---|---|
| 2 | Gmail OAuth (read-only), Google Cloud project, test user = you | added in Phase 2 |
| 2 | Adzuna API key (personal use allowed; must show an "Adzuna" credit link) | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` |
| 3 | Gemini API key from Google AI Studio | `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_EMBEDDING_MODEL` |
| 3 | Langfuse keys | added in Phase 3 |
