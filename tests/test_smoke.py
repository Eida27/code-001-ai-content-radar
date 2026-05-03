from worker.config import Settings
from worker.models import Source
from worker.processing.normalize import normalize_items
from worker.smoke import (
    check_discord_webhook,
    check_openrouter_key,
    run_worker_no_ai_smoke,
)
from tests.test_worker_reliability import (
    FakeDB,
    FakeDiscord,
    FakeFetcher,
    FakeOpenRouter,
    major_item,
    official_source,
)


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


class RecordingClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.response


def smoke_settings():
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role",
        openrouter_api_key="openrouter-key",
        openrouter_default_model="google/gemma-4-31b-it:free",
        openrouter_fallback_model="google/gemma-4-31b-it",
        allow_paid_fallback=False,
        discord_webhook_url="https://discord.com/api/webhooks/test",
        min_importance_score=7,
        max_ai_drafts_per_day=35,
        max_items_per_run=30,
        source_timeout_seconds=1,
        source_retry_limit=1,
        run_timeout_seconds=60,
        request_timeout_seconds=1,
        openrouter_daily_free_request_limit=10,
    )


def test_smoke_checks_discord_with_get_without_posting():
    client = RecordingClient(FakeResponse(200))

    result = check_discord_webhook("https://discord.com/api/webhooks/test", client)

    assert result.ok is True
    assert [call[0] for call in client.calls] == ["GET"]


def test_smoke_checks_openrouter_key_without_generation():
    client = RecordingClient(FakeResponse(200, {"data": {"label": "test-key"}}))

    result = check_openrouter_key("openrouter-key", client)

    assert result.ok is True
    assert client.calls == [
        (
            "GET",
            "https://openrouter.ai/api/v1/auth/key",
            {"headers": {"Authorization": "Bearer openrouter-key"}, "timeout": 20},
        )
    ]


def test_smoke_can_run_worker_with_ai_disabled():
    source = official_source()
    db = FakeDB([source])
    db.ready_for_drafting = [normalize_items([major_item()], source)[0]]
    fetcher = FakeFetcher(items=[major_item("https://openai.com/news/smoke")])
    openrouter = FakeOpenRouter([])
    discord = FakeDiscord()

    result = run_worker_no_ai_smoke(
        settings=smoke_settings(),
        db=db,
        fetchers={"rss": fetcher},
        openrouter=openrouter,
        discord=discord,
    )

    assert result.ok is True
    assert openrouter.calls == 0
    assert not db.drafts
