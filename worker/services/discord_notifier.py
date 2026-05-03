from __future__ import annotations

import httpx

from worker.models import DiscordAlert, Draft, NewsItem, Score


class DiscordRateLimitError(RuntimeError):
    def __init__(self, message: str, retry_after_seconds: float) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.status_code = 429


class DiscordHardFailure(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class DiscordNotifier:
    def __init__(
        self,
        webhook_url: str,
        timeout_seconds: int,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def send_digest(self, alerts: list[DiscordAlert]) -> None:
        if not alerts:
            return

        content = _trim_discord_content(_build_digest_content(alerts))
        response = self._client.post(
            self.webhook_url,
            json={"content": content, "allowed_mentions": {"parse": []}},
        )

        if response.status_code == 429:
            retry_after = _retry_after_seconds(response)
            raise DiscordRateLimitError("Discord webhook rate limited", retry_after)
        if response.status_code in {401, 403, 404}:
            raise DiscordHardFailure(
                f"Discord webhook rejected request with {response.status_code}",
                response.status_code,
            )
        response.raise_for_status()

    def send_alert(self, item: NewsItem, drafts: list[Draft], score: Score) -> None:
        short_post = next(
            (draft.content for draft in drafts if draft.draft_type == "short_post"),
            drafts[0].content if drafts else "",
        )
        why_it_matters = next(
            (draft.content for draft in drafts if draft.draft_type == "why_it_matters"),
            "",
        )
        content = (
            "AI Update Found\n\n"
            f"Title: {item.title}\n"
            f"Source: {item.source_name or 'Unknown'}\n"
            f"Score: {item.importance_score}/10\n"
            f"Reason: {score.reason}\n\n"
            f"Why it matters:\n{why_it_matters}\n\n"
            f"Draft:\n{short_post}\n\n"
            f"Source:\n{item.canonical_url}"
        )
        response = self._client.post(
            self.webhook_url,
            json={"content": content[:2000], "allowed_mentions": {"parse": []}},
        )
        response.raise_for_status()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _build_digest_content(alerts: list[DiscordAlert]) -> str:
    lines = [
        "AI News Radar Digest",
        "",
        f"{len(alerts)} draft(s) ready for review.",
        "",
    ]
    for index, alert in enumerate(alerts, start=1):
        payload = alert.payload
        lines.extend(
            [
                f"{index}. {payload.get('title') or 'Untitled'}",
                f"Source: {payload.get('source_name') or 'Unknown'}",
                f"Score: {payload.get('score') or 'n/a'}/10",
                f"Reason: {payload.get('reason') or 'No reason recorded.'}",
                f"Why: {payload.get('why_it_matters') or 'No summary recorded.'}",
                f"Draft: {payload.get('short_post') or 'No draft recorded.'}",
                f"Link: {payload.get('source_url') or ''}",
                "",
            ]
        )
    return "\n".join(lines).strip()


def _trim_discord_content(content: str) -> str:
    if len(content) <= 2000:
        return content
    return f"{content[:1997]}..."


def _retry_after_seconds(response: httpx.Response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        return float(retry_after)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    retry_after = payload.get("retry_after")
    if retry_after is not None:
        return float(retry_after)
    retry_after = response.headers.get("X-RateLimit-Reset-After")
    if retry_after:
        return float(retry_after)
    return 60.0
