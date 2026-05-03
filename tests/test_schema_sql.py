from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "001_initial_schema.sql"
ADVISOR_CLEANUP_MIGRATION = (
    ROOT / "supabase" / "migrations" / "002_advisor_cleanup.sql"
)
RETENTION_DISCORD_MIGRATION = (
    ROOT / "supabase" / "migrations" / "003_retention_discord_digest.sql"
)
FRESHNESS_MIGRATION = ROOT / "supabase" / "migrations" / "004_freshness_hardening.sql"
DRAFT_IDEMPOTENCY_MIGRATION = (
    ROOT / "supabase" / "migrations" / "005_draft_idempotency.sql"
)
PRODUCTION_READINESS_MIGRATION = (
    ROOT / "supabase" / "migrations" / "006_production_readiness.sql"
)


def test_schema_has_strict_statuses_indexes_dedupe_and_rls():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create type public.news_item_status" in sql
    assert "draft_failed" in sql
    assert "create type public.worker_run_status" in sql
    assert "normalized_url_hash text not null" in sql
    assert "canonical_url_hash text" in sql
    assert "unique (normalized_url_hash)" in sql
    assert "unique (canonical_url_hash)" in sql

    for index_name in [
        "sources_active_idx",
        "news_items_source_id_idx",
        "news_items_published_at_idx",
        "news_items_status_idx",
        "news_items_created_at_idx",
        "drafts_news_item_id_idx",
        "worker_runs_status_idx",
    ]:
        assert index_name in sql

    for table_name in [
        "sources",
        "news_items",
        "drafts",
        "posts",
        "worker_runs",
        "logs",
        "ai_requests",
    ]:
        assert f"alter table public.{table_name} enable row level security" in sql


def test_schema_stores_ai_raw_response_and_parse_errors():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create table if not exists public.ai_requests" in sql
    assert "raw_response text" in sql
    assert "parse_error text" in sql
    assert "rate_limited" in sql


def test_advisor_cleanup_indexes_foreign_keys():
    sql = ADVISOR_CLEANUP_MIGRATION.read_text(encoding="utf-8").lower()

    assert "create index if not exists ai_requests_news_item_id_idx" in sql
    assert "on public.ai_requests (news_item_id)" in sql
    assert "create index if not exists posts_draft_id_idx" in sql
    assert "on public.posts (draft_id)" in sql


def test_retention_and_discord_digest_schema():
    sql = RETENTION_DISCORD_MIGRATION.read_text(encoding="utf-8").lower()

    assert "create type public.discord_alert_status" in sql
    assert "create table if not exists public.discord_alerts" in sql
    assert "news_item_id uuid not null references public.news_items(id) on delete cascade" in sql
    assert "payload jsonb not null" in sql
    assert "unique (news_item_id)" in sql
    assert "discord_alerts_pending_idx" in sql
    assert "logs_created_at_idx" in sql
    assert "ai_requests_created_at_idx" in sql
    assert "worker_runs_finished_at_idx" in sql
    assert "news_items_status_created_at_idx" in sql
    assert "alter table public.discord_alerts enable row level security" in sql


def test_freshness_hardening_schema_and_sources():
    sql = FRESHNESS_MIGRATION.read_text(encoding="utf-8").lower()

    for column_name in [
        "last_success_at",
        "last_error_at",
        "last_error_message",
        "latest_feed_published_at",
        "latest_stored_published_at",
    ]:
        assert column_name in sql

    assert "sources_latest_feed_published_at_idx" in sql
    assert "google ai blog" in sql
    assert "nvidia ai blog" in sql
    assert "tensorfeed ai" in sql
    assert "https://blog.google/innovation-and-ai/technology/ai/rss/" in sql
    assert "https://blogs.nvidia.com/blog/category/deep-learning/feed/" in sql
    assert "https://tensorfeed.ai/feed.xml" in sql


def test_draft_idempotency_schema_has_unique_news_item_draft_type_index():
    sql = DRAFT_IDEMPOTENCY_MIGRATION.read_text(encoding="utf-8").lower()

    assert "drafts_news_item_id_draft_type_unique_idx" in sql
    assert "create unique index if not exists" in sql
    assert "on public.drafts (news_item_id, draft_type)" in sql


def test_production_readiness_schema_has_worker_lock_and_snapshot_rpc():
    sql = PRODUCTION_READINESS_MIGRATION.read_text(encoding="utf-8").lower()

    assert "create table if not exists public.worker_locks" in sql
    assert "lock_name text primary key" in sql
    assert "worker_locks_expires_at_idx" in sql
    assert "alter table public.worker_locks enable row level security" in sql
    assert "create or replace function public.try_acquire_worker_lock" in sql
    assert "create or replace function public.release_worker_lock" in sql
    assert "create or replace function public.production_readiness_snapshot" in sql
    assert "revoke execute on function public.try_acquire_worker_lock" in sql
    assert "grant execute on function public.try_acquire_worker_lock" in sql
