from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class CleanupResult:
    skipped: bool = False
    logs_deleted: int = 0
    ai_raw_responses_stripped: int = 0
    low_priority_items_stripped: int = 0
    worker_runs_deleted: int = 0
    storage_snapshot: dict[str, Any] | None = None

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


def run_retention_cleanup(
    db: Any,
    settings: Any,
    *,
    now: datetime | None = None,
) -> CleanupResult:
    if not settings.cleanup_enabled:
        return CleanupResult(skipped=True)

    now = now or datetime.now(timezone.utc)
    limit = max(settings.cleanup_batch_limit, 0)
    if limit == 0:
        return CleanupResult(skipped=True)

    logs_deleted = db.delete_old_logs(
        now - timedelta(days=settings.retention_log_days),
        limit,
    )
    ai_raw_responses_stripped = db.strip_old_ai_raw_responses(
        now - timedelta(days=settings.retention_ai_raw_response_days),
        limit,
    )
    low_priority_items_stripped = db.strip_old_low_priority_news_fields(
        now - timedelta(days=settings.retention_low_priority_days),
        limit,
    )
    worker_runs_deleted = db.delete_old_success_worker_runs(
        now - timedelta(days=settings.retention_worker_run_days),
        limit,
    )
    storage_snapshot = db.get_storage_usage_snapshot()

    return CleanupResult(
        logs_deleted=logs_deleted,
        ai_raw_responses_stripped=ai_raw_responses_stripped,
        low_priority_items_stripped=low_priority_items_stripped,
        worker_runs_deleted=worker_runs_deleted,
        storage_snapshot=storage_snapshot,
    )
