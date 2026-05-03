from __future__ import annotations

from worker.models import NewsItem, Score, Source


IMPORTANT_KEYWORDS = [
    "launch",
    "launched",
    "release",
    "released",
    "new model",
    "api",
    "agent",
    "agents",
    "robotics",
    "open source",
    "open-source",
    "benchmark",
    "multimodal",
    "video generation",
    "coding",
    "developer",
    "research",
    "paper",
    "update",
]

TOP_COMPANIES = [
    "openai",
    "google",
    "deepmind",
    "anthropic",
    "meta",
    "microsoft",
    "nvidia",
    "hugging face",
    "mistral",
    "xai",
]

IMPACT_KEYWORDS = [
    "creator",
    "developer",
    "student",
    "freelancer",
    "automation",
    "api",
    "workflow",
]


def score_item(item: NewsItem, source: Source) -> Score:
    score = 0
    reasons: list[str] = []
    haystack = f"{item.title} {item.raw_summary or ''}".lower()

    if source.priority >= 8:
        score += 5
        reasons.append("High-trust source")

    if any(keyword in haystack for keyword in IMPORTANT_KEYWORDS):
        score += 4
        reasons.append("Important AI keyword detected")

    if any(company in haystack for company in TOP_COMPANIES):
        score += 3
        reasons.append("Major AI company mentioned")

    if any(keyword in haystack for keyword in IMPACT_KEYWORDS):
        score += 2
        reasons.append("Direct audience impact")

    if "rumor" in haystack:
        score -= 4
        reasons.append("Possible rumor")

    bounded_score = min(max(score, 0), 10)
    return Score(value=bounded_score, reason="; ".join(reasons) or "No strong signals")
