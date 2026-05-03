from __future__ import annotations

import httpx

from worker.models import NewsItem, Score


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
        response = self._client.post(
            "/chat/completions",
            json={
                "model": self.model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Write factual X draft options as JSON only. "
                            "Do not invent details beyond the source."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Title: {item.title}\n"
                            f"Summary: {item.raw_summary or ''}\n"
                            f"URL: {item.canonical_url}\n"
                            f"Importance: {score.reason}\n\n"
                            "Return keys: short_post, long_post, thread, "
                            "why_it_matters, risk_note."
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
