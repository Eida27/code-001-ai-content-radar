from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from worker.models import Draft, DraftPackage


class DraftParseError(ValueError):
    def __init__(self, message: str, raw_response: str, parse_error: str):
        super().__init__(message)
        self.raw_response = raw_response
        self.parse_error = parse_error


class AIDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    image_overlay_caption: str = Field(min_length=1, max_length=180)
    short_post: str = Field(min_length=1, max_length=280)

    @field_validator("image_overlay_caption")
    @classmethod
    def validate_overlay_sentence_count(cls, value: str) -> str:
        sentence_count = _count_sentences(value)
        if sentence_count > 2:
            raise ValueError("image_overlay_caption must be 1-2 sentences")
        return value


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
        Draft("image_overlay_caption", response.image_overlay_caption, model_used),
        Draft("short_post", response.short_post, model_used),
    ]
    return DraftPackage(drafts=drafts, raw_response=raw_response)


def _count_sentences(value: str) -> int:
    sentences = re.findall(r"[^.!?]+(?:[.!?]+|$)", value.strip())
    return len([sentence for sentence in sentences if sentence.strip()])
