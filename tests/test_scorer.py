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
