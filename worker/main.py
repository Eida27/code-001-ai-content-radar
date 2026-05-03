from __future__ import annotations

import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from worker.config import Settings, load_settings
from worker.fetchers.rss_fetcher import RSSFetcher
from worker.processing.draft_generator import DraftParseError, parse_ai_draft_response
from worker.processing.dedupe import dedupe_in_memory
from worker.processing.normalize import normalize_items
from worker.processing.scorer import score_item
from worker.services.discord_notifier import DiscordNotifier
from worker.services.openrouter_client import OpenRouterClient, OpenRouterRateLimitError
from worker.services.supabase_client import DuplicateItemError, SupabaseRestClient
from worker.sources import load_active_sources


def run_worker(
    *,
    settings: Settings,
    db: Any,
    fetchers: Mapping[str, Any],
    openrouter: Any,
    discord: Any,
) -> None:
    run_id = db.start_worker_run()
    deadline = time.monotonic() + settings.run_timeout_seconds
    drafts_created_today = db.count_drafts_created_today()
    free_requests_today = db.count_ai_requests_today(settings.openrouter_default_model)
    ai_disabled_for_run = False
    processed_items = 0

    try:
        sources = load_active_sources(db)
        for source in sources:
            if time.monotonic() >= deadline:
                db.log_event(
                    "warning",
                    "worker",
                    "Run deadline reached before all sources were processed",
                    {"source_id": source.id},
                )
                break

            try:
                fetcher = _resolve_fetcher(fetchers, source)
                raw_items = fetcher.fetch(
                    source,
                    timeout_seconds=settings.source_timeout_seconds,
                    retry_limit=settings.source_retry_limit,
                )
            except Exception as error:
                db.log_event(
                    "error",
                    "fetcher",
                    f"Source {source.name} failed: {error}",
                    {"source_id": source.id, "source_type": source.type},
                )
                continue

            items = dedupe_in_memory(normalize_items(raw_items, source))
            for item in items:
                if processed_items >= settings.max_items_per_run:
                    break
                processed_items += 1

                score = score_item(item, source)
                item.importance_score = score.value
                item.importance_reason = score.reason

                if score.value < settings.min_importance_score:
                    _save_item_or_skip(db, item, status="low_priority")
                    continue

                saved_item = _save_item_or_skip(db, item, status="scored")
                if saved_item is None:
                    continue

                if drafts_created_today >= settings.max_ai_drafts_per_day:
                    db.log_event(
                        "info",
                        "worker",
                        "Daily AI draft limit reached",
                        {"news_item_title": saved_item.title},
                    )
                    continue

                if _free_model_budget_exhausted(settings, free_requests_today):
                    db.log_event(
                        "warning",
                        "openrouter",
                        "Daily free-model request limit reached",
                        {"model": settings.openrouter_default_model},
                    )
                    ai_disabled_for_run = True

                if ai_disabled_for_run:
                    continue

                try:
                    raw_response = openrouter.generate_draft(saved_item, score)
                    free_requests_today += 1
                except OpenRouterRateLimitError as error:
                    db.save_ai_request(
                        news_item_id=saved_item.id,
                        model_used=settings.openrouter_default_model,
                        status="rate_limited",
                        raw_response=str(error),
                        parse_error=None,
                    )
                    db.update_item_status(saved_item.id, "draft_failed")
                    saved_item.status = "draft_failed"
                    ai_disabled_for_run = True
                    db.log_event(
                        "warning",
                        "openrouter",
                        "OpenRouter rate limit reached; disabling AI for this run",
                        {"news_item_title": saved_item.title},
                    )
                    continue
                except Exception as error:
                    db.save_ai_request(
                        news_item_id=saved_item.id,
                        model_used=settings.openrouter_default_model,
                        status="error",
                        raw_response=str(error),
                        parse_error=None,
                    )
                    db.update_item_status(saved_item.id, "draft_failed")
                    saved_item.status = "draft_failed"
                    db.log_event(
                        "error",
                        "openrouter",
                        f"AI draft generation failed: {error}",
                        {"news_item_title": saved_item.title},
                    )
                    continue

                try:
                    draft_package = parse_ai_draft_response(
                        raw_response,
                        model_used=settings.openrouter_default_model,
                    )
                except DraftParseError as error:
                    db.save_ai_request(
                        news_item_id=saved_item.id,
                        model_used=settings.openrouter_default_model,
                        status="parse_error",
                        raw_response=error.raw_response,
                        parse_error=error.parse_error,
                    )
                    db.update_item_status(saved_item.id, "draft_failed")
                    saved_item.status = "draft_failed"
                    db.log_event(
                        "error",
                        "openrouter",
                        "AI response failed JSON validation",
                        {"parse_error": error.parse_error},
                    )
                    continue

                db.save_ai_request(
                    news_item_id=saved_item.id,
                    model_used=settings.openrouter_default_model,
                    status="success",
                    raw_response=draft_package.raw_response,
                    parse_error=None,
                )
                db.save_drafts(saved_item.id, draft_package.drafts)
                drafts_created_today += 1
                db.update_item_status(saved_item.id, "needs_review")
                saved_item.status = "needs_review"

                try:
                    discord.send_alert(saved_item, draft_package.drafts, score)
                except Exception as error:
                    db.log_event(
                        "error",
                        "discord",
                        f"Discord webhook failed: {error}",
                        {"news_item_title": saved_item.title},
                    )

        db.finish_worker_run(
            run_id,
            status="success",
            metadata={"items_processed": processed_items},
        )
    except Exception as error:
        db.log_event("error", "worker", f"Worker run failed: {error}")
        db.finish_worker_run(run_id, status="error", metadata={"error": str(error)})
        raise
    finally:
        _close_resources(db, openrouter, discord, fetchers)


