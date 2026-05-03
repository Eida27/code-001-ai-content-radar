# AI News Radar

A small Python worker for monitoring trusted AI sources, scoring updates, generating X draft suggestions through OpenRouter, and sending Discord review alerts. The MVP keeps posting manual.

## Local Setup

From a fresh checkout on Windows PowerShell, using a repository-root virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
python -m pip install --upgrade pip
python -m pip install -r worker/requirements.txt
copy .env.example .env
python -m worker.main
```

If you are already inside the `worker` directory, use:

```powershell
cd worker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

The `.venv/` directory is intentionally ignored by git.

## Railway Cron Notes

Railway cron schedules are evaluated in UTC. The minimum frequency is 5 minutes, and a 15 minute schedule is the recommended MVP default:

```text
*/15 * * * *
```

The worker is designed to finish before the next schedule and close HTTP resources. If a previous Railway cron execution is still active, Railway can skip the next scheduled run, so the worker must remain idempotent and time-bounded.

Recommended Railway start command when the service root is `worker`:

```bash
python main.py
```

The worker service root should be `worker` so Railway can read
`worker/requirements.txt`, `worker/main.py`, and `worker/railway.toml`.
That config uses Railpack, `python main.py`, `*/15 * * * *`, and restart
policy `NEVER`. Do not add a Dockerfile unless Railpack detection fails or the
worker later needs custom system packages.

## Production Readiness

Before enabling Railway cron, run this checklist from the repository root:

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m compileall worker
.\.venv\Scripts\python -m worker.production_check
.\.venv\Scripts\python -m worker.freshness_audit
```

Then confirm the Railway service root is `worker`, add the same secrets and
settings from `.env` as Railway variables, and enable the cron schedule. The
production check is read-only: it validates environment, Supabase schema/RLS,
quota headroom, duplicate protection, OpenRouter key/model availability,
Discord webhook validity, and RSS freshness without generating drafts or
posting Discord messages.

Production defaults are free-first: `ALLOW_PAID_FALLBACK=false`,
`MAX_PAID_FALLBACKS_PER_RUN=0`, and `MAX_AI_CALLS_PER_RUN=4`. If you
intentionally enable paid fallback, keep a low per-run cap and treat the
production check warning as a cost reminder.

## Verification

Run unit tests from the repository root:

```powershell
.\.venv\Scripts\python -m pytest -q
```

Run a live smoke check without generating AI drafts or sending Discord messages:

```powershell
.\.venv\Scripts\python -m worker.smoke
```

Run a freshness audit without generating AI drafts or sending Discord messages:

```powershell
.\.venv\Scripts\python -m worker.freshness_audit
```

## Storage And Notification Safety

The worker sends Discord review notices as one digest per run instead of one
message per item. This keeps webhook traffic low and lets Discord rate-limit
responses schedule a later retry.

Retention cleanup is enabled by default and only trims old operational data:
logs older than 30 days, raw AI responses older than 14 days, bulky fields on
old low-priority items, and successful worker run records older than 90 days.
Drafts and review-worthy news items are kept for manual posting.

To audit Supabase Storage bucket usage without deleting anything, run the query
in `supabase/queries/storage_audit.sql` from the Supabase SQL editor.

## Freshness Controls

The worker processes each source newest-first, caps candidates per source, and
uses a 72-hour freshness window before AI drafting or Discord digesting. This
prevents a large duplicate-heavy feed from starving smaller sources.

Important knobs:

```text
MAX_ITEMS_PER_SOURCE=10
MAX_FEED_CANDIDATES_PER_SOURCE=30
FRESHNESS_WINDOW_HOURS=72
```
