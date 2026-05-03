from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import sleep
from typing import Any

import feedparser
import httpx

from worker.models import RawItem, Source


class RSSFetchError(RuntimeError):
    pass


class RSSFetcher:
    def __init__(self) -> None:
        self._client = httpx.Client(follow_redirects=True)

    def fetch(
        self, source: Source, timeout_seconds: int, retry_limit: int
    ) -> list[RawItem]:
        last_error: Exception | None = None
        attempts = retry_limit + 1

        for attempt in range(attempts):
            try:
                response = self._client.get(source.url, timeout=timeout_seconds)
                response.raise_for_status()
                parsed = feedparser.parse(response.content)
                if parsed.bozo and not parsed.entries:
                    raise RSSFetchError(str(parsed.bozo_exception))
                return [_entry_to_raw_item(entry) for entry in parsed.entries]
            except Exception as error:
                last_error = error
                if attempt < attempts - 1:
                    sleep(0.25)

        raise RSSFetchError(f"Failed to fetch RSS source {source.name}: {last_error}")

    def close(self) -> None:
        self._client.close()


def _entry_to_raw_item(entry: dict[str, Any]) -> RawItem:
    published_at = None
    published = entry.get("published") or entry.get("updated")
    if published:
        try:
            parsed_date = parsedate_to_datetime(published)
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            published_at = parsed_date
        except (TypeError, ValueError):
            published_at = None

    return RawItem(
        title=entry.get("title", "").strip(),
        url=entry.get("link", "").strip(),
        raw_summary=(entry.get("summary") or entry.get("description") or "").strip(),
        published_at=published_at,
        metadata={"id": entry.get("id")},
    )
