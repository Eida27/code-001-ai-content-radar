from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STORAGE_AUDIT_QUERY = ROOT / "supabase" / "queries" / "storage_audit.sql"


def test_storage_audit_query_reports_object_count_and_bytes_without_deleting():
    sql = STORAGE_AUDIT_QUERY.read_text(encoding="utf-8").lower()

    assert "from storage.objects" in sql
    assert "count(*)" in sql
    assert "metadata->>'size'" in sql
    assert "delete" not in sql
    assert "drop" not in sql
