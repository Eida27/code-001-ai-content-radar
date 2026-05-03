import httpx
import pytest

from worker.models import DiscordAlert
from worker.services.discord_notifier import (
    DiscordHardFailure,
    DiscordNotifier,
    DiscordRateLimitError,
)


def _alert(alert_id="alert-1", title="AI launch"):
    return DiscordAlert(
        id=alert_id,
        news_item_id=f"news-{alert_id}",
        payload={
            "title": title,
            "source_name": "OpenAI News",
            "score": 9,
            "reason": "Major developer impact",
            "why_it_matters": "Developers should review the change.",
            "short_post": "OpenAI shipped a new developer update.",
            "source_url": "https://example.com/news",
        },
    )


def test_digest_sends_one_webhook_message_without_mentions():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = DiscordNotifier("https://discord.test/webhook", 10, client=client)

    notifier.send_digest([_alert("alert-1"), _alert("alert-2", "Model update")])

    assert len(requests) == 1
    payload = requests[0].read().decode()
    assert '"allowed_mentions":{"parse":[]}' in payload
    assert "AI News Radar Digest" in payload
    assert len(payload) < 2200


def test_digest_respects_discord_retry_after_on_rate_limit():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                429,
                headers={"Retry-After": "12.5"},
                json={"message": "rate limited", "retry_after": 12.5},
            )
        )
    )
    notifier = DiscordNotifier("https://discord.test/webhook", 10, client=client)

    with pytest.raises(DiscordRateLimitError) as error:
        notifier.send_digest([_alert()])

    assert error.value.retry_after_seconds == 12.5


def test_digest_treats_invalid_webhook_status_as_hard_failure():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(404, text="missing"))
    )
    notifier = DiscordNotifier("https://discord.test/webhook", 10, client=client)

    with pytest.raises(DiscordHardFailure) as error:
        notifier.send_digest([_alert()])

    assert error.value.status_code == 404
