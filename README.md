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

The `.venv/` directory is intentionally ignored by git. Railway/Railpack uses
`.python-version` to pin the cloud runtime to Python 3.13.2.

## Railway Cron Notes

Railway cron schedules are evaluated in UTC. The minimum frequency is 5 minutes, and a 15 minute schedule is the recommended MVP default:

```text
*/15 * * * *
```

The worker is designed to finish before the next schedule and close HTTP resources. If a previous Railway cron execution is still active, Railway can skip the next scheduled run, so the worker must remain idempotent and time-bounded.

Recommended Railway start command when the service root is the repository root:

```bash
python -m worker.main
```

The Railway service root should be the repository root so Railway can read
`.python-version`, `requirements.txt`, `railway.toml`, and the `worker` package.
That config uses Railpack, Python 3.13.2, `python -m worker.main`,
`*/15 * * * *`, and restart policy `NEVER`. Do not deploy the `worker`
directory as a flattened app root because `main.py` imports the `worker`
package. Do not add a Dockerfile unless Railpack detection fails or the worker
later needs custom system packages.

## Production Readiness

Before enabling Railway cron, run this checklist from the repository root:

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m compileall worker
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python -m worker.production_check
.\.venv\Scripts\python -m worker.freshness_audit
```

Then confirm the Railway service root is the repository root, add the same
secrets and settings from `.env` as Railway variables, and enable the cron
schedule. The production check is read-only: it validates environment, Supabase
schema/RLS, quota headroom, duplicate protection, OpenRouter key/model
availability, Discord webhook validity, and RSS freshness without generating
drafts or posting Discord messages.

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

This smoke check is intentionally live: it can write worker/source/news state to
Supabase so the runtime path is exercised. Use `worker.production_check` when
you need a read-only deployment check.

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

## Reliable Unofficial RSS Sources

The worker can monitor manually allowlisted RSS or Atom feeds from reliable
unofficial sources, such as verified creators, forums with a strong moderation
record, and AI news aggregators. Do not auto-discover sources. Add only feeds
you have reviewed and are comfortable seeing in the manual review queue.

Use these source categories and priority ranges:

```text
verified_creator  priority 5-7
reliable_forum    priority 5-6
ai_aggregator     priority 5
```

Official and high-trust sources keep their existing higher priorities. Unofficial
sources can create review items when their content has strong AI, company, and
audience-impact signals, but draft prompts and Discord review reasons label
them as unconfirmed and require manual verification before posting.

Use `supabase/queries/source_allowlist_examples.sql` as a safe template for
adding approved RSS-compatible sources. It inserts nothing until each candidate
row is reviewed and marked `approved = true`.
