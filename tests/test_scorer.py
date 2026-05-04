from worker.models import NewsItem, Source
from worker.processing.scorer import score_item


def test_score_item_gives_major_official_release_high_score():
    source = Source(
        id="source-1",
        name="OpenAI News",
        type="rss",
        url="https://openai.com/news/rss.xml",
        priority=10,
    )
    item = NewsItem(
        title="OpenAI launches new model API for developers",
        url="https://openai.com/news/model-api",
        canonical_url="https://openai.com/news/model-api",
        normalized_url_hash="n",
        canonical_url_hash="c",
        content_hash="h",
    )

    score = score_item(item, source)

    assert score.value >= 7
    assert "High-trust source" in score.reason


def test_score_item_penalizes_rumors():
    source = Source(
        id="source-1",
        name="Random Blog",
        type="html",
        url="https://example.com",
        priority=2,
    )
    item = NewsItem(
        title="Rumor: vague AI drama might happen",
        url="https://example.com/rumor",
        canonical_url="https://example.com/rumor",
        normalized_url_hash="n",
        canonical_url_hash="c",
        content_hash="h",
    )

    score = score_item(item, source)

    assert score.value < 7
    assert "Possible rumor" in score.reason


def test_verified_creator_leak_can_reach_review_with_unconfirmed_label():
    source = Source(
        id="source-1",
        name="Trusted Creator Feed",
        type="rss",
        url="https://creator.example/feed.xml",
        category="verified_creator",
        priority=7,
    )
    item = NewsItem(
        title="Leak: OpenAI new model API for developers",
        url="https://creator.example/openai-model-api",
        canonical_url="https://creator.example/openai-model-api",
        normalized_url_hash="n",
        canonical_url_hash="c",
        content_hash="h",
        raw_summary="A verified creator says an OpenAI developer API update is coming.",
    )

    score = score_item(item, source)

    assert score.value >= 7
    assert "Allowlisted unofficial source" in score.reason
    assert "Unconfirmed: verify before posting" in score.reason


def test_reliable_forum_leak_needs_strong_signals_to_reach_review():
    source = Source(
        id="source-1",
        name="Reliable AI Forum",
        type="rss",
        url="https://forum.example/rss",
        category="reliable_forum",
        priority=6,
    )
    weak_item = NewsItem(
        title="Forum leak: something may happen soon",
        url="https://forum.example/weak",
        canonical_url="https://forum.example/weak",
        normalized_url_hash="weak",
        canonical_url_hash="weak-c",
        content_hash="weak-h",
    )
    strong_item = NewsItem(
        title="Forum leak: Anthropic agent API for developers",
        url="https://forum.example/strong",
        canonical_url="https://forum.example/strong",
        normalized_url_hash="strong",
        canonical_url_hash="strong-c",
        content_hash="strong-h",
        raw_summary="Developers may get a new automation workflow.",
    )

    weak_score = score_item(weak_item, source)
    strong_score = score_item(strong_item, source)

    assert weak_score.value < 7
    assert strong_score.value >= 7
    assert "Unconfirmed: verify before posting" in strong_score.reason


def test_random_strong_rumor_stays_low_priority():
    source = Source(
        id="source-1",
        name="Random Blog",
        type="rss",
        url="https://example.com/feed",
        category="personal_blog",
        priority=3,
    )
    item = NewsItem(
        title="Rumor: OpenAI new model API for developers",
        url="https://example.com/openai-rumor",
        canonical_url="https://example.com/openai-rumor",
        normalized_url_hash="n",
        canonical_url_hash="c",
        content_hash="h",
    )

    score = score_item(item, source)

    assert score.value < 7
    assert "Unconfirmed: verify before posting" not in score.reason
