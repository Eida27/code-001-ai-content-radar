from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "001_initial_schema.sql"
ADVISOR_CLEANUP_MIGRATION = (
    ROOT / "supabase" / "migrations" / "002_advisor_cleanup.sql"
)
RETENTION_DISCORD_MIGRATION = (
    ROOT / "supabase" / "migrations" / "003_retention_discord_digest.sql"
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
