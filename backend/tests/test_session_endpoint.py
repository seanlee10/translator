import asyncio
import time

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from app.config import Settings
from app.deepgram_client import DeepgramStreamError
from app.models import TranscriptEvent, TranslationResult


def make_settings(**overrides) -> Settings:
    base = dict(
        deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g", xai_api_key="x"
    )
    base.update(overrides)
    # _env_file=None keeps the suite hermetic: Settings now resolves a real
    # repo-root .env, and a developer's local one must not change defaults.
    return Settings(_env_file=None, **base)


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
    # events() returns instead of idling — a clean Deepgram close (code 1000).
    end_after_script: bool = False
    # connect() raises — the most common first-run failure, a bad API key.
    connect_error: Exception | None = None

    def __init__(self, settings, sample_rate, direction):
        FakeSource.last_sample_rate = sample_rate
        self.audio_chunks: list[bytes] = []
        self._chunk_count = 0
        self._chunk_event = asyncio.Event()

    async def connect(self):
        if FakeSource.connect_error is not None:
            raise FakeSource.connect_error
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
        if FakeSource.end_after_script:
            return  # graceful end: the generator simply stops
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
        return TranslationResult(text=f"[{self.provider}]{text}", provider=self.provider, latency_ms=7)


def build_client(**settings_overrides):
    from app.main import app

    # Reset class-level fake state so tests are order-independent: without
    # this, last_sample_rate/fail_after leak across tests and only happen
    # to pass because the asserted values differ from test to test.
    FakeSource.last_sample_rate = None
    FakeSource.script = []
    FakeSource.gate_after = {}
    FakeSource.fail_after = None
    FakeSource.end_after_script = False
    FakeSource.connect_error = None
    FakeRouter.last = None

    app.state.settings = make_settings(**settings_overrides)
    app.state.source_factory = FakeSource
    app.state.router_factory = FakeRouter
    return TestClient(app)


def receive_caption(ws) -> dict:
    """Read past the English-only transcript events to the next non-transcript.

    Finals publish their English as a transcript event before translation, so
    a test that cares about the caption must skip those. Deliberately returns
    *any* non-transcript message (a caption, an error, ...) rather than
    looping until a caption specifically appears — a test asserting on a final
    caption must still fail loudly if a provisional or an error arrives first.
    """
    while True:
        message = ws.receive_json()
        if message["type"] != "transcript":
            return message


def test_health_endpoint():
    assert build_client().get("/health").json() == {"status": "ok"}


# ---- GET /providers ----


def test_providers_endpoint_returns_all_three_when_all_keys_are_configured():
    client = build_client()  # build_client's default helper sets all three

    response = client.get("/providers")

    assert response.status_code == 200
    body = response.json()
    assert {p["id"] for p in body} == {"anthropic", "groq", "xai"}
    assert {
        "id": "anthropic",
        "label": "Claude Haiku 4.5",
        "model": "claude-haiku-4-5",
    } in body


def test_providers_endpoint_returns_only_the_configured_subset():
    client = build_client(groq_api_key="", xai_api_key="")

    body = client.get("/providers").json()

    assert [p["id"] for p in body] == ["anthropic"]


def test_providers_endpoint_returns_an_empty_list_when_no_key_is_configured():
    client = build_client(anthropic_api_key="", groq_api_key="", xai_api_key="")

    assert client.get("/providers").json() == []


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
            "ko": None,
            "status": "interim",
        }
        # The final's English is published before it is enqueued for
        # translation, so the row exists (and carries the smart-formatted
        # text) even if the translation later fails.
        assert ws.receive_json() == {
            "type": "transcript",
            "phrase_id": 0,
            "en": "hello there",
            "ko": None,
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

        caption = receive_caption(ws)

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

        caption = receive_caption(ws)

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
            "ko": None,
            "status": "interim",
        }

        # Give a would-be interim-translation task every chance to run to
        # completion before the final is ever released to the pipeline.
        time.sleep(0.1)

        ws.send_bytes(b"\x00\x01" * 160)  # 2nd chunk: releases the final
        caption = receive_caption(ws)

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


