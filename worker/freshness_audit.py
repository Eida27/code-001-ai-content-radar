from __future__ import annotations

import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from worker.config import load_settings
from worker.fetchers.rss_fetcher import RSSFetcher
from worker.freshness import latest_published_at
from worker.services.supabase_client import SupabaseRestClient
from worker.sources import load_active_sources


def run_freshness_audit(
    *,
    db: Any,
    fetchers: Mapping[str, Any],
    timeout_seconds: int,
    retry_limit: int,
    freshness_window_hours: int,
) -> list[str]:
    lines: list[str] = []
    freshness_cutoff = datetime.now(timezone.utc) - timedelta(
        hours=freshness_window_hours
    )

    for source in load_active_sources(db):
        try:
            fetcher = _resolve_fetcher(fetchers, source)
            raw_items = fetcher.fetch(
                source,
                timeout_seconds=timeout_seconds,
                retry_limit=retry_limit,
            )
            latest_feed = latest_published_at(raw_items)
            latest_stored = _latest_stored_for_source(db, source)
            status = _source_status(latest_feed, latest_stored, freshness_cutoff)
            lines.append(
                " | ".join(
                    [
                        f"{source.name}: {status}",
                        f"entries={len(raw_items)}",
                        f"latest_feed={_format_dt(latest_feed)}",
                        f"latest_stored={_format_dt(latest_stored)}",
                    ]
                )
            )
        except Exception as error:
            lines.append(f"{source.name}: error | {type(error).__name__}: {error}")

    return lines


def main() -> None:
    settings = load_settings()
    db = SupabaseRestClient(
        settings.supabase_url,
        settings.supabase_service_role_key,
        settings.request_timeout_seconds,
    )
    fetchers = {"rss": RSSFetcher()}
    try:
        for line in run_freshness_audit(
            db=db,
            fetchers=fetchers,
            timeout_seconds=settings.source_timeout_seconds,
            retry_limit=settings.source_retry_limit,
            freshness_window_hours=settings.freshness_window_hours,
        ):
            print(line)
    finally:
        db.close()
        for fetcher in fetchers.values():
            close = getattr(fetcher, "close", None)
            if callable(close):
                close()


def _resolve_fetcher(fetchers: Mapping[str, Any], source: Any) -> Any:
    fetcher = fetchers.get(source.type)
    if isinstance(fetcher, Mapping):
        return fetcher[source.id]
    if fetcher is None:
        raise KeyError(f"No fetcher registered for source type {source.type}")
    return fetcher


def _latest_stored_for_source(db: Any, source: Any) -> datetime | None:
    loader = getattr(db, "get_latest_stored_published_at_by_source", None)
    if callable(loader):
        return loader(source.id)
    latest_by_source = getattr(db, "latest_stored_by_source", {})
    return latest_by_source.get(source.id) or getattr(
        source,
        "latest_stored_published_at",
        None,
    )


def _source_status(
    latest_feed: datetime | None,
    latest_stored: datetime | None,
    freshness_cutoff: datetime,
) -> str:
    if latest_feed is None:
        return "no-dated-items"
    if latest_feed < freshness_cutoff:
        return "no-fresh-feed-items"
    if latest_stored is None or latest_stored < latest_feed:
        return "fresh-feed-ahead-of-storage"
    return "fresh"


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "none"
    return value.astimezone(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
