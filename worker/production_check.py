from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from worker.config import Settings, load_settings
from worker.fetchers.rss_fetcher import RSSFetcher
from worker.freshness_audit import run_freshness_audit
from worker.services.supabase_client import SupabaseRestClient


REQUIRED_ENV_FIELDS = [
    "supabase_url",
    "supabase_service_role_key",
    "openrouter_api_key",
    "openrouter_default_model",
    "discord_webhook_url",
]

REQUIRED_TABLES = [
    "sources",
    "news_items",
    "drafts",
    "posts",
    "worker_runs",
    "logs",
    "ai_requests",
    "discord_alerts",
    "worker_locks",
]

REQUIRED_INDEXES = [
    "sources_active_idx",
    "news_items_source_id_idx",
    "news_items_published_at_idx",
    "news_items_status_idx",
    "news_items_created_at_idx",
    "drafts_news_item_id_idx",
    "drafts_news_item_id_draft_type_unique_idx",
    "discord_alerts_pending_idx",
    "worker_locks_expires_at_idx",
]

REQUIRED_FUNCTIONS = [
    "try_acquire_worker_lock",
    "release_worker_lock",
    "production_readiness_snapshot",
]


class CheckStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ProductionCheckReport:
    results: list[CheckResult]

    @property
    def ok(self) -> bool:
        return all(result.status is not CheckStatus.FAIL for result in self.results)


def run_production_check(
    *,
    settings: Settings,
    db: Any,
    fetchers: Mapping[str, Any],
    http_client: Any,
) -> ProductionCheckReport:
    results: list[CheckResult] = []
    try:
        results.append(check_env(settings))
        results.append(check_cost_defaults(settings))
        results.extend(evaluate_supabase_snapshot(settings, db.get_production_readiness_snapshot()))
        results.append(
            check_openrouter_model(
                settings.openrouter_api_key,
                settings.openrouter_default_model,
                client=http_client,
            )
        )
        results.append(check_discord_webhook(settings.discord_webhook_url, client=http_client))
        results.append(check_rss_freshness(settings, db, fetchers))
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()
        _close_fetchers(fetchers)

    return ProductionCheckReport(results)


def check_env(settings: Settings) -> CheckResult:
    missing = [field for field in REQUIRED_ENV_FIELDS if not getattr(settings, field)]
    if missing:
        return CheckResult("ENV", CheckStatus.FAIL, f"missing: {', '.join(missing)}")
    return CheckResult("ENV", CheckStatus.OK)


def check_cost_defaults(settings: Settings) -> CheckResult:
    if not settings.allow_paid_fallback and settings.max_paid_fallbacks_per_run == 0:
        return CheckResult("COST_DEFAULTS", CheckStatus.OK, "free-first")
    if settings.allow_paid_fallback and settings.max_paid_fallbacks_per_run > 0:
        return CheckResult(
            "COST_DEFAULTS",
            CheckStatus.WARNING,
            (
                "paid fallback enabled with explicit cap "
                f"{settings.max_paid_fallbacks_per_run}/run"
            ),
        )
    return CheckResult(
        "COST_DEFAULTS",
        CheckStatus.FAIL,
        "paid fallback is enabled without a positive per-run cap",
    )


def evaluate_supabase_snapshot(
    settings: Settings,
    snapshot: dict[str, Any],
) -> list[CheckResult]:
    table_counts = snapshot.get("table_counts") or {}
    rls = snapshot.get("rls") or {}
    index_names = set(snapshot.get("index_names") or [])
    function_names = set(snapshot.get("function_names") or [])

    results = [
        _threshold_result(
            "SUPABASE_DB_SIZE",
            int(snapshot.get("db_size_bytes") or 0),
            settings.production_db_warning_mb,
            settings.production_db_fail_mb,
        ),
        _threshold_result(
            "SUPABASE_STORAGE_SIZE",
            int(snapshot.get("storage_bytes") or 0),
            settings.production_storage_warning_mb,
            settings.production_storage_fail_mb,
        ),
    ]

    missing_tables = [table for table in REQUIRED_TABLES if table not in table_counts]
    results.append(
        CheckResult(
            "SUPABASE_TABLES",
            CheckStatus.FAIL if missing_tables else CheckStatus.OK,
            f"missing: {', '.join(missing_tables)}" if missing_tables else "",
        )
    )

    rls_missing = [table for table in REQUIRED_TABLES if rls.get(table) is not True]
    results.append(
        CheckResult(
            "SUPABASE_RLS",
            CheckStatus.FAIL if rls_missing else CheckStatus.OK,
            f"not enabled: {', '.join(rls_missing)}" if rls_missing else "",
        )
    )

    missing_indexes = [index for index in REQUIRED_INDEXES if index not in index_names]
    results.append(
        CheckResult(
            "SUPABASE_INDEXES",
            CheckStatus.FAIL if missing_indexes else CheckStatus.OK,
            f"missing: {', '.join(missing_indexes)}" if missing_indexes else "",
        )
    )

    missing_functions = [
        function_name
        for function_name in REQUIRED_FUNCTIONS
        if function_name not in function_names
    ]
    results.append(
        CheckResult(
            "SUPABASE_RPCS",
            CheckStatus.FAIL if missing_functions else CheckStatus.OK,
            f"missing: {', '.join(missing_functions)}" if missing_functions else "",
        )
    )

    results.extend(
        [
            _zero_required_result(
                "DRAFT_DUPLICATES",
                int(snapshot.get("duplicate_draft_groups") or 0),
            ),
            _zero_required_result(
                "NEWS_DEDUPE",
                int(snapshot.get("duplicate_news_hash_groups") or 0),
            ),
            _zero_required_result(
                "WORKER_RUNNING_RUNS",
                int(snapshot.get("running_worker_runs") or 0),
            ),
        ]
    )
    return results


