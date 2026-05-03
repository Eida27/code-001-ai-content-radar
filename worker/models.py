from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Source:
    id: str
    name: str
    type: str
    url: str
    category: str | None = None
    priority: int = 5


@dataclass(slots=True)
class RawItem:
    title: str
    url: str
    raw_summary: str | None = None
    published_at: datetime | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class NewsItem:
    title: str
    url: str
    canonical_url: str
    normalized_url_hash: str
    canonical_url_hash: str | None
    content_hash: str
    id: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    raw_summary: str | None = None
    published_at: datetime | None = None
    status: str = "new"
    importance_score: int | None = None
    importance_reason: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Score:
    value: int
    reason: str


@dataclass(frozen=True, slots=True)
class Draft:
    draft_type: str
    content: str
    model_used: str


@dataclass(frozen=True, slots=True)
class DraftPackage:
    drafts: list[Draft]
    raw_response: str
    parse_error: str | None = None


@dataclass(frozen=True, slots=True)
class DiscordAlert:
    id: str
    news_item_id: str
    payload: dict[str, Any]
