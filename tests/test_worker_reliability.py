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
    max_ai_calls_per_run = 10
    max_paid_fallbacks_per_run = 3
    openrouter_default_model = "google/gemma-4-31b-it:free"
    openrouter_fallback_model = "google/gemma-4-31b-it"
    allow_paid_fallback = False
    openrouter_daily_free_request_limit = 10
    source_timeout_seconds = 1
    source_retry_limit = 1
    run_timeout_seconds = 60


class FakeDB:
    def __init__(self, sources):
        self.sources = sources
        self.logs = []
        self.items = []
        self.drafts = []
        self.ai_requests = []
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

    def update_item_status(self, item_id, status):
        if item_id is None:
            return
        self.items[-1].status = status

    def save_drafts(self, news_item_id, drafts):
        self.drafts.extend(drafts)

    def save_ai_request(self, **kwargs):
        self.ai_requests.append(kwargs)

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
        self.closed = False

    def send_alert(self, item, drafts, score):
        self.alerts.append((item, drafts, score))

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
    assert discord.alerts
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
    assert discord.alerts


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
    assert discord.alerts


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
    assert discord.alerts


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
