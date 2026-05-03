# AI News Radar v0.2 Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the MVP recover from OpenRouter rate limits, safely smoke-test live integrations, and clear Supabase advisor findings before the dashboard phase.

**Architecture:** Keep the single Railway cron worker. Add a retryable drafting queue for existing `scored` items, controlled fallback behavior for OpenRouter, and explicit integration smoke commands that do not accidentally spam Discord or spend too much model budget.

**Tech Stack:** Python 3.14, pytest, Pydantic, httpx, Supabase Postgres, OpenRouter, Discord webhooks.

---

### Task 1: Retry Drafting For Existing Scored Items

**Files:**
- Modify: `worker/main.py`
- Modify: `worker/services/supabase_client.py`
- Test: `tests/test_worker_reliability.py`

- [ ] **Step 1: Write the failing test**

Add this test to `tests/test_worker_reliability.py`:

```python
def test_worker_retries_existing_scored_items_after_rate_limit_recovers():
    source = official_source()
    db = FakeDB([source])
    scored_item = major_item("https://openai.com/news/retry")
    db.ready_for_drafting = [
        normalize_items([scored_item], source)[0]
    ]
    db.sources = []
    fetcher = FakeFetcher(items=[])
    openrouter = FakeOpenRouter([VALID_AI_JSON])
    discord = FakeDiscord()

    run_worker(
        settings=FakeSettings(),
        db=db,
        fetchers={"rss": fetcher},
        openrouter=openrouter,
        discord=discord,
    )

    assert openrouter.calls == 1
    assert db.drafts
    assert discord.alerts
```

Expected failure: `FakeDB` has no `load_items_ready_for_drafting` method or the worker never calls it.

- [ ] **Step 2: Run the failing test**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/test_worker_reliability.py::test_worker_retries_existing_scored_items_after_rate_limit_recovers -q
```

Expected: FAIL because existing `scored` rows are not retried.

- [ ] **Step 3: Implement retry loading**

Add `load_items_ready_for_drafting(limit)` to `SupabaseRestClient`. It should select `news_items` where `status = 'scored'`, `importance_score >= MIN_IMPORTANCE_SCORE`, and no row exists in `drafts` for the item. Order by `created_at asc`.

Update `run_worker` to draft existing scored items before fetching new source items, using the same OpenRouter budget and rate-limit handling as new items.

- [ ] **Step 4: Verify**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/test_worker_reliability.py -q
```

Expected: all worker reliability tests pass.

### Task 2: Controlled OpenRouter Fallback

**Files:**
- Modify: `.env.example`
- Modify: `worker/config.py`
- Modify: `worker/main.py`
- Test: `tests/test_worker_reliability.py`

- [ ] **Step 1: Write the failing test**

Add a test that sets `allow_paid_fallback = True`, makes the default model raise `OpenRouterRateLimitError`, and verifies the fallback client is called once and drafts are saved.

- [ ] **Step 2: Run the failing test**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/test_worker_reliability.py::test_paid_fallback_model_is_used_only_when_enabled -q
```

Expected: FAIL because fallback model execution does not exist.

- [ ] **Step 3: Implement fallback configuration**

Add:

```env
ALLOW_PAID_FALLBACK=false
```

Add `allow_paid_fallback: bool` to `Settings`. If the default model returns provider `429` and `ALLOW_PAID_FALLBACK=true`, retry once with `OPENROUTER_FALLBACK_MODEL`. If false, keep the current behavior and leave remaining items in `scored`.

- [ ] **Step 4: Verify**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/test_worker_reliability.py -q
```

Expected: all worker reliability tests pass.

### Task 3: Safe Live Smoke Command

**Files:**
- Create: `worker/smoke.py`
- Modify: `README.md`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: Write the failing tests**

Create tests that verify smoke mode can:

```python
def test_smoke_checks_discord_with_get_without_posting():
    ...

def test_smoke_checks_openrouter_key_without_generation():
    ...

def test_smoke_can_run_worker_with_ai_disabled():
    ...
```

Expected failures: `worker.smoke` does not exist.

- [ ] **Step 2: Implement smoke checks**

Add `python -m worker.smoke` with three checks:

```text
1. Load .env and report required keys as SET/MISSING without printing values.
2. GET Discord webhook and require HTTP 200.
3. GET OpenRouter key endpoint and require HTTP 200.
4. Run worker with MAX_ITEMS_PER_RUN=3 and MAX_AI_DRAFTS_PER_DAY=0.
```

- [ ] **Step 3: Document the command**

Add this to `README.md`:

```powershell
.\.venv\Scripts\python -m worker.smoke
```

- [ ] **Step 4: Verify**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/test_smoke.py -q
```

Expected: smoke tests pass.

### Task 4: Supabase Advisor Cleanup

**Files:**
- Create: `supabase/migrations/002_advisor_cleanup.sql`
- Test: `tests/test_schema_sql.py`

- [ ] **Step 1: Write the failing schema test**

Add assertions that the migration contains:

```sql
create index if not exists ai_requests_news_item_id_idx
create index if not exists posts_draft_id_idx
```

Expected failure: migration `002_advisor_cleanup.sql` does not exist.

- [ ] **Step 2: Add indexes**

Create `supabase/migrations/002_advisor_cleanup.sql`:

```sql
create index if not exists ai_requests_news_item_id_idx
  on public.ai_requests (news_item_id);

create index if not exists posts_draft_id_idx
  on public.posts (draft_id);
```

- [ ] **Step 3: Decide RLS policy timing**

Keep the current service-role-only worker tables private for MVP. Add dashboard RLS policies only when the dashboard exists and the authenticated user model is clear.

- [ ] **Step 4: Verify**

Run:

```powershell
.\.venv\Scripts\python -m pytest tests/test_schema_sql.py -q
```

Expected: schema tests pass.

### Task 5: Acceptance Verification

**Files:**
- No production file changes.

- [ ] **Step 1: Run unit tests**

Run:

```powershell
.\.venv\Scripts\python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run safe smoke**

Run:

```powershell
.\.venv\Scripts\python -m worker.smoke
```

Expected:

```text
ENV: ok
SUPABASE: ok
DISCORD_WEBHOOK: ok
OPENROUTER_KEY: ok
WORKER_NO_AI_SMOKE: ok
```

- [ ] **Step 3: Run one controlled drafting test**

Set these process-only overrides:

```powershell
$env:MAX_ITEMS_PER_RUN='1'
$env:MAX_AI_DRAFTS_PER_DAY='1'
.\.venv\Scripts\python -m worker.main
```

Expected if OpenRouter is available: one draft is created and one Discord alert is sent.

Expected if OpenRouter free model is upstream rate-limited: worker exits successfully, records `rate_limited`, and leaves the item retryable as `scored`.
