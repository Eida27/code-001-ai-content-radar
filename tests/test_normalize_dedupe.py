from datetime import datetime, timezone

from worker.models import RawItem, Source
from worker.processing.dedupe import dedupe_in_memory
from worker.processing.normalize import canonicalize_url, normalize_items


def test_canonicalize_url_strips_tracking_and_fragment():
    url = "HTTPS://Example.com/Path/?utm_source=x&b=2&a=1#section"

    assert canonicalize_url(url) == "https://example.com/Path/?a=1&b=2"


def test_normalize_items_computes_stable_hashes():
    source = Source(
        id="source-1",
        name="OpenAI News",
        type="rss",
        url="https://openai.com/news/rss.xml",
        priority=10,
    )
    raw_items = [
        RawItem(
            title="OpenAI launches a model",
            url="https://openai.com/news/item?utm_campaign=tracking",
            raw_summary="A useful update.",
            published_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
        )
    ]

    [item] = normalize_items(raw_items, source)

    assert item.source_id == "source-1"
    assert item.canonical_url == "https://openai.com/news/item"
    assert len(item.normalized_url_hash) == 64
    assert len(item.canonical_url_hash) == 64
    assert len(item.content_hash) == 64


def test_dedupe_in_memory_skips_duplicate_url_hashes():
    source = Source(
        id="source-1",
        name="OpenAI News",
        type="rss",
        url="https://openai.com/news/rss.xml",
        priority=10,
    )
    items = normalize_items(
        [
            RawItem(title="Same story", url="https://example.com/a?utm_source=x"),
            RawItem(title="Same story", url="https://example.com/a"),
        ],
        source,
    )

    unique_items = dedupe_in_memory(items)

    assert len(unique_items) == 1
    assert unique_items[0].canonical_url == "https://example.com/a"
