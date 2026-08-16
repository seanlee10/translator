import asyncio
from dataclasses import dataclass

import pytest

from app.config import Settings
from app.deepgram_client import (
    DeepgramSource,
    DeepgramStreamError,
    PhraseTracker,
    connect_params,
    message_to_dict,
)


def make_settings(**overrides) -> Settings:
    base = dict(deepgram_api_key="secret", anthropic_api_key="a", groq_api_key="g")
    base.update(overrides)
    return Settings(**base)


def test_connect_params_include_everything_the_policy_depends_on():
    params = connect_params(make_settings(), sample_rate=48000)

    assert params["model"] == "nova-3"
    assert params["language"] == "en"
    assert params["encoding"] == "linear16"
    assert params["channels"] == "1"
    assert params["interim_results"] == "true"
    assert params["punctuate"] == "true"
    assert params["smart_format"] == "true"


def test_every_connect_param_is_a_string():
    # The SDK types all of these as Optional[str]; passing True or 16000
    # fails confusingly at request time.
    params = connect_params(make_settings(), sample_rate=48000)

    assert all(isinstance(v, str) for v in params.values()), params


def test_sample_rate_comes_from_the_browser_not_from_settings():
    params = connect_params(make_settings(sample_rate=16000), sample_rate=44100)
    assert params["sample_rate"] == "44100"


def results(transcript: str, is_final: bool) -> dict:
    return {
        "type": "Results",
        "is_final": is_final,
        "speech_final": is_final,
        "channel": {"alternatives": [{"transcript": transcript, "confidence": 0.9}]},
    }


def test_interims_share_a_phrase_id_until_a_final_closes_it():
    tracker = PhraseTracker()

    first = tracker.normalize(results("hel", False))
    second = tracker.normalize(results("hello", False))
    final = tracker.normalize(results("hello there", True))
    following = tracker.normalize(results("how", False))

    assert (first.phrase_id, first.text, first.is_final) == (0, "hel", False)
    assert second.phrase_id == 0
    assert (final.phrase_id, final.is_final) == (0, True)
    assert following.phrase_id == 1


def test_non_results_messages_are_ignored():
    tracker = PhraseTracker()
    assert tracker.normalize({"type": "Metadata"}) is None
    assert tracker.normalize({"type": "UtteranceEnd", "channel": [0]}) is None
    assert tracker.normalize({"type": "SpeechStarted", "channel": [0]}) is None


def test_empty_transcripts_are_dropped_without_advancing_the_phrase():
    tracker = PhraseTracker()

    tracker.normalize(results("hel", False))
    assert tracker.normalize(results("", True)) is None
    assert tracker.normalize(results("   ", False)) is None

    # Still the same phrase — an empty final must not strand the open one.
    assert tracker.normalize(results("hello", False)).phrase_id == 0


def test_missing_alternatives_do_not_raise():
    tracker = PhraseTracker()
    assert tracker.normalize({"type": "Results", "is_final": False, "channel": {}}) is None


def test_message_to_dict_accepts_pydantic_dataclass_and_plain_dict():
    class Pydanticish:
        def model_dump(self):
            return {"type": "Results", "is_final": True}

    class Legacy:
        def dict(self):
            return {"type": "Results", "is_final": False}

    @dataclass
    class Plain:
        type: str = "Results"

    assert message_to_dict(Pydanticish())["is_final"] is True
    assert message_to_dict(Legacy())["is_final"] is False
    assert message_to_dict(Plain())["type"] == "Results"
    assert message_to_dict({"type": "Results"})["type"] == "Results"


# ---- DeepgramSource: the callback -> queue -> async-iterator bridge ----
#
# _on_message, _on_error, _on_close, and events() never touch self._connection
# or self._client, so the adapter can be driven directly, with no network and
# no real SDK client, by constructing DeepgramSource and calling its handlers
# by hand.


async def test_a_message_pushed_through_on_message_surfaces_from_events():
    source = DeepgramSource(make_settings(), 16000)

    await source._on_message(results("hello", False))
    await source._on_close()

    collected = [event async for event in source.events()]

    assert len(collected) == 1
    assert (collected[0].phrase_id, collected[0].text, collected[0].is_final) == (
        0,
        "hello",
        False,
    )


async def test_messages_dropped_by_the_phrase_tracker_produce_no_event():
    source = DeepgramSource(make_settings(), 16000)

    await source._on_message({"type": "Metadata"})  # non-Results: dropped
    await source._on_message(results("", True))  # empty transcript: dropped
    await source._on_message(results("hello", False))
    await source._on_close()

    collected = [event async for event in source.events()]

    assert len(collected) == 1
    assert collected[0].text == "hello"


async def test_events_terminates_cleanly_after_on_close_rather_than_hanging():
    source = DeepgramSource(make_settings(), 16000)
    await source._on_close()

    async def drain():
        return [event async for event in source.events()]

    # wait_for guards against a regression that makes events() hang forever
    # instead of terminating once the sentinel is seen.
    collected = await asyncio.wait_for(drain(), timeout=1)
    assert collected == []


async def test_aclose_is_safe_to_call_twice_and_before_connect():
    source = DeepgramSource(make_settings(), 16000)

    # Never connected: _listener and _ctx are both still None.
    await source.aclose()
    # Calling again must not raise either.
    await source.aclose()


async def test_on_error_causes_events_to_raise_deepgram_stream_error():
    source = DeepgramSource(make_settings(), 16000)

    await source._on_error(RuntimeError("boom"))

    with pytest.raises(DeepgramStreamError):
        async for _ in source.events():
            pass


async def test_a_graceful_close_terminates_without_raising():
    source = DeepgramSource(make_settings(), 16000)

    await source._on_close()

    # Must not raise DeepgramStreamError — only _on_error does that.
    collected = [event async for event in source.events()]
    assert collected == []


async def test_events_already_queued_are_yielded_before_the_error_is_raised():
    source = DeepgramSource(make_settings(), 16000)

    await source._on_message(results("hello", False))
    await source._on_message(results("hello there", True))
    await source._on_error(RuntimeError("boom"))

    collected = []
    with pytest.raises(DeepgramStreamError):
        async for event in source.events():
            collected.append(event)

    assert [event.text for event in collected] == ["hello", "hello there"]