def test_a_clean_source_close_reports_closed_status_and_a_non_fatal_notice():
    # A clean Deepgram close (code 1000 — its own idle timeout, rotation, or
    # max stream duration) makes events() simply return. That used to produce
    # no user-visible signal whatsoever: status stayed "listening" and the
    # only eventual hint was a raw websockets exception string when the next
    # audio chunk hit the closed socket.
    client = build_client()
    FakeSource.script = []
    FakeSource.end_after_script = True

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()  # connected
        ws.send_json({"type": "start", "sample_rate": 16000})
        ws.receive_json()  # listening

        ws.send_bytes(b"\x00\x01" * 160)  # releases the source's events()

        assert ws.receive_json() == {"type": "status", "state": "closed"}

        notice = ws.receive_json()
        assert notice["type"] == "error"
        assert notice["fatal"] is False
        assert "transcription ended" in notice["message"].lower()


def test_audio_sent_after_a_clean_close_does_not_produce_a_raw_exception():
    client = build_client()
    FakeSource.script = []
    FakeSource.end_after_script = True

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()  # connected
        ws.send_json({"type": "start", "sample_rate": 16000})
        ws.receive_json()  # listening

        ws.send_bytes(b"\x00\x01" * 160)
        ws.receive_json()  # status: closed
        ws.receive_json()  # the non-fatal notice

        # The mic is still running browser-side; more audio keeps arriving.
        # It must be dropped rather than pushed at a dead source.
        for _ in range(3):
            ws.send_bytes(b"\x00\x01" * 160)

        # Nothing further is emitted: the session ends cleanly on 'stop'
        # rather than reporting a library-level error to the user.
        ws.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_a_source_that_fails_to_connect_reports_a_fatal_error():
    # The most common first-run failure: a bad or missing Deepgram key makes
    # connect() raise. It must reach the browser as a fatal error, not an
    # unhandled exception that drops the socket with no explanation.
    client = build_client()
    FakeSource.connect_error = RuntimeError("invalid deepgram credentials")

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()  # connected
        ws.send_json({"type": "start", "sample_rate": 16000})

        message = ws.receive_json()

    assert message["type"] == "error"
    assert message["fatal"] is True
    assert "invalid deepgram credentials" in message["message"]


def test_translate_interims_false_as_a_json_string_is_not_truthy():
    # bool("false") is True. Sending the flag as a string therefore used to
    # *enable* interim translation while the UI said "Finals only".
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

        ws.send_json({"type": "config", "translate_interims": "false"})
        ws.send_bytes(b"\x00\x01" * 160)  # 1st chunk: releases the interim

        assert ws.receive_json() == {
            "type": "transcript",
            "phrase_id": 0,
            "en": "hel",
            "ko": None,
            "status": "interim",
        }

        # Same gating rationale as the boolean version of this test: give a
        # would-be interim translation every chance to complete and emit
        # before the final is released.
        time.sleep(0.1)

        ws.send_bytes(b"\x00\x01" * 160)  # 2nd chunk: releases the final
        caption = receive_caption(ws)

    assert caption["status"] == "final"  # no provisional was ever emitted
    assert caption["ko"] == "[anthropic]hello"


def test_stop_still_translates_the_last_phrase_before_shutting_down():
    # Teardown drains the finals queue: without it, pressing Stop discards
    # the closing sentence's translation and it never gets any Korean.

    class SlowRouter(FakeRouter):
        async def translate(self, text, context):
            await asyncio.sleep(0.2)
            return await super().translate(text, context)

    client = build_client(translate_interims=False)
    from app.main import app

    app.state.router_factory = SlowRouter
    FakeSource.script = [TranscriptEvent(phrase_id=0, text="goodbye", is_final=True)]

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()  # connected
        ws.send_json({"type": "start", "sample_rate": 16000})
        ws.receive_json()  # listening

        ws.send_bytes(b"\x00\x01" * 160)
        assert ws.receive_json()["type"] == "transcript"

        # Stop immediately, while the translation is still in flight.
        ws.send_json({"type": "stop"})

        caption = receive_caption(ws)

    assert caption["type"] == "caption"
    assert caption["status"] == "final"
    assert caption["ko"] == "[anthropic]goodbye"
