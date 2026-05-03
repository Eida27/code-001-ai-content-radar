from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from worker.models import DiscordAlert, Draft, NewsItem, Score, Source


class DuplicateItemError(RuntimeError):
    pass


class SupabaseRestClient:
    def __init__(self, url: str, service_role_key: str, timeout_seconds: int) -> None:
        self.url = url.rstrip("/")
        self._client = httpx.Client(
            base_url=f"{self.url}/rest/v1",
            timeout=timeout_seconds,
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )

    def start_worker_run(self) -> str:
        response = self._client.post("/worker_runs", json={"status": "running"})
        response.raise_for_status()
        return response.json()[0]["id"]

    def try_acquire_worker_lock(
        self,
        lock_name: str,
        holder: str,
        ttl_seconds: int,
    ) -> bool:
        response = self._client.post(
            "/rpc/try_acquire_worker_lock",
            json={
                "p_lock_name": lock_name,
                "p_holder": holder,
                "p_ttl_seconds": ttl_seconds,
            },
            headers={"Prefer": "return=representation"},
        )
        response.raise_for_status()
        return _rpc_bool(response.json())

    def release_worker_lock(self, lock_name: str, holder: str) -> bool:
        response = self._client.post(
            "/rpc/release_worker_lock",
            json={
                "p_lock_name": lock_name,
                "p_holder": holder,
            },
            headers={"Prefer": "return=representation"},
        )
        response.raise_for_status()
        return _rpc_bool(response.json())

    def get_production_readiness_snapshot(self) -> dict[str, Any]:
        response = self._client.post(
            "/rpc/production_readiness_snapshot",
            json={},
            headers={"Prefer": "return=representation"},
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        raise ValueError("Unexpected production readiness snapshot response")

    def finish_worker_run(
        self, run_id: str, status: str, metadata: dict[str, Any] | None = None
    ) -> None:
        response = self._client.patch(
            f"/worker_runs?id=eq.{run_id}",
            json={
                "status": status,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "metadata": metadata or {},
            },
        )
        response.raise_for_status()

    def load_active_sources(self) -> list[Source]:
        response = self._client.get(
            "/sources",
            params={
                "is_active": "eq.true",
                "order": "priority.desc,name.asc",
            },
        )
        response.raise_for_status()
        return [
            Source(
                id=row["id"],
                name=row["name"],
                type=row["type"],
                url=row["url"],
                category=row.get("category"),
                priority=row.get("priority") or 5,
                last_checked_at=_parse_datetime(row.get("last_checked_at")),
                last_success_at=_parse_datetime(row.get("last_success_at")),
                last_error_at=_parse_datetime(row.get("last_error_at")),
                last_error_message=row.get("last_error_message"),
                latest_feed_published_at=_parse_datetime(
                    row.get("latest_feed_published_at")
                ),
                latest_stored_published_at=_parse_datetime(
                    row.get("latest_stored_published_at")
                ),
            )
            for row in response.json()
        ]

    def load_items_ready_for_drafting(
        self, limit: int, min_importance_score: int
    ) -> list[NewsItem]:
        if limit <= 0:
            return []

        items: list[NewsItem] = []
        page_size = max(limit * 3, 10)
        offset = 0

        while len(items) < limit:
            response = self._client.get(
                "/news_items",
                params={
                    "select": "*",
                    "status": "eq.scored",
                    "importance_score": f"gte.{min_importance_score}",
                    "order": "created_at.asc",
                    "limit": str(page_size),
                    "offset": str(offset),
                },
            )
            response.raise_for_status()
            rows = response.json()
            if not rows:
                break

            for row in rows:
                if self._item_has_draft(row["id"]):
                    continue
                items.append(_row_to_news_item(row))
                if len(items) >= limit:
                    break

            if len(rows) < page_size:
                break
            offset += page_size

        return items

    def _item_has_draft(self, news_item_id: str) -> bool:
        response = self._client.get(
            "/drafts",
            params={
                "select": "id",
                "news_item_id": f"eq.{news_item_id}",
                "limit": "1",
            },
        )
        response.raise_for_status()
        return bool(response.json())

    def count_drafts_created_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        response = self._client.get(
            "/drafts",
            params={
                "select": "id",
                "created_at": f"gte.{today}T00:00:00+00:00",
            },
            headers={"Prefer": "count=exact"},
        )
        response.raise_for_status()
        return _content_range_count(response)

    def count_ai_requests_today(self, model: str) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        response = self._client.get(
            "/ai_requests",
            params={
                "select": "id",
                "model_used": f"eq.{model}",
                "created_at": f"gte.{today}T00:00:00+00:00",
            },
            headers={"Prefer": "count=exact"},
        )
        response.raise_for_status()
        return _content_range_count(response)

    def save_item(self, item: NewsItem, status: str) -> NewsItem:
        payload = {
            "source_id": item.source_id,
            "title": item.title,
            "url": item.url,
            "canonical_url": item.canonical_url,
            "normalized_url_hash": item.normalized_url_hash,
            "canonical_url_hash": item.canonical_url_hash,
            "content_hash": item.content_hash,
            "raw_summary": item.raw_summary,
            "source_name": item.source_name,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "status": status,
            "importance_score": item.importance_score,
            "importance_reason": item.importance_reason,
        }
        try:
            response = self._client.post(
                "/news_items?on_conflict=normalized_url_hash",
                json=payload,
                headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 409:
                raise DuplicateItemError("Duplicate news item insert conflict") from error
            raise
        rows = response.json()
        if rows:
            item.id = rows[0]["id"]
            item.status = rows[0]["status"]
        else:
            raise DuplicateItemError("Duplicate news item skipped by Supabase")
        return item

    def update_item_status(self, item_id: str | None, status: str) -> None:
        if not item_id:
            return
        response = self._client.patch(
            f"/news_items?id=eq.{item_id}",
            json={"status": status},
        )
        response.raise_for_status()

    def mark_source_check_started(self, source_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        response = self._client.patch(
            f"/sources?id=eq.{source_id}",
            json={"last_checked_at": now, "updated_at": now},
            headers={"Prefer": "return=minimal"},
        )
        response.raise_for_status()

    def mark_source_check_success(
        self,
        source_id: str,
        latest_feed_published_at: datetime | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "last_success_at": now,
            "last_error_message": None,
            "updated_at": now,
        }
        if latest_feed_published_at is not None:
            payload["latest_feed_published_at"] = latest_feed_published_at.isoformat()
        response = self._client.patch(
            f"/sources?id=eq.{source_id}",
            json=payload,
            headers={"Prefer": "return=minimal"},
        )
        response.raise_for_status()

    def mark_source_check_failure(self, source_id: str, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        response = self._client.patch(
            f"/sources?id=eq.{source_id}",
            json={
                "last_error_at": now,
                "last_error_message": error[:500],
                "updated_at": now,
            },
            headers={"Prefer": "return=minimal"},
        )
        response.raise_for_status()

    def update_source_latest_stored_published_at(
        self, source_id: str, latest_stored_published_at: datetime
    ) -> None:
        response = self._client.patch(
            f"/sources?id=eq.{source_id}",
            json={
                "latest_stored_published_at": latest_stored_published_at.isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Prefer": "return=minimal"},
        )
        response.raise_for_status()

    def get_latest_stored_published_at_by_source(self, source_id: str) -> datetime | None:
        response = self._client.get(
            "/news_items",
            params={
                "select": "published_at",
                "source_id": f"eq.{source_id}",
                "published_at": "not.is.null",
                "order": "published_at.desc",
                "limit": "1",
            },
        )
        response.raise_for_status()
        rows = response.json()
        if not rows:
            return None
        return _parse_datetime(rows[0].get("published_at"))

    def save_drafts(self, news_item_id: str | None, drafts: list[Draft]) -> None:
        if not news_item_id or not drafts:
            return
        response = self._client.post(
            "/drafts?on_conflict=news_item_id,draft_type",
            json=[
                {
                    "news_item_id": news_item_id,
                    "draft_type": draft.draft_type,
                    "content": draft.content,
                    "model_used": draft.model_used,
                    "status": "needs_review",
                }
                for draft in drafts
            ],
            headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
        )
        response.raise_for_status()

    def save_ai_request(self, **kwargs: Any) -> None:
        response = self._client.post("/ai_requests", json=kwargs)
        response.raise_for_status()

    def queue_discord_alert(
        self,
        item: NewsItem,
        drafts: list[Draft],
        score: Score,
    ) -> None:
        if not item.id:
            return
        payload = {
            "news_item_id": item.id,
            "status": "pending",
            "payload": _discord_payload(item, drafts, score),
        }
        response = self._client.post(
            "/discord_alerts?on_conflict=news_item_id",
            json=payload,
            headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
        )
        response.raise_for_status()

    def load_pending_discord_alerts(self, limit: int) -> list[DiscordAlert]:
        if limit <= 0:
            return []
        response = self._client.get(
            "/discord_alerts",
            params={
                "select": "id,news_item_id,payload",
                "status": "eq.pending",
                "next_attempt_at": f"lte.{datetime.now(timezone.utc).isoformat()}",
                "order": "created_at.asc",
                "limit": str(limit),
            },
        )
        response.raise_for_status()
        return [
            DiscordAlert(
                id=row["id"],
                news_item_id=row["news_item_id"],
                payload=row["payload"],
            )
            for row in response.json()
        ]

    def mark_discord_alerts_sent(self, alert_ids: list[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._mark_discord_alerts_attempted(
            alert_ids,
            {
                "status": "sent",
                "sent_at": now,
                "updated_at": now,
                "last_error": None,
                "last_status_code": None,
            },
        )

    def mark_discord_alerts_retry(
        self,
        alert_ids: list[str],
        retry_after_seconds: float,
        error: str,
        status_code: int | None,
    ) -> None:
        next_attempt_at = (
            datetime.now(timezone.utc) + timedelta(seconds=retry_after_seconds)
        ).isoformat()
        self._mark_discord_alerts_attempted(
            alert_ids,
            {
                "status": "pending",
                "next_attempt_at": next_attempt_at,
                "last_error": error,
                "last_status_code": status_code,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def mark_discord_alerts_failed(
        self,
        alert_ids: list[str],
        error: str,
        status_code: int | None,
    ) -> None:
        self._mark_discord_alerts_attempted(
            alert_ids,
            {
                "status": "failed",
                "last_error": error,
                "last_status_code": status_code,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def delete_old_logs(self, cutoff: datetime, limit: int) -> int:
        ids = self._select_ids(
            "/logs",
            {
                "created_at": f"lt.{cutoff.isoformat()}",
                "order": "created_at.asc",
                "limit": str(limit),
            },
        )
        return self._delete_rows_by_ids("/logs", ids)

    def strip_old_ai_raw_responses(self, cutoff: datetime, limit: int) -> int:
        ids = self._select_ids_with_non_null_fields(
            "/ai_requests",
            {"created_at": f"lt.{cutoff.isoformat()}", "order": "created_at.asc"},
            ["raw_response"],
            limit,
        )
        return self._patch_rows_by_ids("/ai_requests", ids, {"raw_response": None})

    def strip_old_low_priority_news_fields(self, cutoff: datetime, limit: int) -> int:
        ids = self._select_ids_with_non_null_fields(
            "/news_items",
            {
                "status": "eq.low_priority",
                "created_at": f"lt.{cutoff.isoformat()}",
                "order": "created_at.asc",
            },
            ["raw_summary", "importance_reason"],
            limit,
        )
        return self._patch_rows_by_ids(
            "/news_items",
            ids,
            {"raw_summary": None, "importance_reason": None},
        )

    def delete_old_success_worker_runs(self, cutoff: datetime, limit: int) -> int:
        ids = self._select_ids(
            "/worker_runs",
            {
                "status": "eq.success",
                "finished_at": f"lt.{cutoff.isoformat()}",
                "order": "finished_at.asc",
                "limit": str(limit),
            },
        )
        return self._delete_rows_by_ids("/worker_runs", ids)

    def get_storage_usage_snapshot(self) -> dict[str, Any]:
        return {
            "object_count": None,
            "total_bytes": None,
            "query_path": "supabase/queries/storage_audit.sql",
        }

    def log_event(
        self,
        level: str,
        module: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        response = self._client.post(
            "/logs",
            json={
                "level": level,
                "module": module,
                "message": message,
                "metadata": metadata or {},
            },
            headers={"Prefer": "return=minimal"},
        )
        response.raise_for_status()

    def close(self) -> None:
        self._client.close()

    def _mark_discord_alerts_attempted(
        self,
        alert_ids: list[str],
        patch_payload: dict[str, Any],
    ) -> None:
        for row in self._load_discord_alert_attempts(alert_ids):
            self._patch_rows_by_ids(
                "/discord_alerts",
                [row["id"]],
                {**patch_payload, "attempts": int(row.get("attempts") or 0) + 1},
            )

    def _load_discord_alert_attempts(self, alert_ids: list[str]) -> list[dict[str, Any]]:
        if not alert_ids:
            return []
        response = self._client.get(
            "/discord_alerts",
            params={
                "select": "id,attempts",
                "id": _in_filter(alert_ids),
            },
        )
        response.raise_for_status()
        return response.json()

    def _select_ids(self, path: str, params: dict[str, str]) -> list[str]:
        response = self._client.get(
            path,
            params={"select": "id", **params},
        )
        response.raise_for_status()
        return [row["id"] for row in response.json()]

    def _select_ids_with_non_null_fields(
        self,
        path: str,
        base_params: dict[str, str],
        field_names: list[str],
        limit: int,
    ) -> list[str]:
        if limit <= 0:
            return []

        ids: list[str] = []
        page_size = max(limit * 3, 10)
        offset = 0
        select_fields = ",".join(["id", *field_names])

        while len(ids) < limit:
            response = self._client.get(
                path,
                params={
                    "select": select_fields,
                    **base_params,
                    "limit": str(page_size),
                    "offset": str(offset),
                },
            )
            response.raise_for_status()
            rows = response.json()
            if not rows:
                break
            for row in rows:
                if any(row.get(field_name) is not None for field_name in field_names):
                    ids.append(row["id"])
                    if len(ids) >= limit:
                        break
            if len(rows) < page_size:
                break
            offset += page_size

        return ids

    def _patch_rows_by_ids(
        self,
        path: str,
        ids: list[str],
        payload: dict[str, Any],
    ) -> int:
        if not ids:
            return 0
        response = self._client.patch(
            path,
            params={"id": _in_filter(ids)},
            json=payload,
            headers={"Prefer": "return=minimal"},
        )
        response.raise_for_status()
        return len(ids)

    def _delete_rows_by_ids(self, path: str, ids: list[str]) -> int:
        if not ids:
            return 0
        response = self._client.delete(
            path,
            params={"id": _in_filter(ids)},
            headers={"Prefer": "return=minimal"},
        )
        response.raise_for_status()
        return len(ids)


def _content_range_count(response: httpx.Response) -> int:
    content_range = response.headers.get("content-range", "")
    if "/" not in content_range:
        return len(response.json())
    return int(content_range.rsplit("/", 1)[1])


def _rpc_bool(payload: Any) -> bool:
    if isinstance(payload, bool):
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], bool):
        return payload[0]
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, bool):
                return value
    return bool(payload)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def _in_filter(ids: list[str]) -> str:
    return f"in.({','.join(ids)})"


def _discord_payload(
    item: NewsItem,
    drafts: list[Draft],
    score: Score,
) -> dict[str, Any]:
    short_post = next(
        (draft.content for draft in drafts if draft.draft_type == "short_post"),
        drafts[0].content if drafts else "",
    )
    why_it_matters = next(
        (draft.content for draft in drafts if draft.draft_type == "why_it_matters"),
        "",
    )
    return {
        "title": item.title,
        "source_name": item.source_name or "Unknown",
        "score": item.importance_score,
        "reason": score.reason,
        "why_it_matters": why_it_matters,
        "short_post": short_post,
        "source_url": item.canonical_url,
    }


def _row_to_news_item(row: dict[str, Any]) -> NewsItem:
    published_at = _parse_datetime(row.get("published_at"))

    return NewsItem(
        id=row["id"],
        source_id=row.get("source_id"),
        title=row["title"],
        url=row["url"],
        canonical_url=row.get("canonical_url") or row["url"],
        normalized_url_hash=row["normalized_url_hash"],
        canonical_url_hash=row.get("canonical_url_hash"),
        content_hash=row["content_hash"],
        raw_summary=row.get("raw_summary"),
        source_name=row.get("source_name"),
        published_at=published_at,
        status=row.get("status", "scored"),
        importance_score=row.get("importance_score"),
        importance_reason=row.get("importance_reason"),
    )
