import asyncio

from fastapi.testclient import TestClient

from app.config import Settings
from app.models import TranscriptEvent, TranslationResult


def make_settings(**overrides) -> Settings:
    base = dict(deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g")
    base.update(overrides)
    return Settings(**base)


class FakeSource:
    """Replays a scripted transcript instead of talking to Deepgram."""

    script: list[TranscriptEvent] = []
    last_sample_rate: int | None = None

    def __init__(self, settings, sample_rate):
        FakeSource.last_sample_rate = sample_rate
        self.audio_chunks: list[bytes] = []
        self._released = asyncio.Event()

    async def connect(self):
        return None

    async def send_audio(self, chunk):
        self.audio_chunks.append(chunk)
        self._released.set()

    async def events(self):
        await self._released.wait()  # wait until audio actually arrives
        for event in FakeSource.script:
            yield event
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

    app.state.settings = make_settings(**settings_overrides)
    app.state.source_factory = FakeSource
    app.state.router_factory = FakeRouter
    return TestClient(app)


def test_health_endpoint():
    assert build_client().get("/health").json() == {"status": "ok"}


def test_a_scripted_utterance_produces_transcript_then_caption():
    FakeSource.script = [
        TranscriptEvent(phrase_id=0, text="hel", is_final=False),
        TranscriptEvent(phrase_id=0, text="hello there", is_final=True),
    ]
    client = build_client(translate_interims=False)

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
    FakeSource.script = []
    client = build_client()

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()
        ws.send_json({"type": "start", "sample_rate": 44100})
        ws.receive_json()

    assert FakeSource.last_sample_rate == 44100


def test_config_message_switches_the_provider_mid_session():
    FakeSource.script = [TranscriptEvent(phrase_id=0, text="hello", is_final=True)]
    client = build_client(translate_interims=False)

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
    FakeSource.script = []
    client = build_client()

    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()
        ws.send_json({"type": "start", "sample_rate": 16000})
        ws.receive_json()
        ws.send_json({"type": "config", "provider": "nope"})

        message = ws.receive_json()

    assert message["type"] == "error"
    assert message["fatal"] is False
