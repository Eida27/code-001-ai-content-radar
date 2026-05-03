from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import httpx

from worker.config import Settings, load_settings
from worker.fetchers.rss_fetcher import RSSFetcher
from worker.main import run_worker
from worker.services.discord_notifier import DiscordNotifier
from worker.services.openrouter_client import OpenRouterClient
from worker.services.supabase_client import SupabaseRestClient


REQUIRED_ENV_FIELDS = [
    "supabase_url",
    "supabase_service_role_key",
    "openrouter_api_key",
    "openrouter_default_model",
    "discord_webhook_url",
]


@dataclass(frozen=True, slots=True)
class SmokeResult:
    name: str
    ok: bool
    detail: str = ""


def check_env(settings: Settings) -> SmokeResult:
    missing = [field for field in REQUIRED_ENV_FIELDS if not getattr(settings, field)]
    if missing:
        return SmokeResult("ENV", False, f"missing: {', '.join(missing)}")
    return SmokeResult("ENV", True)


def check_supabase_connection(db: Any) -> SmokeResult:
    try:
        sources = db.load_active_sources()
    except Exception as error:
        return SmokeResult("SUPABASE", False, str(error))
    return SmokeResult("SUPABASE", True, f"active_sources={len(sources)}")


def check_discord_webhook(webhook_url: str, client: Any = httpx) -> SmokeResult:
    try:
        response = client.get(webhook_url, timeout=20)
    except Exception as error:
        return SmokeResult("DISCORD_WEBHOOK", False, str(error))
    return SmokeResult("DISCORD_WEBHOOK", response.status_code == 200, str(response.status_code))


def check_openrouter_key(api_key: str, client: Any = httpx) -> SmokeResult:
    try:
        response = client.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
    except Exception as error:
        return SmokeResult("OPENROUTER_KEY", False, str(error))
    return SmokeResult("OPENROUTER_KEY", response.status_code == 200, str(response.status_code))


def run_worker_no_ai_smoke(
    *,
    settings: Settings,
    db: Any,
    fetchers: dict[str, Any],
    openrouter: Any,
    discord: Any,
) -> SmokeResult:
    smoke_settings = replace(
        settings,
        max_items_per_run=3,
        max_ai_drafts_per_day=0,
    )
    try:
        run_worker(
            settings=smoke_settings,
            db=db,
            fetchers=fetchers,
            openrouter=openrouter,
            discord=discord,
        )
    except Exception as error:
        return SmokeResult("WORKER_NO_AI_SMOKE", False, str(error))
    return SmokeResult("WORKER_NO_AI_SMOKE", True)


def main() -> None:
    settings = load_settings()
    env_result = check_env(settings)
    print(_format_result(env_result))
    if not env_result.ok:
        raise SystemExit(1)

    db = SupabaseRestClient(
        settings.supabase_url,
        settings.supabase_service_role_key,
        settings.request_timeout_seconds,
    )
    openrouter = OpenRouterClient(
        settings.openrouter_api_key,
        settings.openrouter_default_model,
        settings.request_timeout_seconds,
    )
    discord = DiscordNotifier(settings.discord_webhook_url, settings.request_timeout_seconds)
    fetchers = {"rss": RSSFetcher()}

    results = [
        check_supabase_connection(db),
        check_discord_webhook(settings.discord_webhook_url),
        check_openrouter_key(settings.openrouter_api_key),
        run_worker_no_ai_smoke(
            settings=settings,
            db=db,
            fetchers=fetchers,
            openrouter=openrouter,
            discord=discord,
        ),
    ]

    failed = False
    for result in results:
        print(_format_result(result))
        failed = failed or not result.ok

    if failed:
        raise SystemExit(1)


def _format_result(result: SmokeResult) -> str:
    status = "ok" if result.ok else "failed"
    if result.detail:
        return f"{result.name}: {status} ({result.detail})"
    return f"{result.name}: {status}"


if __name__ == "__main__":
    main()
