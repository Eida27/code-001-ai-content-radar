from __future__ import annotations

import httpx

from worker.models import NewsItem, Score

UNCONFIRMED_REASON = "Unconfirmed: verify before posting"


class OpenRouterRateLimitError(RuntimeError):
    pass


class OpenRouterClient:
    def __init__(self, api_key: str, model: str, timeout_seconds: int) -> None:
        self.api_key = api_key
        self.model = model
        self._client = httpx.Client(
            base_url="https://openrouter.ai/api/v1",
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def generate_draft(self, item: NewsItem, score: Score) -> str:
        unconfirmed_instruction = _unconfirmed_instruction(score)
        response = self._client.post(
            "/chat/completions",
            json={
                "model": self.model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Write factual AI news draft copy as JSON only for "
                            "AI builders. Do not invent details beyond the source. "
                            "Return exactly these JSON keys: "
                            "image_overlay_caption, short_post. "
                            "The image_overlay_caption must be 1-2 sentences and "
                            "180 characters or fewer for a black gradient image overlay. "
                            "The short_post must be a single X/Twitter-ready post, "
                            "280 characters or fewer. Do not include the source URL "
                            "in short_post; reviewers will attach links separately."
                            f"{unconfirmed_instruction}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Title: {item.title}\n"
                            f"Summary: {item.raw_summary or ''}\n"
                            f"URL: {item.canonical_url}\n"
                            f"Importance: {score.reason}\n\n"
                            f"{_source_confidence_note(score)}"
                            "Return exactly these JSON keys: "
                            "image_overlay_caption, short_post."
                        ),
                    },
                ],
            },
        )
        if response.status_code in {402, 429}:
            raise OpenRouterRateLimitError(response.text)
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"]

    def close(self) -> None:
        self._client.close()


def _is_unconfirmed(score: Score) -> bool:
    return UNCONFIRMED_REASON.lower() in score.reason.lower()


def _unconfirmed_instruction(score: Score) -> str:
    if not _is_unconfirmed(score):
        return ""
    return (
        " This item is unconfirmed. Say that clearly, avoid definitive claims, "
        "and require manual verification. Do not describe it as launched, "
        "released, or announced unless an official source confirms it."
    )


def _source_confidence_note(score: Score) -> str:
    if not _is_unconfirmed(score):
        return ""
    return "Source confidence: unconfirmed. Requires manual confirmation before posting.\n"
