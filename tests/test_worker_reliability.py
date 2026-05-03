from datetime import datetime, timezone

from worker.main import run_worker
from worker.models import RawItem, Source
from worker.processing.normalize import normalize_items
from worker.services.openrouter_client import OpenRouterRateLimitError
from worker.services.supabase_client import DuplicateItemError


class FakeSettings:
    min_importance_score = 7
    max_ai_drafts_per_day = 35
    max_items_per_run = 30
    max_items_per_source = 10
    max_feed_candidates_per_source = 30
    freshness_window_hours = 72
    max_ai_calls_per_run = 10
    max_paid_fallbacks_per_run = 3
    openrouter_default_model = "google/gemma-4-31b-it:free"
    openrouter_fallback_model = "google/gemma-4-31b-it"
    allow_paid_fallback = False
    openrouter_daily_free_request_limit = 10
    source_timeout_seconds = 1
    source_retry_limit = 1
    run_timeout_seconds = 60
    cleanup_enabled = True
    retention_log_days = 30
    retention_ai_raw_response_days = 14
    retention_low_priority_days = 30
    retention_worker_run_days = 90
    cleanup_batch_limit = 100
    discord_max_items_per_digest = 10


class FakeDB:
    def __init__(self, sources):
        self.sources = sources
        self.logs = []
        self.items = []
        self.drafts = []
        self.ai_requests = []
        self.discord_alerts = []
        self.source_checks = []
        self.source_successes = []
        self.source_failures = []
        self.sent_discord_alert_ids = []
        self.retry_discord_alerts = []
        self.failed_discord_alerts = []
        self.finished = []
        self.closed = False

    def start_worker_run(self):
        return "run-1"

    def finish_worker_run(self, run_id, status, metadata=None):
        self.finished.append((run_id, status, metadata or {}))

    def load_active_sources(self):
        return self.sources

    def load_items_ready_for_drafting(self, limit, min_importance_score):
        return getattr(self, "ready_for_drafting", [])[:limit]

    def count_drafts_created_today(self):
        return 0

    def count_ai_requests_today(self, model):
        return 0

    def save_item(self, item, status):
        item.status = status
        self.items.append(item)
        return item

    def mark_source_check_started(self, source_id):
        self.source_checks.append(source_id)

    def mark_source_check_success(self, source_id, latest_feed_published_at=None):
        self.source_successes.append((source_id, latest_feed_published_at))

    def mark_source_check_failure(self, source_id, error):
        self.source_failures.append((source_id, str(error)))

    def update_item_status(self, item_id, status):
        if item_id is None:
            return
        self.items[-1].status = status

    def save_drafts(self, news_item_id, drafts):
        self.drafts.extend(drafts)

    def save_ai_request(self, **kwargs):
        self.ai_requests.append(kwargs)

    def queue_discord_alert(self, item, drafts, score):
        self.discord_alerts.append((item, drafts, score))

    def load_pending_discord_alerts(self, limit):
        return [
            type(
                "Alert",
                (),
                {
                    "id": f"alert-{index}",
                    "news_item_id": item.id or f"item-{index}",
                    "payload": {
                        "title": item.title,
                        "source_name": item.source_name,
                        "score": item.importance_score,
                        "reason": score.reason,
                        "why_it_matters": next(
                            (
                                draft.content
                                for draft in drafts
                                if draft.draft_type == "why_it_matters"
                            ),
                            "",
                        ),
                        "short_post": next(
                            (
                                draft.content
                                for draft in drafts
                                if draft.draft_type == "short_post"
                            ),
                            "",
                        ),
                        "source_url": item.canonical_url,
                    },
                },
            )()
            for index, (item, drafts, score) in enumerate(self.discord_alerts[:limit], start=1)
            if f"alert-{index}" not in self.sent_discord_alert_ids
        ]

    def mark_discord_alerts_sent(self, alert_ids):
        self.sent_discord_alert_ids.extend(alert_ids)

    def mark_discord_alerts_retry(self, alert_ids, retry_after_seconds, error, status_code):
        self.retry_discord_alerts.append(
            (alert_ids, retry_after_seconds, error, status_code)
        )

    def mark_discord_alerts_failed(self, alert_ids, error, status_code):
        self.failed_discord_alerts.append((alert_ids, error, status_code))

    def delete_old_logs(self, cutoff, limit):
        return 0

    def strip_old_ai_raw_responses(self, cutoff, limit):
        return 0

    def strip_old_low_priority_news_fields(self, cutoff, limit):
        return 0

    def delete_old_success_worker_runs(self, cutoff, limit):
        return 0

    def get_storage_usage_snapshot(self):
        return {"object_count": 0, "total_bytes": 0}

    def log_event(self, level, module, message, metadata=None):
        self.logs.append((level, module, message, metadata or {}))

    def close(self):
        self.closed = True


