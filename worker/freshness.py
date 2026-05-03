from __future__ import annotations

from datetime import datetime, timedelta, timezone

from worker.models import NewsItem, RawItem


def latest_published_at(raw_items: list[RawItem]) -> datetime | None:
    published_dates = [
        item.published_at.astimezone(timezone.utc)
        for item in raw_items
        if item.published_at is not None
    ]
    if not published_dates:
        return None
    return max(published_dates)


def is_fresh_news_item(
    item: NewsItem,
    freshness_window_hours: int,
    *,
    now: datetime | None = None,
) -> bool:
    if item.published_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    published_at = item.published_at.astimezone(timezone.utc)
    return published_at >= now - timedelta(hours=freshness_window_hours)
