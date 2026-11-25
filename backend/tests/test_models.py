import pytest
from pydantic import ValidationError

from app.models import (
    CaptionOut,
    ContextPair,
    ErrorOut,
    TranscriptEvent,
    TranscriptOut,
    TranslationResult,
)


def test_transcript_event_is_hashable_and_frozen():
    event = TranscriptEvent(phrase_id=1, text="hello", is_final=False)
    assert {event}
    assert event.phrase_id == 1


def test_translation_result_carries_provenance():
    result = TranslationResult(text="안녕하세요", provider="groq", latency_ms=310)
    assert (result.text, result.provider, result.latency_ms) == ("안녕하세요", "groq", 310)


def test_caption_out_serializes_with_provider_and_latency():
    payload = CaptionOut(
        phrase_id=3,
        en="hello",
        ko="안녕하세요",
        status="final",
        provider="anthropic",
        latency_ms=420,
    ).model_dump()

    assert payload == {
        "type": "caption",
        "phrase_id": 3,
        "en": "hello",
        "ko": "안녕하세요",
        "zh": None,
        "status": "final",
        "provider": "anthropic",
        "latency_ms": 420,
    }


def test_transcript_out_is_always_interim():
    assert TranscriptOut(phrase_id=1, en="hel").model_dump()["status"] == "interim"


def test_error_out_defaults_to_non_fatal():
    payload = ErrorOut(phrase_id=2, message="timeout").model_dump()
    assert payload["type"] == "error"
    assert payload["fatal"] is False


def test_context_pair_holds_the_spoken_and_translated_sides():
    pair = ContextPair(source="hello", target="안녕하세요")
    assert pair.source == "hello" and pair.target == "안녕하세요"


def test_transcript_out_can_carry_english_alone():
    event = TranscriptOut(phrase_id=0, en="hello there")
    assert event.en == "hello there"
    assert event.ko is None
    assert event.status == "interim"


def test_transcript_out_can_carry_korean_alone():
    # In KO→EN the raw Deepgram text IS the Korean, so it must land in the
    # `ko` slot: en/ko are LANGUAGE slots, not source/target roles.
    event = TranscriptOut(phrase_id=0, ko="안녕하세요")
    assert event.ko == "안녕하세요"
    assert event.en is None


def test_transcript_out_serializes_both_slots_so_the_client_can_tell_them_apart():
    # The client slots by field name, so an omitted key would be
    # indistinguishable from a null one only if we dropped it. Keep both.
    dumped = TranscriptOut(phrase_id=3, ko="안녕").model_dump()
    assert dumped["en"] is None
    assert dumped["ko"] == "안녕"
    assert dumped["type"] == "transcript"


def test_caption_out_still_requires_both_sides():
    # By the time a caption is emitted, both sides exist. The slots are
    # individually optional (which two depends on the direction), so the
    # guarantee is enforced as "exactly two" instead.
    with pytest.raises(ValidationError):
        CaptionOut(
            phrase_id=0,
            en="hello",
            status="final",
            provider="anthropic",
            latency_ms=10,
        )


def test_caption_out_rejects_all_three_slots_filled():
    with pytest.raises(ValidationError):
        CaptionOut(
            phrase_id=0, en="hello", ko="안녕", zh="你好",
            status="final", provider="anthropic", latency_ms=10,
        )


def test_caption_out_accepts_an_english_chinese_pair():
    payload = CaptionOut(
        phrase_id=0, en="hello", zh="你好",
        status="final", provider="anthropic", latency_ms=10,
    ).model_dump()
    assert (payload["en"], payload["ko"], payload["zh"]) == ("hello", None, "你好")


def test_transcript_out_serializes_the_zh_slot_too():
    dumped = TranscriptOut(phrase_id=0, en="hello").model_dump()
    assert "zh" in dumped and dumped["zh"] is None
