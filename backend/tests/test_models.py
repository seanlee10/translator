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


def test_context_pair_holds_both_languages():
    pair = ContextPair(en="hello", ko="안녕하세요")
    assert pair.en == "hello" and pair.ko == "안녕하세요"
