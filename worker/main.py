from __future__ import annotations

import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from worker.config import Settings, load_settings
from worker.fetchers.rss_fetcher import RSSFetcher
from worker.models import Score
from worker.processing.draft_generator import DraftParseError, parse_ai_draft_response
from worker.processing.dedupe import dedupe_in_memory
from worker.processing.normalize import normalize_items
from worker.processing.scorer import score_item
from worker.services.discord_notifier import DiscordNotifier
from worker.services.openrouter_client import OpenRouterClient, OpenRouterRateLimitError
from worker.services.supabase_client import DuplicateItemError, SupabaseRestClient
from worker.sources import load_active_sources


@dataclass(slots=True)
class DraftAttempt:
    status: str
    ai_calls_used: int = 0
    paid_fallbacks_used: int = 0


def run_worker(
    *,
    settings: Settings,
    db: Any,
    fetchers: Mapping[str, Any],
    openrouter: Any,
    fallback_openrouter: Any | None = None,
    discord: Any,
) -> None:
    run_id = db.start_worker_run()
    deadline = time.monotonic() + settings.run_timeout_seconds
    drafts_created_today = db.count_drafts_created_today()
    free_requests_today = db.count_ai_requests_today(settings.openrouter_default_model)
    ai_calls_this_run = 0
    paid_fallbacks_this_run = 0
    ai_disabled_for_run = False
    processed_items = 0

    try:
        (
            ai_disabled_for_run,
            drafts_created_today,
            free_requests_today,
            run_state,
        ) = _process_retryable_scored_items(
            settings=settings,
            db=db,
            openrouter=openrouter,
            fallback_openrouter=fallback_openrouter,
            discord=discord,
            ai_disabled_for_run=ai_disabled_for_run,
            drafts_created_today=drafts_created_today,
            free_requests_today=free_requests_today,
            ai_calls_this_run=ai_calls_this_run,
            paid_fallbacks_this_run=paid_fallbacks_this_run,
        )
        ai_calls_this_run = run_state["ai_calls_this_run"]
        paid_fallbacks_this_run = run_state["paid_fallbacks_this_run"]

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

                if _run_ai_budget_exhausted(settings, ai_calls_this_run):
                    db.log_event(
                        "warning",
                        "openrouter",
                        "Per-run AI call limit reached",
                        {"max_ai_calls_per_run": settings.max_ai_calls_per_run},
                    )
                    ai_disabled_for_run = True

                if ai_disabled_for_run:
                    continue

                draft_attempt = _draft_item(
                    db=db,
                    openrouter=openrouter,
                    fallback_openrouter=fallback_openrouter,
                    discord=discord,
                    settings=settings,
                    saved_item=saved_item,
                    score=score,
                    model_used=settings.openrouter_default_model,
                    ai_calls_this_run=ai_calls_this_run,
                    paid_fallbacks_this_run=paid_fallbacks_this_run,
                )
                ai_calls_this_run += draft_attempt.ai_calls_used
                paid_fallbacks_this_run += draft_attempt.paid_fallbacks_used
                if draft_attempt.ai_calls_used:
                    free_requests_today += 1
                if draft_attempt.status == "success":
                    drafts_created_today += 1
                elif draft_attempt.status == "rate_limited":
                    ai_disabled_for_run = True

        db.finish_worker_run(
            run_id,
            status="success",
            metadata={"items_processed": processed_items},
        )
    except BaseException as error:
        db.log_event("error", "worker", f"Worker run failed: {error}")
        db.finish_worker_run(
            run_id,
            status="error",
            metadata={"error": str(error), "error_type": type(error).__name__},
        )
        raise
    finally:
        _close_resources(db, openrouter, fallback_openrouter, discord, fetchers)


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
    fallback_openrouter = None
    if settings.allow_paid_fallback:
        fallback_openrouter = OpenRouterClient(
            settings.openrouter_api_key,
            settings.openrouter_fallback_model,
            settings.request_timeout_seconds,
        )
    discord = DiscordNotifier(settings.discord_webhook_url, settings.request_timeout_seconds)
    run_worker(
        settings=settings,
        db=db,
        fetchers=fetchers,
        openrouter=openrouter,
        fallback_openrouter=fallback_openrouter,
        discord=discord,
    )