class ConflictOnceDB(FakeDB):
    def __init__(self, sources):
        super().__init__(sources)
        self.conflicted = False

    def save_item(self, item, status):
        if not self.conflicted:
            self.conflicted = True
            raise DuplicateItemError("duplicate normalized_url_hash")
        return super().save_item(item, status)


class DuplicateDB(FakeDB):
    def save_item(self, item, status):
        raise DuplicateItemError("duplicate normalized_url_hash")


class FakeFetcher:
    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error
        self.closed = False

    def fetch(self, source, timeout_seconds, retry_limit):
        if self.error:
            raise self.error
        return self.items

    def close(self):
        self.closed = True


class FakeOpenRouter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.closed = False

    def generate_draft(self, item, score):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self):
        self.closed = True


class FakeDiscord:
    def __init__(self):
        self.alerts = []
        self.digests = []
        self.closed = False

    def send_alert(self, item, drafts, score):
        self.alerts.append((item, drafts, score))

    def send_digest(self, alerts):
        self.digests.append(list(alerts))

    def close(self):
        self.closed = True


def official_source(source_id="source-1"):
    return Source(
        id=source_id,
        name="OpenAI News",
        type="rss",
        url="https://openai.com/news/rss.xml",
        priority=10,
    )


def major_item(url="https://openai.com/news/model"):
    return RawItem(
        title="OpenAI launches new model API for developers",
        url=url,
        raw_summary="A useful update.",
        published_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
    )


def dated_item(url, published_at, title="OpenAI launches new model API for developers"):
    return RawItem(
        title=title,
        url=url,
        raw_summary="A useful update for developers.",
        published_at=published_at,
    )


VALID_AI_JSON = """
{
  "short_post": "OpenAI released a model API update.",
  "long_post": "OpenAI released a model API update for developers.",
  "thread": ["OpenAI released an update.", "Developers should verify the source."],
  "why_it_matters": "Developers may get a faster workflow.",
  "risk_note": "Verify details before posting."
}
"""


def test_source_failure_is_logged_and_other_sources_continue():
    source_a = official_source("source-a")
    source_b = official_source("source-b")
    db = FakeDB([source_a, source_b])
    bad_fetcher = FakeFetcher(error=RuntimeError("feed down"))
    good_fetcher = FakeFetcher(items=[major_item()])
    openrouter = FakeOpenRouter([VALID_AI_JSON])
    discord = FakeDiscord()

    run_worker(
        settings=FakeSettings(),
        db=db,
        fetchers={"rss": {"source-a": bad_fetcher, "source-b": good_fetcher}},
        openrouter=openrouter,
        discord=discord,
    )

    assert len(db.items) == 1
    assert db.finished[-1][1] == "success"
    assert any("feed down" in log[2] for log in db.logs)
    assert not discord.alerts
    assert len(discord.digests) == 1
    assert db.closed is True
    assert openrouter.closed is True
    assert discord.closed is True


