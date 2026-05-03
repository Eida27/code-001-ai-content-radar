from datetime import datetime, timezone

from worker.services.cleanup import run_retention_cleanup


class CleanupSettings:
    cleanup_enabled = True
    retention_log_days = 30
    retention_ai_raw_response_days = 14
    retention_low_priority_days = 30
    retention_worker_run_days = 90
    cleanup_batch_limit = 25


class FakeCleanupDB:
    def __init__(self):
        self.calls = []

    def delete_old_logs(self, cutoff, limit):
        self.calls.append(("delete_old_logs", cutoff, limit))
        return 3

    def strip_old_ai_raw_responses(self, cutoff, limit):
        self.calls.append(("strip_old_ai_raw_responses", cutoff, limit))
        return 5

    def strip_old_low_priority_news_fields(self, cutoff, limit):
        self.calls.append(("strip_old_low_priority_news_fields", cutoff, limit))
        return 7

    def delete_old_success_worker_runs(self, cutoff, limit):
        self.calls.append(("delete_old_success_worker_runs", cutoff, limit))
        return 11

    def get_storage_usage_snapshot(self):
        self.calls.append(("get_storage_usage_snapshot", None, None))
        return {"object_count": 0, "total_bytes": 0}


def test_retention_cleanup_uses_balanced_cutoffs_and_batch_limit():
    db = FakeCleanupDB()
    now = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)

    result = run_retention_cleanup(db, CleanupSettings(), now=now)

    assert result.logs_deleted == 3
    assert result.ai_raw_responses_stripped == 5
    assert result.low_priority_items_stripped == 7
    assert result.worker_runs_deleted == 11
    assert result.storage_snapshot == {"object_count": 0, "total_bytes": 0}
    assert db.calls == [
        ("delete_old_logs", datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc), 25),
        (
            "strip_old_ai_raw_responses",
            datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc),
            25,
        ),
        (
            "strip_old_low_priority_news_fields",
            datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc),
            25,
        ),
        (
            "delete_old_success_worker_runs",
            datetime(2026, 2, 2, 12, 0, tzinfo=timezone.utc),
            25,
        ),
        ("get_storage_usage_snapshot", None, None),
    ]


def test_retention_cleanup_skips_when_disabled():
    db = FakeCleanupDB()
    settings = CleanupSettings()
    settings.cleanup_enabled = False

    result = run_retention_cleanup(db, settings)

    assert result.skipped is True
    assert db.calls == []
