from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_QUERY = ROOT / "supabase" / "queries" / "source_allowlist_examples.sql"


def test_source_allowlist_query_uses_manual_rss_categories_only():
    sql = ALLOWLIST_QUERY.read_text(encoding="utf-8").lower()

    assert "verified_creator" in sql
    assert "reliable_forum" in sql
    assert "ai_aggregator" in sql
    assert "'rss'" in sql
    assert "where not exists" in sql
    assert "'x_api'" not in sql
    assert "'html'" not in sql
