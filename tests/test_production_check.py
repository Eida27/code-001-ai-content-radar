from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from tests.test_smoke import FakeResponse, smoke_settings
from tests.test_worker_reliability import FakeFetcher, dated_item, official_source
from worker.production_check import (
    CheckStatus,
    check_cost_defaults,
    check_discord_webhook,
    check_openrouter_model,
    evaluate_supabase_snapshot,
    run_production_check,
)


class RecordingHTTPClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        raise AssertionError("production checks must not POST to external services")


class FakeProductionDB:
    def __init__(self, snapshot=None, sources=None):
        self.snapshot = snapshot or healthy_snapshot()
        self.sources = sources or []
        self.latest_stored_by_source = {}
        self.closed = False

    def get_production_readiness_snapshot(self):
        return self.snapshot

    def load_active_sources(self):
        return self.sources

    def get_latest_stored_published_at_by_source(self, source_id):
        return self.latest_stored_by_source.get(source_id)

    def close(self):
        self.closed = True


def healthy_snapshot():
    return {
        "db_size_bytes": 11 * 1024 * 1024,
        "storage_bytes": 0,
        "storage_objects": 0,
        "table_counts": {
            "sources": 6,
            "news_items": 89,
            "drafts": 90,
            "ai_requests": 38,
            "discord_alerts": 0,
            "worker_runs": 15,
            "posts": 0,
            "logs": 389,
            "worker_locks": 0,
        },
        "rls": {
            "sources": True,
            "news_items": True,
            "drafts": True,
            "posts": True,
            "worker_runs": True,
            "logs": True,
            "ai_requests": True,
            "discord_alerts": True,
            "worker_locks": True,
        },
        "index_names": [
            "sources_active_idx",
            "news_items_source_id_idx",
            "news_items_published_at_idx",
            "news_items_status_idx",
            "news_items_created_at_idx",
            "drafts_news_item_id_idx",
            "drafts_news_item_id_draft_type_unique_idx",
            "discord_alerts_pending_idx",
            "worker_locks_expires_at_idx",
        ],
        "function_names": [
            "try_acquire_worker_lock",
            "release_worker_lock",
            "production_readiness_snapshot",
        ],
        "duplicate_draft_groups": 0,
        "duplicate_news_hash_groups": 0,
        "running_worker_runs": 0,
        "pending_discord_alerts": 0,
    }


def statuses(results):
    return {result.name: result.status for result in results}


def test_evaluate_supabase_snapshot_passes_for_healthy_free_tier_state():
    results = evaluate_supabase_snapshot(smoke_settings(), healthy_snapshot())

    assert all(result.status is CheckStatus.OK for result in results)


def test_evaluate_supabase_snapshot_flags_schema_quota_duplicates_and_running_runs():
    snapshot = healthy_snapshot()
    snapshot["db_size_bytes"] = 451 * 1024 * 1024
    snapshot["storage_bytes"] = 951 * 1024 * 1024
    snapshot["duplicate_draft_groups"] = 1
    snapshot["duplicate_news_hash_groups"] = 2
    snapshot["running_worker_runs"] = 1
    snapshot["rls"]["worker_locks"] = False
    snapshot["index_names"].remove("worker_locks_expires_at_idx")

    results = evaluate_supabase_snapshot(smoke_settings(), snapshot)

    result_statuses = statuses(results)
    assert result_statuses["SUPABASE_DB_SIZE"] is CheckStatus.FAIL
    assert result_statuses["SUPABASE_STORAGE_SIZE"] is CheckStatus.FAIL
    assert result_statuses["SUPABASE_RLS"] is CheckStatus.FAIL
    assert result_statuses["SUPABASE_INDEXES"] is CheckStatus.FAIL
    assert result_statuses["DRAFT_DUPLICATES"] is CheckStatus.FAIL
    assert result_statuses["NEWS_DEDUPE"] is CheckStatus.FAIL
    assert result_statuses["WORKER_RUNNING_RUNS"] is CheckStatus.FAIL


def test_cost_defaults_fail_paid_fallback_without_explicit_cap_and_warn_with_cap():
    no_cap = replace(
        smoke_settings(),
        allow_paid_fallback=True,
        max_paid_fallbacks_per_run=0,
    )
    capped = replace(
        smoke_settings(),
        allow_paid_fallback=True,
        max_paid_fallbacks_per_run=1,
    )

    assert check_cost_defaults(no_cap).status is CheckStatus.FAIL
    assert check_cost_defaults(capped).status is CheckStatus.WARNING


def test_openrouter_and_discord_checks_are_read_only():
    openrouter = RecordingHTTPClient(
        [
            FakeResponse(200, {"data": {"usage_daily": 0}}),
            FakeResponse(200, {"data": [{"id": "google/gemma-4-31b-it:free"}]}),
        ]
    )
    discord = RecordingHTTPClient([FakeResponse(200)])

    model_result = check_openrouter_model(
        "key",
        "google/gemma-4-31b-it:free",
        client=openrouter,
    )
    discord_result = check_discord_webhook(
        "https://discord.com/api/webhooks/test",
        client=discord,
    )

    assert model_result.status is CheckStatus.OK
    assert discord_result.status is CheckStatus.OK
    assert [call[0] for call in openrouter.calls + discord.calls] == [
        "GET",
        "GET",
        "GET",
    ]


def test_openrouter_check_fails_when_default_model_is_missing():
    client = RecordingHTTPClient(
        [
            FakeResponse(200, {"data": {"usage_daily": 0}}),
            FakeResponse(200, {"data": [{"id": "other/model:free"}]}),
        ]
    )

    result = check_openrouter_model(
        "key",
        "google/gemma-4-31b-it:free",
        client=client,
    )

    assert result.status is CheckStatus.FAIL


def test_run_production_check_treats_fresh_feed_ahead_of_storage_as_warning():
    source = official_source("tensorfeed")
    source.name = "TensorFeed AI"
    now = datetime(2026, 5, 3, 16, 3, 4, tzinfo=timezone.utc)
    db = FakeProductionDB(healthy_snapshot(), [source])
    db.latest_stored_by_source[source.id] = datetime(
        2026, 5, 3, 14, 38, 34, tzinfo=timezone.utc
    )
    fetcher = FakeFetcher(items=[dated_item("https://tensorfeed.ai/item", now)])
    http_client = RecordingHTTPClient(
        [
            FakeResponse(200, {"data": {"usage_daily": 0}}),
            FakeResponse(200, {"data": [{"id": "google/gemma-4-31b-it:free"}]}),
            FakeResponse(200),
        ]
    )

    report = run_production_check(
        settings=smoke_settings(),
        db=db,
        fetchers={"rss": fetcher},
        http_client=http_client,
    )

    assert report.ok is True
    assert statuses(report.results)["RSS_FRESHNESS"] is CheckStatus.WARNING
    assert db.closed is True
    assert [call[0] for call in http_client.calls] == ["GET", "GET", "GET"]