def test_rate_limit_stops_ai_generation_for_rest_of_run():
    source = official_source()
    db = FakeDB([source])
    fetcher = FakeFetcher(
        items=[
            major_item("https://openai.com/news/model-1"),
            major_item("https://openai.com/news/model-2"),
        ]
    )
    openrouter = FakeOpenRouter([OpenRouterRateLimitError("429 rate limit")])
    discord = FakeDiscord()

    run_worker(
        settings=FakeSettings(),
        db=db,
        fetchers={"rss": fetcher},
        openrouter=openrouter,
        discord=discord,
    )

    assert openrouter.calls == 1
    assert db.items[0].status == "scored"
    assert db.items[1].status == "scored"
    assert db.ai_requests[0]["status"] == "rate_limited"
    assert not discord.alerts
    assert not discord.digests


def test_invalid_ai_json_is_stored_as_parse_error_and_draft_failed():
    source = official_source()
    db = FakeDB([source])
    fetcher = FakeFetcher(items=[major_item()])
    openrouter = FakeOpenRouter(['{"short_post": "broken"'])
    discord = FakeDiscord()

    run_worker(
        settings=FakeSettings(),
        db=db,
        fetchers={"rss": fetcher},
        openrouter=openrouter,
        discord=discord,
    )

    assert db.items[0].status == "draft_failed"
    assert db.ai_requests[0]["status"] == "parse_error"
    assert db.ai_requests[0]["raw_response"] == '{"short_post": "broken"'
    assert db.ai_requests[0]["parse_error"]
    assert not db.drafts
    assert not discord.digests


def test_duplicate_insert_conflict_is_logged_and_run_continues():
    source = official_source()
    db = ConflictOnceDB([source])
    fetcher = FakeFetcher(
        items=[
            major_item("https://openai.com/news/model-1"),
            major_item("https://openai.com/news/model-2"),
        ]
    )
    openrouter = FakeOpenRouter([VALID_AI_JSON])
    discord = FakeDiscord()

    run_worker(
        settings=FakeSettings(),
        db=db,
        fetchers={"rss": fetcher},
        openrouter=openrouter,
        discord=discord,
    )

    assert len(db.items) == 1
    assert db.finished[-1][1] == "success"
    assert any("Duplicate item skipped" in log[2] for log in db.logs)
    assert len(discord.digests) == 1


def test_worker_retries_existing_scored_items_after_rate_limit_recovers():
    source = official_source()
    db = FakeDB([source])
    scored_item = major_item("https://openai.com/news/retry")
    db.ready_for_drafting = [normalize_items([scored_item], source)[0]]
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
    assert len(discord.digests) == 1


def test_paid_fallback_model_is_used_only_when_enabled():
    settings = FakeSettings()
    settings.allow_paid_fallback = True
    source = official_source()
    db = FakeDB([source])
    fetcher = FakeFetcher(items=[major_item()])
    openrouter = FakeOpenRouter([OpenRouterRateLimitError("429 rate limit")])
    fallback_openrouter = FakeOpenRouter([VALID_AI_JSON])
    discord = FakeDiscord()

    run_worker(
        settings=settings,
        db=db,
        fetchers={"rss": fetcher},
        openrouter=openrouter,
        fallback_openrouter=fallback_openrouter,
        discord=discord,
    )

    assert openrouter.calls == 1
    assert fallback_openrouter.calls == 1
    assert db.drafts
    assert db.ai_requests[-1]["status"] == "success"
    assert db.ai_requests[-1]["model_used"] == settings.openrouter_fallback_model
    assert len(discord.digests) == 1


def test_paid_fallbacks_stop_at_per_run_cap():
    settings = FakeSettings()
    settings.allow_paid_fallback = True
    settings.max_paid_fallbacks_per_run = 1
    source = official_source()
    db = FakeDB([source])
    fetcher = FakeFetcher(
        items=[
            major_item("https://openai.com/news/model-1"),
            major_item("https://openai.com/news/model-2"),
        ]
    )
    openrouter = FakeOpenRouter(
        [
            OpenRouterRateLimitError("429 rate limit"),
            OpenRouterRateLimitError("429 rate limit"),
        ]
    )
    fallback_openrouter = FakeOpenRouter([VALID_AI_JSON, VALID_AI_JSON])
    discord = FakeDiscord()

    run_worker(
        settings=settings,
        db=db,
        fetchers={"rss": fetcher},
        openrouter=openrouter,
        fallback_openrouter=fallback_openrouter,
        discord=discord,
    )

    assert openrouter.calls == 2
    assert fallback_openrouter.calls == 1
    assert len(db.drafts) == 5
    assert any("Paid fallback limit reached" in log[2] for log in db.logs)


