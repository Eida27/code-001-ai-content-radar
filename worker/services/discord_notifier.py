from __future__ import annotations

import httpx

from worker.models import Draft, NewsItem, Score


class DiscordNotifier:
    def __init__(self, webhook_url: str, timeout_seconds: int) -> None:
        self.webhook_url = webhook_url
        self._client = httpx.Client(timeout=timeout_seconds)

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
        self._client.close()
