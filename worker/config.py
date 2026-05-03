from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    supabase_url: str
    supabase_service_role_key: str
    openrouter_api_key: str
    openrouter_default_model: str
    openrouter_fallback_model: str
    allow_paid_fallback: bool
    discord_webhook_url: str
    min_importance_score: int
    max_ai_drafts_per_day: int
    max_items_per_run: int
    max_ai_calls_per_run: int
    max_paid_fallbacks_per_run: int
    source_timeout_seconds: int
    source_retry_limit: int
    run_timeout_seconds: int
    request_timeout_seconds: int
    openrouter_daily_free_request_limit: int
    cleanup_enabled: bool
    retention_log_days: int
    retention_ai_raw_response_days: int
    retention_low_priority_days: int
    retention_worker_run_days: int
    cleanup_batch_limit: int
    discord_max_items_per_digest: int


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        supabase_url=os.getenv("SUPABASE_URL", "").rstrip("/"),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        openrouter_default_model=os.getenv(
            "OPENROUTER_DEFAULT_MODEL", "google/gemma-4-31b-it:free"
        ),
        openrouter_fallback_model=os.getenv(
            "OPENROUTER_FALLBACK_MODEL", "google/gemma-4-31b-it"
        ),
        allow_paid_fallback=_bool_env("ALLOW_PAID_FALLBACK", False),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
        min_importance_score=_int_env("MIN_IMPORTANCE_SCORE", 7),
        max_ai_drafts_per_day=_int_env("MAX_AI_DRAFTS_PER_DAY", 35),
        max_items_per_run=_int_env("MAX_ITEMS_PER_RUN", 30),
        max_ai_calls_per_run=_int_env("MAX_AI_CALLS_PER_RUN", 4),
        max_paid_fallbacks_per_run=_int_env("MAX_PAID_FALLBACKS_PER_RUN", 1),
        source_timeout_seconds=_int_env("SOURCE_TIMEOUT_SECONDS", 20),
        source_retry_limit=_int_env("SOURCE_RETRY_LIMIT", 1),
        run_timeout_seconds=_int_env("RUN_TIMEOUT_SECONDS", 720),
        request_timeout_seconds=_int_env("REQUEST_TIMEOUT_SECONDS", 20),
        openrouter_daily_free_request_limit=_int_env(
            "OPENROUTER_DAILY_FREE_REQUEST_LIMIT", 50
        ),
        cleanup_enabled=_bool_env("CLEANUP_ENABLED", True),
        retention_log_days=_int_env("RETENTION_LOG_DAYS", 30),
        retention_ai_raw_response_days=_int_env("RETENTION_AI_RAW_RESPONSE_DAYS", 14),
        retention_low_priority_days=_int_env("RETENTION_LOW_PRIORITY_DAYS", 30),
        retention_worker_run_days=_int_env("RETENTION_WORKER_RUN_DAYS", 90),
        cleanup_batch_limit=_int_env("CLEANUP_BATCH_LIMIT", 100),
        discord_max_items_per_digest=_int_env("DISCORD_MAX_ITEMS_PER_DIGEST", 10),
    )