def check_openrouter_model(
    api_key: str,
    model: str,
    *,
    client: Any = httpx,
) -> CheckResult:
    try:
        key_response = client.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
        if key_response.status_code != 200:
            return CheckResult(
                "OPENROUTER_KEY",
                CheckStatus.FAIL,
                f"status={key_response.status_code}",
            )

        models_response = client.get(
            "https://openrouter.ai/api/v1/models",
            timeout=20,
        )
        if models_response.status_code != 200:
            return CheckResult(
                "OPENROUTER_MODEL",
                CheckStatus.FAIL,
                f"status={models_response.status_code}",
            )
        models = models_response.json().get("data") or []
    except Exception as error:
        return CheckResult("OPENROUTER_MODEL", CheckStatus.FAIL, str(error))

    if any(entry.get("id") == model for entry in models):
        return CheckResult("OPENROUTER_MODEL", CheckStatus.OK, model)
    return CheckResult("OPENROUTER_MODEL", CheckStatus.FAIL, f"missing: {model}")


def check_discord_webhook(webhook_url: str, *, client: Any = httpx) -> CheckResult:
    try:
        response = client.get(webhook_url, timeout=20)
    except Exception as error:
        return CheckResult("DISCORD_WEBHOOK", CheckStatus.FAIL, str(error))
    if response.status_code == 200:
        return CheckResult("DISCORD_WEBHOOK", CheckStatus.OK)
    if response.status_code in {401, 403, 404}:
        return CheckResult(
            "DISCORD_WEBHOOK",
            CheckStatus.FAIL,
            f"status={response.status_code}",
        )
    return CheckResult(
        "DISCORD_WEBHOOK",
        CheckStatus.WARNING,
        f"status={response.status_code}",
    )


def check_rss_freshness(
    settings: Settings,
    db: Any,
    fetchers: Mapping[str, Any],
) -> CheckResult:
    lines = run_freshness_audit(
        db=db,
        fetchers=fetchers,
        timeout_seconds=settings.source_timeout_seconds,
        retry_limit=settings.source_retry_limit,
        freshness_window_hours=settings.freshness_window_hours,
    )
    errors = [line for line in lines if ": error |" in line]
    if errors:
        return CheckResult("RSS_FRESHNESS", CheckStatus.FAIL, "; ".join(errors))
    warnings = [line for line in lines if "fresh-feed-ahead-of-storage" in line]
    if warnings:
        return CheckResult("RSS_FRESHNESS", CheckStatus.WARNING, "; ".join(warnings))
    return CheckResult("RSS_FRESHNESS", CheckStatus.OK, f"sources={len(lines)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    settings = load_settings()
    db = SupabaseRestClient(
        settings.supabase_url,
        settings.supabase_service_role_key,
        settings.request_timeout_seconds,
    )
    fetchers = {"rss": RSSFetcher()}
    with httpx.Client(timeout=settings.request_timeout_seconds) as http_client:
        report = run_production_check(
            settings=settings,
            db=db,
            fetchers=fetchers,
            http_client=http_client,
        )

    if args.json:
        print(json.dumps(asdict(report), default=_json_default, indent=2))
    else:
        for result in report.results:
            line = f"{result.name}: {result.status.value}"
            if result.detail:
                line += f" ({result.detail})"
            print(line)

    if not report.ok:
        raise SystemExit(1)


def _threshold_result(
    name: str,
    value_bytes: int,
    warning_mb: int,
    fail_mb: int,
) -> CheckResult:
    value_mb = value_bytes / (1024 * 1024)
    detail = f"{value_mb:.1f} MB"
    if value_mb >= fail_mb:
        return CheckResult(name, CheckStatus.FAIL, detail)
    if value_mb >= warning_mb:
        return CheckResult(name, CheckStatus.WARNING, detail)
    return CheckResult(name, CheckStatus.OK, detail)


def _zero_required_result(name: str, count: int) -> CheckResult:
    return CheckResult(
        name,
        CheckStatus.FAIL if count else CheckStatus.OK,
        str(count),
    )


def _close_fetchers(fetchers: Mapping[str, Any]) -> None:
    for fetcher in fetchers.values():
        if isinstance(fetcher, Mapping):
            nested_values = fetcher.values()
        else:
            nested_values = [fetcher]
        for nested_value in nested_values:
            close = getattr(nested_value, "close", None)
            if callable(close):
                close()


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return str(value)


if __name__ == "__main__":
    main()
