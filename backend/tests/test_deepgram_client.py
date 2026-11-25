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
from app.direction import EN_TO_KO, KO_TO_EN


def make_settings(**overrides) -> Settings:
    base = dict(
        deepgram_api_key="secret",
        anthropic_api_key="a",
        groq_api_key="g",
        xai_api_key="x",
    )
    base.update(overrides)
    # _env_file=None keeps the suite hermetic: Settings now resolves a real
    # repo-root .env, and a developer's local one must not change defaults.
    return Settings(_env_file=None, **base)


def test_connect_params_include_everything_the_policy_depends_on():
    params = connect_params(make_settings(), 48000, EN_TO_KO)

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
    params = connect_params(make_settings(), 48000, EN_TO_KO)

    assert all(isinstance(v, str) for v in params.values()), params


def test_sample_rate_comes_from_the_browser_not_from_settings():
    params = connect_params(make_settings(sample_rate=16000), 44100, EN_TO_KO)
    assert params["sample_rate"] == "44100"


@pytest.mark.parametrize(
    "direction,expected_language",
    [(EN_TO_KO, "en"), (KO_TO_EN, "ko")],
)
def test_connect_params_transcribes_the_source_language(direction, expected_language):
    params = connect_params(make_settings(), 48000, direction)
    assert params["language"] == expected_language


@pytest.mark.parametrize("direction", [EN_TO_KO, KO_TO_EN])
def test_every_connect_param_is_a_string_in_both_directions(direction):
    # The SDK types every one of these Optional[str]. Passing True or 16000
    # fails confusingly at request time rather than here.
    params = connect_params(make_settings(), 48000, direction)
    for key, value in params.items():
        assert isinstance(value, str), f"{key} is {type(value).__name__}, not str"


@pytest.mark.parametrize("direction", [EN_TO_KO, KO_TO_EN])
def test_direction_changes_only_the_language_param(direction):
    # Guards against a well-meaning "Korean needs a different model" edit:
    # nova-3 supports Korean streaming, so nothing else should vary.
    base = connect_params(make_settings(), 48000, EN_TO_KO)
    other = connect_params(make_settings(), 48000, direction)
    differing = {k for k in base if base[k] != other[k]}
    assert differing <= {"language"}


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
    source = DeepgramSource(make_settings(), 16000, EN_TO_KO)

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
    source = DeepgramSource(make_settings(), 16000, EN_TO_KO)

    await source._on_message({"type": "Metadata"})  # non-Results: dropped
    await source._on_message(results("", True))  # empty transcript: dropped
    await source._on_message(results("hello", False))
    await source._on_close()

    collected = [event async for event in source.events()]

    assert len(collected) == 1
    assert collected[0].text == "hello"


async def test_events_terminates_cleanly_after_on_close_rather_than_hanging():
    source = DeepgramSource(make_settings(), 16000, EN_TO_KO)
    await source._on_close()

    async def drain():
        return [event async for event in source.events()]

    # wait_for guards against a regression that makes events() hang forever
    # instead of terminating once the sentinel is seen.
    collected = await asyncio.wait_for(drain(), timeout=1)
    assert collected == []


async def test_aclose_is_safe_to_call_twice_and_before_connect():
    source = DeepgramSource(make_settings(), 16000, EN_TO_KO)

    # Never connected: _listener and _ctx are both still None.
    await source.aclose()
    # Calling again must not raise either.
    await source.aclose()


async def test_on_error_causes_events_to_raise_deepgram_stream_error():
    source = DeepgramSource(make_settings(), 16000, EN_TO_KO)

    await source._on_error(RuntimeError("boom"))

    with pytest.raises(DeepgramStreamError):
        async for _ in source.events():
            pass


async def test_a_graceful_close_terminates_without_raising():
    source = DeepgramSource(make_settings(), 16000, EN_TO_KO)

    await source._on_close()

    # Must not raise DeepgramStreamError — only _on_error does that.
    collected = [event async for event in source.events()]
    assert collected == []


async def test_events_already_queued_are_yielded_before_the_error_is_raised():
    source = DeepgramSource(make_settings(), 16000, EN_TO_KO)

    await source._on_message(results("hello", False))
    await source._on_message(results("hello there", True))
    await source._on_error(RuntimeError("boom"))

    collected = []
    with pytest.raises(DeepgramStreamError):
        async for event in source.events():
            collected.append(event)

    assert [event.text for event in collected] == ["hello", "hello there"]


# ---- keepalive ----
#
# deepgram-sdk 7.x sends no keepalives of its own; send_keep_alive() is the
# only mechanism it exposes. Without a ticker calling it, a silence longer
# than Deepgram's idle timeout ends the stream mid-session.


class FakeConnection:
    def __init__(self):
        self.keep_alives = 0

    async def send_keep_alive(self):
        self.keep_alives += 1


async def test_the_sdk_exposes_only_a_manual_keepalive():
    # Pins the fact the design doc used to get wrong. If a future SDK ever
    # does keepalive itself, this is the assertion to revisit.
    from deepgram.listen.v1.socket_client import AsyncV1SocketClient

    assert hasattr(AsyncV1SocketClient, "send_keep_alive")


async def test_keepalive_is_sent_repeatedly_while_connected(monkeypatch):
    monkeypatch.setattr("app.deepgram_client.KEEPALIVE_INTERVAL_S", 0.01)

    source = DeepgramSource(make_settings(), 16000, EN_TO_KO)
    connection = FakeConnection()
    source._connection = connection

    task = asyncio.create_task(source._run_keepalive())
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # Repeatedly, not once: a single send would not hold a long silence open.
    assert connection.keep_alives >= 2


async def test_the_keepalive_interval_is_five_seconds():
    from app.deepgram_client import KEEPALIVE_INTERVAL_S

    assert KEEPALIVE_INTERVAL_S == 5.0


async def test_aclose_cancels_the_keepalive_task(monkeypatch):
    monkeypatch.setattr("app.deepgram_client.KEEPALIVE_INTERVAL_S", 0.01)

    source = DeepgramSource(make_settings(), 16000, EN_TO_KO)
    source._connection = FakeConnection()
    source._keepalive = asyncio.create_task(source._run_keepalive())

    await source.aclose()

    assert source._keepalive.cancelled() or source._keepalive.done()


async def test_a_failing_keepalive_stops_the_ticker_instead_of_looping(monkeypatch):
    monkeypatch.setattr("app.deepgram_client.KEEPALIVE_INTERVAL_S", 0.01)

    class Broken:
        def __init__(self):
            self.attempts = 0

        async def send_keep_alive(self):
            self.attempts += 1
            raise RuntimeError("connection is gone")

    source = DeepgramSource(make_settings(), 16000, EN_TO_KO)
    broken = Broken()
    source._connection = broken

    await asyncio.wait_for(source._run_keepalive(), timeout=1)

    assert broken.attempts == 1
