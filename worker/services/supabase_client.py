from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from worker.models import Draft, NewsItem, Source


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
            )
            for row in response.json()
        ]

    def load_items_ready_for_drafting(
        self, limit: int, min_importance_score: int
    ) -> list[NewsItem]:
        response = self._client.get(
            "/news_items",
            params={
                "select": "*",
                "status": "eq.scored",
                "importance_score": f"gte.{min_importance_score}",
                "order": "created_at.asc",
                "limit": str(limit),
            },
        )
        response.raise_for_status()

        items: list[NewsItem] = []
        for row in response.json():
            if self._item_has_draft(row["id"]):
                continue
            items.append(_row_to_news_item(row))
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

    def save_drafts(self, news_item_id: str | None, drafts: list[Draft]) -> None:
        if not news_item_id or not drafts:
            return
        response = self._client.post(
            "/drafts",
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
        )
        response.raise_for_status()

    def save_ai_request(self, **kwargs: Any) -> None:
        response = self._client.post("/ai_requests", json=kwargs)
        response.raise_for_status()

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


def _content_range_count(response: httpx.Response) -> int:
    content_range = response.headers.get("content-range", "")
    if "/" not in content_range:
        return len(response.json())
    return int(content_range.rsplit("/", 1)[1])


def _row_to_news_item(row: dict[str, Any]) -> NewsItem:
    published_at = row.get("published_at")
    if isinstance(published_at, str):
        published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))

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
