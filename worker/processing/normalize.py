from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from worker.models import NewsItem, RawItem, Source


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_NAMES = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = parts.path or "/"

    query_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in TRACKING_QUERY_NAMES:
            continue
        if key_lower.startswith(TRACKING_QUERY_PREFIXES):
            continue
        query_pairs.append((key, value))

    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_items(raw_items: list[RawItem], source: Source) -> list[NewsItem]:
    normalized: list[NewsItem] = []
    for raw_item in raw_items:
        canonical_url = canonicalize_url(raw_item.url)
        title = " ".join(raw_item.title.split())
        summary = raw_item.raw_summary.strip() if raw_item.raw_summary else None
        content_identity = "|".join(
            [
                source.name.lower(),
                title.lower(),
                canonical_url,
                raw_item.published_at.isoformat() if raw_item.published_at else "",
            ]
        )
        normalized.append(
            NewsItem(
                source_id=source.id,
                source_name=source.name,
                title=title,
                url=raw_item.url,
                canonical_url=canonical_url,
                raw_summary=summary,
                published_at=raw_item.published_at,
                normalized_url_hash=_sha256(canonical_url),
                canonical_url_hash=_sha256(canonical_url) if canonical_url else None,
                content_hash=_sha256(content_identity),
                metadata=raw_item.metadata,
            )
        )
    return normalized
