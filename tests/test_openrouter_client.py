from worker.models import NewsItem, Score
from worker.services.openrouter_client import OpenRouterClient


class FakeResponse:
    status_code = 200
    text = "ok"

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"image_overlay_caption": "Overlay draft.", '
                            '"short_post": "X draft."}'
                        )
                    }
                }
            ]
        }


class FakeChatClient:
    def __init__(self):
        self.calls = []
        self.closed = False

    def post(self, path, json=None):
        self.calls.append((path, json))
        return FakeResponse()

    def close(self):
        self.closed = True


def test_generate_draft_requires_cautious_language_for_unconfirmed_items():
    client = OpenRouterClient("key", "model-a", 10)
    client._client.close()
    fake_chat = FakeChatClient()
    client._client = fake_chat
    item = NewsItem(
        title="Leak: OpenAI agent API for developers",
        url="https://creator.example/leak",
        canonical_url="https://creator.example/leak",
        normalized_url_hash="n",
        canonical_url_hash="c",
        content_hash="h",
        source_name="Trusted Creator Feed",
    )
    score = Score(
        value=8,
        reason=(
            "Allowlisted unofficial source; Important AI keyword detected; "
            "Unconfirmed: verify before posting"
        ),
    )

    client.generate_draft(item, score)

    payload = fake_chat.calls[0][1]
    prompt = "\n".join(message["content"] for message in payload["messages"])
    assert "unconfirmed" in prompt.lower()
    assert "Do not describe it as launched, released, or announced" in prompt
    assert "Requires manual confirmation before posting" in prompt


def test_generate_draft_requests_two_drafts_for_ai_builders_without_source_url():
    client = OpenRouterClient("key", "model-a", 10)
    client._client.close()
    fake_chat = FakeChatClient()
    client._client = fake_chat
    item = NewsItem(
        title="OpenAI model API update",
        url="https://openai.com/news/model",
        canonical_url="https://openai.com/news/model",
        normalized_url_hash="n",
        canonical_url_hash="c",
        content_hash="h",
        raw_summary="A useful update for developers.",
        source_name="OpenAI News",
    )
    score = Score(value=8, reason="Important AI builder update")

    client.generate_draft(item, score)

    payload = fake_chat.calls[0][1]
    prompt = "\n".join(message["content"] for message in payload["messages"])
    assert "Return exactly these JSON keys: image_overlay_caption, short_post." in prompt
    assert "image_overlay_caption" in prompt
    assert "1-2 sentences" in prompt
    assert "180 characters" in prompt
    assert "AI builders" in prompt
    assert "Do not include the source URL in short_post" in prompt
    assert "long_post" not in prompt
    assert "thread" not in prompt
    assert "why_it_matters" not in prompt
    assert "risk_note" not in prompt