def test_total_ai_calls_stop_at_per_run_cap_even_with_fallback():
    settings = FakeSettings()
    settings.allow_paid_fallback = True
    settings.max_ai_calls_per_run = 2
    settings.max_paid_fallbacks_per_run = 5
    source = official_source()
    db = FakeDB([source])
    fetcher = FakeFetcher(
        items=[
            major_item("https://openai.com/news/model-1"),
            major_item("https://openai.com/news/model-2"),
        ]
    )
    openrouter = FakeOpenRouter(
        [
            OpenRouterRateLimitError("429 rate limit"),
            OpenRouterRateLimitError("429 rate limit"),
        ]
    )
    fallback_openrouter = FakeOpenRouter([VALID_AI_JSON, VALID_AI_JSON])
    discord = FakeDiscord()

    run_worker(
        settings=settings,
        db=db,
        fetchers={"rss": fetcher},
        openrouter=openrouter,
        fallback_openrouter=fallback_openrouter,
        discord=discord,
    )

    assert openrouter.calls == 1
    assert fallback_openrouter.calls == 1
    assert len(db.drafts) == 5
    assert any("Per-run AI call limit reached" in log[2] for log in db.logs)


def test_worker_marks_run_error_when_interrupted():
    source = official_source()
    db = FakeDB([source])
    fetcher = FakeFetcher(items=[major_item()])
    openrouter = FakeOpenRouter([KeyboardInterrupt()])
    discord = FakeDiscord()

    try:
        run_worker(
            settings=FakeSettings(),
            db=db,
            fetchers={"rss": fetcher},
            openrouter=openrouter,
            discord=discord,
        )
    except KeyboardInterrupt:
        pass

    assert db.finished[-1][1] == "error"
    assert db.finished[-1][2]["error_type"] == "KeyboardInterrupt"


def test_worker_queues_alerts_and_sends_one_discord_digest_per_run():
    source = official_source()
    db = FakeDB([source])
    fetcher = FakeFetcher(
        items=[
            major_item("https://openai.com/news/model-1"),
            major_item("https://openai.com/news/model-2"),
        ]
    )
    openrouter = FakeOpenRouter([VALID_AI_JSON, VALID_AI_JSON])
    discord = FakeDiscord()

    run_worker(
        settings=FakeSettings(),
        db=db,
        fetchers={"rss": fetcher},
        openrouter=openrouter,
        discord=discord,
    )

    assert len(db.discord_alerts) == 2
    assert not discord.alerts
    assert len(discord.digests) == 1
    assert len(discord.digests[0]) == 2
    assert db.sent_discord_alert_ids == ["alert-1", "alert-2"]


def test_cleanup_failure_is_logged_without_failing_worker_run():
    class CleanupFailingDB(FakeDB):
        def delete_old_logs(self, cutoff, limit):
            raise RuntimeError("cleanup exploded")

    source = official_source()
    db = CleanupFailingDB([source])
    fetcher = FakeFetcher(items=[])
    openrouter = FakeOpenRouter([])
    discord = FakeDiscord()

    run_worker(
        settings=FakeSettings(),
        db=db,
        fetchers={"rss": fetcher},
        openrouter=openrouter,
        discord=discord,
    )

    assert db.finished[-1][1] == "success"
    assert any("Retention cleanup failed" in log[2] for log in db.logs)


