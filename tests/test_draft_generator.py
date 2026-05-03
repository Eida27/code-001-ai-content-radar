import pytest

from worker.processing.draft_generator import DraftParseError, parse_ai_draft_response


def test_parse_ai_draft_response_validates_and_returns_drafts():
    raw = """
    {
      "short_post": "OpenAI released a useful update for builders.",
      "long_post": "OpenAI released a useful update for builders. It matters because teams can ship faster with clearer tools.",
      "thread": ["OpenAI shipped an update.", "It matters for builders.", "Read the source before posting."],
      "why_it_matters": "Builders get a clearer path to test the update.",
      "risk_note": "Details should be verified against the source."
    }
    """

    package = parse_ai_draft_response(raw, model_used="google/gemma-4-31b-it:free")

    assert package.raw_response == raw
    assert package.parse_error is None
    assert {draft.draft_type for draft in package.drafts} == {
        "short_post",
        "long_post",
        "thread",
        "why_it_matters",
        "risk_note",
    }
    assert all(draft.model_used == "google/gemma-4-31b-it:free" for draft in package.drafts)


def test_parse_ai_draft_response_rejects_bad_json_with_raw_response():
    raw = '{"short_post": "missing required fields"'

    with pytest.raises(DraftParseError) as exc_info:
        parse_ai_draft_response(raw, model_used="google/gemma-4-31b-it:free")

    assert exc_info.value.raw_response == raw
    assert "Invalid AI draft JSON" in str(exc_info.value)


def test_parse_ai_draft_response_enforces_post_lengths():
    raw = """
    {
      "short_post": "%s",
      "long_post": "ok",
      "thread": ["ok"],
      "why_it_matters": "ok",
      "risk_note": "ok"
    }
    """ % ("x" * 281)

    with pytest.raises(DraftParseError) as exc_info:
        parse_ai_draft_response(raw, model_used="google/gemma-4-31b-it:free")

    assert exc_info.value.raw_response == raw
    assert "short_post" in exc_info.value.parse_error
