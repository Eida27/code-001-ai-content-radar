from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "001_initial_schema.sql"


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
