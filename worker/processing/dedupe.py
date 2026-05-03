from __future__ import annotations

from worker.models import NewsItem


def dedupe_in_memory(items: list[NewsItem]) -> list[NewsItem]:
    seen_url_hashes: set[str] = set()
    seen_content_hashes: set[str] = set()
    unique_items: list[NewsItem] = []

    for item in items:
        if item.normalized_url_hash in seen_url_hashes:
            continue
        if item.content_hash in seen_content_hashes:
            continue
        seen_url_hashes.add(item.normalized_url_hash)
        seen_content_hashes.add(item.content_hash)
        unique_items.append(item)

    return unique_items