def main() -> None:
    settings = load_settings()
    db = SupabaseRestClient(
        settings.supabase_url,
        settings.supabase_service_role_key,
        settings.request_timeout_seconds,
    )
    fetchers = {"rss": RSSFetcher()}
    openrouter = OpenRouterClient(
        settings.openrouter_api_key,
        settings.openrouter_default_model,
        settings.request_timeout_seconds,
    )
    discord = DiscordNotifier(settings.discord_webhook_url, settings.request_timeout_seconds)
    run_worker(
        settings=settings,
        db=db,
        fetchers=fetchers,
        openrouter=openrouter,
        discord=discord,
    )


def _free_model_budget_exhausted(settings: Settings, free_requests_today: int) -> bool:
    return (
        settings.openrouter_default_model.endswith(":free")
        and free_requests_today >= settings.openrouter_daily_free_request_limit
    )


def _save_item_or_skip(db: Any, item: Any, status: str) -> Any | None:
    try:
        return db.save_item(item, status=status)
    except DuplicateItemError as error:
        db.log_event(
            "debug",
            "dedupe",
            "Duplicate item skipped",
            {
                "title": item.title,
                "canonical_url": item.canonical_url,
                "error": str(error),
            },
        )
        return None


def _resolve_fetcher(fetchers: Mapping[str, Any], source) -> Any:
    fetcher = fetchers.get(source.type)
    if isinstance(fetcher, Mapping):
        return fetcher[source.id]
    if fetcher is None:
        raise KeyError(f"No fetcher registered for source type {source.type}")
    return fetcher


def _close_resources(*resources: Any) -> None:
    seen: set[int] = set()

    def close_one(resource: Any) -> None:
        if id(resource) in seen:
            return
        seen.add(id(resource))
        close = getattr(resource, "close", None)
        if callable(close):
            close()

    for resource in resources:
        if isinstance(resource, Mapping):
            for value in resource.values():
                if isinstance(value, Mapping):
                    for nested_value in value.values():
                        close_one(nested_value)
                else:
                    close_one(value)
        else:
            close_one(resource)


if __name__ == "__main__":
    main()
