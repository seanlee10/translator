import asyncio
import time

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from app.config import Settings
from app.deepgram_client import DeepgramStreamError
from app.models import TranscriptEvent, TranslationResult


def make_settings(**overrides) -> Settings:
    base = dict(deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g")
    base.update(overrides)
    return Settings(**base)


class FakeSource:
    """Replays a scripted transcript instead of talking to Deepgram.

    Every event still requires at least one audio chunk to have arrived
    (matching a live connection: nothing comes back before audio goes in).
    A script index may additionally require more than one chunk via
    `gate_after`, e.g. `{1: 2}` means script[1] won't be yielded until the
    *second* `send_audio` call. This lets a test force real event-loop turns
    to happen between two scripted events — e.g. enough for an in-flight
    interim-translation task to actually run to completion — instead of an
    entire scripted phrase flowing through in one uninterrupted burst.
    """

    script: list[TranscriptEvent] = []
    gate_after: dict[int, int] = {}
    last_sample_rate: int | None = None
    fail_after: Exception | None = None

    def __init__(self, settings, sample_rate):
        FakeSource.last_sample_rate = sample_rate
        self.audio_chunks: list[bytes] = []
        self._chunk_count = 0
        self._chunk_event = asyncio.Event()

    async def connect(self):
        return None

    async def send_audio(self, chunk):
        self.audio_chunks.append(chunk)
        self._chunk_count += 1
        self._chunk_event.set()

    async def _wait_for_chunks(self, n: int) -> None:
        while self._chunk_count < n:
            await self._chunk_event.wait()
            self._chunk_event.clear()

    async def events(self):
        await self._wait_for_chunks(1)  # wait until audio actually arrives
        for index, event in enumerate(FakeSource.script):
            required = FakeSource.gate_after.get(index, 1)
            if required > 1:
                await self._wait_for_chunks(required)
            yield event
        if FakeSource.fail_after is not None:
            raise FakeSource.fail_after
        await asyncio.sleep(3600)  # then idle, like a live connection

    async def aclose(self):
        return None


class FakeRouter:
    """Stands in for TranslatorRouter with the same public surface."""

    last: "FakeRouter | None" = None

    def __init__(self, settings):
        self.provider = "anthropic"
        FakeRouter.last = self

    def set_provider(self, name):
        if name not in ("anthropic", "groq"):
            raise ValueError(name)
        self.provider = name

    async def translate(self, text, context):
        return TranslationResult(ko=f"[{self.provider}]{text}", provider=self.provider, latency_ms=7)


def build_client(**settings_overrides):
    from app.main import app

    # Reset class-level fake state so tests are order-independent: without
    # this, last_sample_rate/fail_after leak across tests and only happen
    # to pass because the asserted values differ from test to test.
    FakeSource.last_sample_rate = None
    FakeSource.script = []
    FakeSource.gate_after = {}
    FakeSource.fail_after = None
    FakeRouter.last = None

    app.state.settings = make_settings(**settings_overrides)
    app.state.source_factory = FakeSource
    app.state.router_factory = FakeRouter
    return TestClient(app)


def test_health_endpoint():
    assert build_client().get("/health").json() == {"status": "ok"}


def test_a_scripted_utterance_produces_transcript_then_caption():
    client = build_client(translate_interims=False)
    FakeSource.script = [
        TranscriptEvent(phrase_id=0, text="hel", is_final=False),
        TranscriptEvent(phrase_id=0, text="hello there", is_final=True),
    ]

    with client.websocket_connect("/ws/session") as ws:
        assert ws.receive_json() == {"type": "status", "state": "connected"}

        ws.send_json({"type": "start", "sample_rate": 48000})
        assert ws.receive_json() == {"type": "status", "state": "listening"}

        ws.send_bytes(b"\x00\x01" * 160)

        assert ws.receive_json() == {
            "type": "transcript",
            "phrase_id": 0,
            "en": "hel",
            "status": "interim",
        }
        assert ws.receive_json() == {
            "type": "caption",
            "phrase_id": 0,
            "en": "hello there",
            "ko": "[anthropic]hello there",
            "status": "final",
            "provider": "anthropic",
            "latency_ms": 7,
        }


def test_the_browsers_sample_rate_configures_the_transcription_source():
    client = build_client()

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()
        ws.send_json({"type": "start", "sample_rate": 44100})
        ws.receive_json()

    assert FakeSource.last_sample_rate == 44100


def test_config_message_switches_the_provider_mid_session():
    client = build_client(translate_interims=False)
    FakeSource.script = [TranscriptEvent(phrase_id=0, text="hello", is_final=True)]

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()
        ws.send_json({"type": "start", "sample_rate": 16000})
        ws.receive_json()
        ws.send_json({"type": "config", "translate_interims": False, "provider": "groq"})
        ws.send_bytes(b"\x00\x01" * 160)

        caption = ws.receive_json()

    assert caption["provider"] == "groq"
    assert caption["ko"] == "[groq]hello"


def test_an_unknown_provider_reports_an_error_without_killing_the_session():
    client = build_client(translate_interims=False)
    FakeSource.script = [TranscriptEvent(phrase_id=0, text="hello", is_final=True)]

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()
        ws.send_json({"type": "start", "sample_rate": 16000})
        ws.receive_json()
        ws.send_json({"type": "config", "provider": "nope"})

        message = ws.receive_json()
        assert message["type"] == "error"
        assert message["fatal"] is False

        # Prove the session actually survived: do one more successful
        # round-trip on the same socket rather than trusting the test's name.
        ws.send_json({"type": "config", "provider": "groq"})
        ws.send_bytes(b"\x00\x01" * 160)

        caption = ws.receive_json()

    assert caption["type"] == "caption"
    assert caption["provider"] == "groq"
    assert caption["ko"] == "[groq]hello"


def test_the_translate_interims_toggle_actually_changes_behavior():
    # Built with interims ENABLED and debounce disabled: if the config
    # message below did not actually reach `set_translate_interims`, the
    # interim event spawns an interim-translation task immediately.
    #
    # The first version of this test yielded the interim and the final
    # event from FakeSource back-to-back with no real suspension between
    # them. That made it pass even with the toggle broken: the final event
    # closed the phrase (`state.closed = True`) before the interim task's
    # `asyncio.wait_for(...)` ever got a turn, so the pipeline's own
    # sequence guard ("a late interim never overwrites a finalized phrase",
    # covered separately in test_pipeline.py) silently swallowed the
    # provisional — the same wire output the *fixed* toggle also produces.
    # The test therefore couldn't tell "toggle works" from "toggle broken,
    # guard saved us" apart.
    #
    # Fix: `gate_after={1: 2}` makes FakeSource withhold the final event
    # until a *second* audio chunk arrives. Between the two chunks the test
    # sleeps for real wall-clock time; because TestClient runs the ASGI app
    # on a persistent background-thread event loop (not just during
    # portal.call()), that sleep gives a broken-toggle's interim task every
    # opportunity to run FakeRouter.translate() (no real I/O, near-instant)
    # to completion and emit its provisional *before* the final is ever
    # delivered to the pipeline. A working toggle never creates that task at
    # all, so nothing arrives regardless of how long the test waits.
    client = build_client(translate_interims=True, debounce_ms=0)
    FakeSource.script = [
        TranscriptEvent(phrase_id=0, text="hel", is_final=False),
        TranscriptEvent(phrase_id=0, text="hello", is_final=True),
    ]
    FakeSource.gate_after = {1: 2}

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()  # connected
        ws.send_json({"type": "start", "sample_rate": 16000})
        ws.receive_json()  # listening

        ws.send_json({"type": "config", "translate_interims": False})
        ws.send_bytes(b"\x00\x01" * 160)  # 1st chunk: releases the interim

        assert ws.receive_json() == {
            "type": "transcript",
            "phrase_id": 0,
            "en": "hel",
            "status": "interim",
        }

        # Give a would-be interim-translation task every chance to run to
        # completion before the final is ever released to the pipeline.
        time.sleep(0.1)

        ws.send_bytes(b"\x00\x01" * 160)  # 2nd chunk: releases the final
        caption = ws.receive_json()

    assert caption == {
        "type": "caption",
        "phrase_id": 0,
        "en": "hello",
        "ko": "[anthropic]hello",
        "status": "final",
        "provider": "anthropic",
        "latency_ms": 7,
    }


def test_stop_closes_the_connection_cleanly():
    client = build_client()
    FakeSource.script = []

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()  # connected
        ws.send_json({"type": "start", "sample_rate": 16000})
        ws.receive_json()  # listening

        ws.send_json({"type": "stop"})

        # The server must send an actual close frame, not just stop
        # responding: the next receive should observe a clean disconnect,
        # not hang.
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_a_dead_transcription_source_reports_a_fatal_error_and_does_not_hang():
    client = build_client()
    FakeSource.script = []
    FakeSource.fail_after = DeepgramStreamError("stream died")

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()  # connected
        ws.send_json({"type": "start", "sample_rate": 16000})
        ws.receive_json()  # listening

        ws.send_bytes(b"\x00\x01" * 160)  # releases the source's events()

        message = ws.receive_json()

    assert message["type"] == "error"
    assert message["fatal"] is True
    assert "stream died" in message["message"]


def test_binary_audio_before_start_does_not_crash_the_session():
    client = build_client()
    FakeSource.script = []

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()  # connected

        # No source exists yet; this must be silently dropped, not raise.
        ws.send_bytes(b"\x00\x01" * 160)

        # The session must still be fully usable afterward.
        ws.send_json({"type": "start", "sample_rate": 16000})
        assert ws.receive_json() == {"type": "status", "state": "listening"}


def test_a_second_start_message_is_ignored():
    client = build_client()
    FakeSource.script = []

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()  # connected
        ws.send_json({"type": "start", "sample_rate": 16000})
        ws.receive_json()  # listening

        ws.send_json({"type": "start", "sample_rate": 48000})

        # No new source was built for the second 'start': the session is
        # still driven by the first one, and remains fully responsive.
        ws.send_json({"type": "config", "translate_interims": False})
        ws.send_bytes(b"\x00\x01" * 160)

    assert FakeSource.last_sample_rate == 16000


def test_config_before_start_reports_a_non_fatal_error():
    client = build_client()

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()  # connected
        ws.send_json({"type": "config", "provider": "nope"})

        message = ws.receive_json()

    assert message["type"] == "error"
    assert message["fatal"] is False
