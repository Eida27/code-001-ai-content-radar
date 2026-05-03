from worker.models import Draft
from worker.services.supabase_client import SupabaseRestClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeRestClient:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None, headers=None):
        self.calls.append((path, params or {}, headers or {}))
        if path == "/news_items":
            offset = int((params or {}).get("offset", "0"))
            limit = int((params or {}).get("limit", "0"))
            rows = [
                _news_item_row("drafted-1"),
                _news_item_row("drafted-2"),
                _news_item_row("eligible-1"),
            ]
            return FakeResponse(rows[offset : offset + limit])
        if path == "/drafts":
            news_item_id = (params or {})["news_item_id"].removeprefix("eq.")
            if news_item_id.startswith("drafted"):
                return FakeResponse([{"id": f"draft-for-{news_item_id}"}])
            return FakeResponse([])
        raise AssertionError(f"Unexpected path {path}")


class FakeDraftRestClient:
    def __init__(self):
        self.calls = []

    def post(self, path, json=None, headers=None):
        self.calls.append((path, json, headers or {}))
        return FakeResponse([])


def test_load_items_ready_for_drafting_does_not_starve_after_drafted_rows():
    client = SupabaseRestClient("https://example.supabase.co", "service-role", 10)
    client._client.close()
    fake_rest = FakeRestClient()
    client._client = fake_rest

    items = client.load_items_ready_for_drafting(limit=1, min_importance_score=7)

    assert [item.id for item in items] == ["eligible-1"]
    news_item_calls = [call for call in fake_rest.calls if call[0] == "/news_items"]
    assert int(news_item_calls[0][1]["limit"]) > 1


def test_save_drafts_uses_unique_conflict_target_for_idempotency():
    client = SupabaseRestClient("https://example.supabase.co", "service-role", 10)
    client._client.close()
    fake_rest = FakeDraftRestClient()
    client._client = fake_rest

    client.save_drafts(
        "news-1",
        [Draft("short_post", "Draft copy", "model-a")],
    )

    assert fake_rest.calls == [
        (
            "/drafts?on_conflict=news_item_id,draft_type",
            [
                {
                    "news_item_id": "news-1",
                    "draft_type": "short_post",
                    "content": "Draft copy",
                    "model_used": "model-a",
                    "status": "needs_review",
                }
            ],
            {"Prefer": "resolution=ignore-duplicates,return=minimal"},
        )
    ]


def _news_item_row(item_id):
    return {
        "id": item_id,
        "source_id": None,
        "title": f"Title {item_id}",
        "url": f"https://example.com/{item_id}",
        "canonical_url": f"https://example.com/{item_id}",
        "normalized_url_hash": f"normalized-{item_id}",
        "canonical_url_hash": f"canonical-{item_id}",
        "content_hash": f"content-{item_id}",
        "raw_summary": "Summary",
        "source_name": "Example",
        "published_at": None,
        "status": "scored",
        "importance_score": 8,
        "importance_reason": "Important",
    }
