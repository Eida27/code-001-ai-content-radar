from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from worker.models import Draft, DraftPackage


class DraftParseError(ValueError):
    def __init__(self, message: str, raw_response: str, parse_error: str):
        super().__init__(message)
        self.raw_response = raw_response
        self.parse_error = parse_error


class AIDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    short_post: str = Field(min_length=1, max_length=280)
    long_post: str = Field(min_length=1, max_length=800)
    thread: list[str] = Field(min_length=1, max_length=5)
    why_it_matters: str = Field(min_length=1, max_length=280)
    risk_note: str = Field(min_length=1, max_length=280)


def parse_ai_draft_response(raw_response: str, model_used: str) -> DraftPackage:
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as error:
        raise DraftParseError(
            "Invalid AI draft JSON",
            raw_response=raw_response,
            parse_error=str(error),
        ) from error

    try:
        response = AIDraftResponse.model_validate(payload)
    except ValidationError as error:
        raise DraftParseError(
            "AI draft response failed validation",
            raw_response=raw_response,
            parse_error=str(error),
        ) from error

    drafts = [
        Draft("short_post", response.short_post, model_used),
        Draft("long_post", response.long_post, model_used),
        Draft("thread", "\n\n".join(response.thread), model_used),
        Draft("why_it_matters", response.why_it_matters, model_used),
        Draft("risk_note", response.risk_note, model_used),
    ]
    return DraftPackage(drafts=drafts, raw_response=raw_response)