def test_duplicate_heavy_first_source_does_not_starve_later_sources():
    openai = official_source("openai")
    github = Source(
        id="github",
        name="GitHub Blog AI",
        type="rss",
        url="https://github.blog/feed/",
        priority=7,
    )
    db = FakeDB([openai, github])

    class DuplicateOpenAIDB(FakeDB):
        def save_item(self, item, status):
            if item.source_id == "openai":
                raise DuplicateItemError("duplicate normalized_url_hash")
            return super().save_item(item, status)

    db = DuplicateOpenAIDB([openai, github])
    fresh = datetime(2026, 5, 3, tzinfo=timezone.utc)
    fetchers = {
        "rss": {
            "openai": FakeFetcher(
                items=[
                    dated_item(f"https://openai.com/news/old-{index}", fresh)
                    for index in range(40)
                ]
            ),
            "github": FakeFetcher(
                items=[dated_item("https://github.blog/ai/copilot-cli", fresh)]
            ),
        }
    }
    openrouter = FakeOpenRouter([VALID_AI_JSON])
    discord = FakeDiscord()

    run_worker(
        settings=FakeSettings(),
        db=db,
        fetchers=fetchers,
        openrouter=openrouter,
        discord=discord,
    )

    assert [item.source_id for item in db.items] == ["github"]
    assert db.source_checks == ["openai", "github"]
    assert openrouter.calls == 1
    assert len(discord.digests) == 1


def test_feed_entries_are_processed_newest_first_and_limited_per_source():
    settings = FakeSettings()
    settings.max_items_per_source = 2
    settings.max_feed_candidates_per_source = 3
    settings.max_ai_drafts_per_day = 0
    source = official_source()
    db = FakeDB([source])
    fetcher = FakeFetcher(
        items=[
            dated_item(
                "https://openai.com/news/older",
                datetime(2026, 5, 1, tzinfo=timezone.utc),
                "Older OpenAI API update",
            ),
            dated_item(
                "https://openai.com/news/newest",
                datetime(2026, 5, 3, tzinfo=timezone.utc),
                "Newest OpenAI API update",
            ),
            dated_item(
                "https://openai.com/news/middle",
                datetime(2026, 5, 2, tzinfo=timezone.utc),
                "Middle OpenAI API update",
            ),
        ]
    )

    run_worker(
        settings=settings,
        db=db,
        fetchers={"rss": fetcher},
        openrouter=FakeOpenRouter([]),
        discord=FakeDiscord(),
    )

    assert [item.canonical_url for item in db.items] == [
        "https://openai.com/news/newest",
        "https://openai.com/news/middle",
    ]


def test_stale_items_are_stored_but_never_drafted_or_queued_for_discord():
    source = official_source()
    db = FakeDB([source])
    stale = datetime(2026, 4, 25, tzinfo=timezone.utc)
    fetcher = FakeFetcher(items=[dated_item("https://openai.com/news/stale", stale)])
    openrouter = FakeOpenRouter([VALID_AI_JSON])
    discord = FakeDiscord()

    run_worker(
        settings=FakeSettings(),
        db=db,
        fetchers={"rss": fetcher},
        openrouter=openrouter,
        discord=discord,
    )

    assert len(db.items) == 1
    assert db.items[0].status == "scored"
    assert openrouter.calls == 0
    assert not db.drafts
    assert not db.discord_alerts
    assert not discord.digests


def test_source_health_success_and_failure_are_recorded():
    source_a = official_source("source-a")
    source_b = official_source("source-b")
    db = FakeDB([source_a, source_b])
    fresh = datetime(2026, 5, 3, tzinfo=timezone.utc)

    run_worker(
        settings=FakeSettings(),
        db=db,
        fetchers={
            "rss": {
                "source-a": FakeFetcher(items=[dated_item("https://a.test/news", fresh)]),
                "source-b": FakeFetcher(error=RuntimeError("timeout")),
            }
        },
        openrouter=FakeOpenRouter([VALID_AI_JSON]),
        discord=FakeDiscord(),
    )

    assert db.source_checks == ["source-a", "source-b"]
    assert db.source_successes == [("source-a", fresh)]
    assert db.source_failures == [("source-b", "timeout")]
