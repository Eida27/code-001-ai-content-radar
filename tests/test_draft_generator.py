import pytest

from worker.processing.draft_generator import DraftParseError, parse_ai_draft_response


def test_parse_ai_draft_response_validates_and_returns_drafts():
    raw = """
    {
      "image_overlay_caption": "OpenAI gives builders a faster way to test model updates. Verify the details before shipping.",
      "short_post": "OpenAI released a useful update for builders."
    }
    """

    package = parse_ai_draft_response(raw, model_used="google/gemma-4-31b-it:free")

    assert package.raw_response == raw
    assert package.parse_error is None
    assert {draft.draft_type for draft in package.drafts} == {
        "image_overlay_caption",
        "short_post",
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
      "image_overlay_caption": "Short overlay caption.",
      "short_post": "%s"
    }
    """ % ("x" * 281)

    with pytest.raises(DraftParseError) as exc_info:
        parse_ai_draft_response(raw, model_used="google/gemma-4-31b-it:free")

    assert exc_info.value.raw_response == raw
    assert "short_post" in exc_info.value.parse_error


def test_parse_ai_draft_response_enforces_overlay_caption_length():
    raw = """
    {
      "image_overlay_caption": "%s",
      "short_post": "OpenAI released a useful update for builders."
    }
    """ % ("x" * 181)

    with pytest.raises(DraftParseError) as exc_info:
        parse_ai_draft_response(raw, model_used="google/gemma-4-31b-it:free")

    assert "image_overlay_caption" in exc_info.value.parse_error


def test_parse_ai_draft_response_enforces_overlay_caption_sentence_count():
    raw = """
    {
      "image_overlay_caption": "OpenAI shipped an update. Builders get a faster workflow. Verify the source.",
      "short_post": "OpenAI released a useful update for builders."
    }
    """

    with pytest.raises(DraftParseError) as exc_info:
        parse_ai_draft_response(raw, model_used="google/gemma-4-31b-it:free")

    assert "image_overlay_caption" in exc_info.value.parse_error


def test_parse_ai_draft_response_rejects_old_extra_draft_keys():
    raw = """
    {
      "image_overlay_caption": "OpenAI gives builders a faster way to test updates.",
      "short_post": "OpenAI released a useful update for builders.",
      "long_post": "Old draft shape should not be accepted."
    }
    """

    with pytest.raises(DraftParseError) as exc_info:
        parse_ai_draft_response(raw, model_used="google/gemma-4-31b-it:free")

    assert "long_post" in exc_info.value.parse_error
