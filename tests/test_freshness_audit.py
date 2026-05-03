from datetime import datetime, timezone

from worker.freshness_audit import run_freshness_audit
from worker.models import RawItem
from tests.test_worker_reliability import FakeDB, FakeFetcher, official_source


def test_freshness_audit_reports_live_and_stored_source_freshness_without_ai_or_discord():
    source = official_source()
    db = FakeDB([source])
    db.latest_stored_by_source = {
        source.id: datetime(2026, 5, 2, tzinfo=timezone.utc)
    }
    fetcher = FakeFetcher(
        items=[
            RawItem(
                title="Fresh launch",
                url="https://openai.com/news/fresh",
                published_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
            )
        ]
    )

    lines = run_freshness_audit(
        db=db,
        fetchers={"rss": fetcher},
        timeout_seconds=1,
        retry_limit=0,
        freshness_window_hours=72,
    )

    assert any("OpenAI News" in line for line in lines)
    assert any("latest_feed=2026-05-03T00:00:00+00:00" in line for line in lines)
    assert any("latest_stored=2026-05-02T00:00:00+00:00" in line for line in lines)
    assert not db.drafts
    assert not db.discord_alerts