def _process_retryable_scored_items(
    *,
    settings: Settings,
    db: Any,
    openrouter: Any,
    fallback_openrouter: Any | None,
    discord: Any,
    ai_disabled_for_run: bool,
    drafts_created_today: int,
    free_requests_today: int,
    ai_calls_this_run: int,
    paid_fallbacks_this_run: int,
) -> tuple[bool, int, int, dict[str, int]]:
    retry_loader = getattr(db, "load_items_ready_for_drafting", None)
    if not callable(retry_loader):
        return (
            ai_disabled_for_run,
            drafts_created_today,
            free_requests_today,
            {
                "ai_calls_this_run": ai_calls_this_run,
                "paid_fallbacks_this_run": paid_fallbacks_this_run,
            },
        )

    retry_limit = max(settings.max_items_per_run, 0)
    retryable_items = retry_loader(retry_limit, settings.min_importance_score)

    for saved_item in retryable_items:
        if drafts_created_today >= settings.max_ai_drafts_per_day:
            break
        if _free_model_budget_exhausted(settings, free_requests_today):
            db.log_event(
                "warning",
                "openrouter",
                "Daily free-model request limit reached",
                {"model": settings.openrouter_default_model},
            )
            break
        if _run_ai_budget_exhausted(settings, ai_calls_this_run):
            db.log_event(
                "warning",
                "openrouter",
                "Per-run AI call limit reached",
                {"max_ai_calls_per_run": settings.max_ai_calls_per_run},
            )
            break
        if ai_disabled_for_run:
            break

        score = Score(
            value=saved_item.importance_score or settings.min_importance_score,
            reason=saved_item.importance_reason or "Retrying previously scored item",
        )
        draft_attempt = _draft_item(
            db=db,
            openrouter=openrouter,
            fallback_openrouter=fallback_openrouter,
            discord=discord,
            settings=settings,
            saved_item=saved_item,
            score=score,
            model_used=settings.openrouter_default_model,
            ai_calls_this_run=ai_calls_this_run,
            paid_fallbacks_this_run=paid_fallbacks_this_run,
        )
        ai_calls_this_run += draft_attempt.ai_calls_used
        paid_fallbacks_this_run += draft_attempt.paid_fallbacks_used
        if draft_attempt.ai_calls_used:
            free_requests_today += 1
        if draft_attempt.status == "success":
            drafts_created_today += 1
        elif draft_attempt.status == "rate_limited":
            ai_disabled_for_run = True

    return (
        ai_disabled_for_run,
        drafts_created_today,
        free_requests_today,
        {
            "ai_calls_this_run": ai_calls_this_run,
            "paid_fallbacks_this_run": paid_fallbacks_this_run,
        },
    )


def _draft_item(
    *,
    db: Any,
    openrouter: Any,
    fallback_openrouter: Any | None,
    discord: Any,
    settings: Settings,
    saved_item: Any,
    score: Score,
    model_used: str,
    ai_calls_this_run: int,
    paid_fallbacks_this_run: int,
) -> DraftAttempt:
    try:
        raw_response = openrouter.generate_draft(saved_item, score)
    except OpenRouterRateLimitError as error:
        db.save_ai_request(
            news_item_id=saved_item.id,
            model_used=model_used,
            status="rate_limited",
            raw_response=str(error),
            parse_error=None,
        )
        if settings.allow_paid_fallback and fallback_openrouter is not None:
            if paid_fallbacks_this_run >= settings.max_paid_fallbacks_per_run:
                saved_item.status = "scored"
                db.log_event(
                    "warning",
                    "openrouter",
                    "Paid fallback limit reached; leaving item scored for retry",
                    {
                        "news_item_title": saved_item.title,
                        "max_paid_fallbacks_per_run": settings.max_paid_fallbacks_per_run,
                    },
                )
                return DraftAttempt("rate_limited", ai_calls_used=1)
            if ai_calls_this_run + 1 >= settings.max_ai_calls_per_run:
                saved_item.status = "scored"
                db.log_event(
                    "warning",
                    "openrouter",
                    "Per-run AI call limit reached before paid fallback",
                    {"max_ai_calls_per_run": settings.max_ai_calls_per_run},
                )
                return DraftAttempt("rate_limited", ai_calls_used=1)
            db.log_event(
                "warning",
                "openrouter",
                "Default OpenRouter model rate-limited; trying fallback model",
                {
                    "news_item_title": saved_item.title,
                    "fallback_model": settings.openrouter_fallback_model,
                },
            )
            fallback_attempt = _draft_item(
                db=db,
                openrouter=fallback_openrouter,
                fallback_openrouter=None,
                discord=discord,
                settings=settings,
                saved_item=saved_item,
                score=score,
                model_used=settings.openrouter_fallback_model,
                ai_calls_this_run=ai_calls_this_run + 1,
                paid_fallbacks_this_run=paid_fallbacks_this_run + 1,
            )
            return DraftAttempt(
                fallback_attempt.status,
                ai_calls_used=fallback_attempt.ai_calls_used + 1,
                paid_fallbacks_used=fallback_attempt.paid_fallbacks_used,
            )

        saved_item.status = "scored"
        db.log_event(
            "warning",
            "openrouter",
            "OpenRouter rate limit reached; disabling AI for this run",
            {"news_item_title": saved_item.title},
        )
        return DraftAttempt("rate_limited", ai_calls_used=1)
    except Exception as error:
        db.save_ai_request(
            news_item_id=saved_item.id,
            model_used=model_used,
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
        return DraftAttempt("error", ai_calls_used=1)

    try:
        draft_package = parse_ai_draft_response(raw_response, model_used=model_used)
    except DraftParseError as error:
        db.save_ai_request(
            news_item_id=saved_item.id,
            model_used=model_used,
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
        return DraftAttempt("parse_error", ai_calls_used=1)

    db.save_ai_request(
        news_item_id=saved_item.id,
        model_used=model_used,
        status="success",
        raw_response=draft_package.raw_response,
        parse_error=None,
    )
    db.save_drafts(saved_item.id, draft_package.drafts)
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

    paid_fallback_used = 1 if model_used == settings.openrouter_fallback_model else 0
    return DraftAttempt(
        "success",
        ai_calls_used=1,
        paid_fallbacks_used=paid_fallback_used,
    )


def _free_model_budget_exhausted(settings: Settings, free_requests_today: int) -> bool:
    return (
        settings.openrouter_default_model.endswith(":free")
        and free_requests_today >= settings.openrouter_daily_free_request_limit
    )


def _run_ai_budget_exhausted(settings: Settings, ai_calls_this_run: int) -> bool:
    return ai_calls_this_run >= settings.max_ai_calls_per_run


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
