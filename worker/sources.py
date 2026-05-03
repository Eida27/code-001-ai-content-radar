from __future__ import annotations

from worker.models import Source


def load_active_sources(db) -> list[Source]:
    return db.load_active_sources()
