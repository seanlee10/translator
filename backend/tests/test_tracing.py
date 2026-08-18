"""Tracing is additive and optional. These tests prove both.

Every test here runs with no credentials, no exporter, and no socket. That
is the point: adding observability must not have made the suite depend on a
network, and a server with no Arize keys must behave exactly as before.
"""

import asyncio
from contextlib import contextmanager

import pytest

import app.tracing as tracing
from app.config import Settings
from app.models import TranscriptEvent, TranslationResult
from app.pipeline import Pipeline, null_trace_phrase


def make_settings(**overrides) -> Settings:
    base = dict(
        deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g", xai_api_key="x"
    )
    base.update(overrides)
    # _env_file=None keeps the suite hermetic. It matters more than usual
    # here: a developer's real .env now carries ARIZE_API_KEY, and without
    # this the tests below would try to open an exporter to Arize.
    return Settings(_env_file=None, **base)


# ---- setup refuses to start without complete credentials ----


@pytest.fixture
def register_must_not_be_called(monkeypatch):
    """Fails loudly if setup_tracing tries to build an exporter.

    Asserting only `setup_tracing(...) is False` is too weak to be worth
    having: register() also returns a failure for empty credentials, so such
    a test passes even with the guard deleted. What actually matters is that
    no exporter is constructed at all — no client, no collector address, no
    connection attempt from a suite that is supposed to be offline.
    """
    import arize.otel

    calls = []

    def spy(**kwargs):
        calls.append(kwargs)
        raise AssertionError(
            "register() was called despite incomplete Arize credentials"
        )

    monkeypatch.setattr(arize.otel, "register", spy)
    return calls


def test_tracing_is_off_when_no_credentials_are_set(register_must_not_be_called):
    tracing.reset_for_tests()
    assert tracing.setup_tracing(make_settings()) is False
    assert tracing.tracing_enabled() is False
    assert register_must_not_be_called == []


def test_tracing_is_off_when_only_the_space_id_is_set(register_must_not_be_called):
    """A half-configured pair must not produce a client that fails at export."""
    tracing.reset_for_tests()
    settings = make_settings(arize_space_id="U3BhY2U6MQ==", arize_api_key="")
    assert tracing.setup_tracing(settings) is False
    assert register_must_not_be_called == []


def test_tracing_is_off_when_only_the_api_key_is_set(register_must_not_be_called):
    tracing.reset_for_tests()
    settings = make_settings(arize_space_id="", arize_api_key="ak-whatever")
    assert tracing.setup_tracing(settings) is False
    assert register_must_not_be_called == []


def test_a_whitespace_only_credential_counts_as_unset(register_must_not_be_called):
    """A copied .env.example with a blank value must not switch tracing on."""
    tracing.reset_for_tests()
    settings = make_settings(arize_space_id="   ", arize_api_key="  ")
    assert tracing.setup_tracing(settings) is False
    assert register_must_not_be_called == []


def test_credentials_are_passed_through_verbatim(monkeypatch):
    """The space id and project name reach register() unmodified.

    A silently substituted space id is the failure mode that costs a whole
    afternoon: spans export successfully and land somewhere nobody is
    looking.
    """
    tracing.reset_for_tests()

    import arize.otel

    seen = {}

    class FakeProvider:
        def get_tracer(self, name):
            return object()

    def spy(**kwargs):
        seen.update(kwargs)
        return FakeProvider()

    monkeypatch.setattr(arize.otel, "register", spy)
    monkeypatch.setattr(tracing, "_instrument_providers", lambda: None)

    settings = make_settings(
        arize_space_id="U3BhY2U6Mjc2Nzc6eVVRRA==",
        arize_api_key="ak-secret",
        arize_project_name="live-caption-translator",
    )
    assert tracing.setup_tracing(settings) is True

    assert seen["space_id"] == "U3BhY2U6Mjc2Nzc6eVVRRA=="
    assert seen["api_key"] == "ak-secret"
    # Arize rejects spans with HTTP 500 when the project name is missing from
    # the resource; service.name is not accepted in its place.
    assert seen["project_name"] == "live-caption-translator"
    # An unset endpoint must not be forwarded as "" — that would override
    # arize-otel's default collector with an empty address.
    assert "endpoint" not in seen

    tracing.reset_for_tests()


def test_setup_never_raises_when_register_fails(monkeypatch):
    """Observability failing is never a reason for the server not to start."""
    tracing.reset_for_tests()

    import arize.otel

    def boom(**kwargs):
        raise RuntimeError("collector unreachable")

    monkeypatch.setattr(arize.otel, "register", boom)

    settings = make_settings(arize_space_id="U3BhY2U6MQ==", arize_api_key="ak-x")
    assert tracing.setup_tracing(settings) is False
    assert tracing.tracing_enabled() is False


def test_shutdown_is_safe_when_tracing_never_came_up():
    tracing.reset_for_tests()
    tracing.shutdown_tracing()  # must not raise


# ---- span kinds must be values the Arize API will accept ----


def test_every_span_kind_this_module_emits_is_accepted_by_arize():
    """Guards a bug that shipped once and was only visible on read-back.

    "AUDIO" is what Arize's voice docs describe, so it was the first thing
    tried. Spans carrying it are ingested without complaint — and then every
    subsequent `ax traces export` over that project fails with:

        422 Validation Error; kind: Input should be 'LLM', 'CHAIN',
        'RETRIEVER', 'EMBEDDING', 'AGENT', 'TOOL', 'RERANKER',
        'GUARDRAIL', 'EVALUATOR', 'PROMPT' or 'UNKNOWN'

    One unreadable span poisons trace export for the whole project, and
    nothing at write time says so. Hence a test rather than a comment.
    """
    emitted = {tracing._CHAIN_SPAN_KIND, tracing._TRANSCRIPTION_SPAN_KIND}

    assert emitted <= tracing.SUPPORTED_SPAN_KINDS
    assert "AUDIO" not in emitted
    assert "AUDIO" not in tracing.SUPPORTED_SPAN_KINDS


def test_the_phrase_span_still_carries_the_audio_transcript_attribute():
    """The kind was downgraded; the audio semantics were not dropped."""
    from openinference.semconv.trace import AudioAttributes

    assert tracing._INPUT_AUDIO_TRANSCRIPT == "input.audio.transcript"
    assert tracing._INPUT_AUDIO_TRANSCRIPT.endswith(AudioAttributes.AUDIO_TRANSCRIPT)


def test_a_successful_phrase_span_is_marked_OK_not_left_unset():
    """UNSET is not success.

    An OTel span starts UNSET and stays there unless something says
    otherwise. Recording ERROR on the failure path only is a trap: every
    successful phrase then looks identical to one that never resolved, and
    filtering by status in Arize returns nothing useful.
    """
    from opentelemetry.trace import StatusCode

    recorded = {}

    class FakeSpan:
        def set_attribute(self, key, value):
            recorded[key] = value

        def set_status(self, status):
            recorded["__status__"] = status

        def record_exception(self, exc):
            recorded["__exc__"] = exc

    span = tracing._PhraseSpan(FakeSpan())
    span.set_translation("안녕하세요", "xai", 42)

    assert recorded["__status__"].status_code is StatusCode.OK

    failed = tracing._PhraseSpan(FakeSpan())
    failed.set_error(RuntimeError("boom"))
    assert recorded["__status__"].status_code is StatusCode.ERROR


# ---- the null span honours the same interface ----


def test_phrase_span_yields_a_usable_null_span_when_tracing_is_off():
    tracing.reset_for_tests()
    with tracing.phrase_span(0, "hello", 120) as span:
        span.set_translation("안녕", "anthropic", 42)
        span.set_error(RuntimeError("boom"))


def test_the_pipelines_default_hook_is_the_null_one():
    """Nothing is traced unless main.py explicitly wires app/tracing.py in."""
    with null_trace_phrase(0, "hello", 120) as span:
        span.set_translation("안녕", "anthropic", 42)
        span.set_error(RuntimeError("boom"))


# ---- the pipeline drives the hook correctly ----


class RecordingSpan:
    def __init__(self):
        self.translations: list[tuple[str, str, int]] = []
        self.errors: list[BaseException] = []

    def set_translation(self, ko, provider, latency_ms):
        self.translations.append((ko, provider, latency_ms))

    def set_error(self, exc):
        self.errors.append(exc)


class RecordingTracer:
    """Stands in for app/tracing.py's phrase_span."""

    def __init__(self):
        self.calls: list[tuple[int, str, int]] = []
        self.spans: list[RecordingSpan] = []
        self.exited = 0

    @contextmanager
    def __call__(self, phrase_id, text, speech_ms):
        self.calls.append((phrase_id, text, speech_ms))
        span = RecordingSpan()
        self.spans.append(span)
        try:
            yield span
        finally:
            self.exited += 1


async def _run(pipeline, events):
    await pipeline.start()
    for event in events:
        await pipeline.handle(event)
    await pipeline.drain()
    await pipeline.aclose()


async def test_a_finalized_phrase_opens_and_closes_exactly_one_span():
    tracer = RecordingTracer()
    emitted = []

    async def translate(text, context):
        return TranslationResult(ko=f"[ko]{text}", provider="anthropic", latency_ms=7)

    pipeline = Pipeline(
        translate=translate,
        emit=lambda event: asyncio.sleep(0),
        settings=make_settings(translate_interims=False),
        trace_phrase=tracer,
    )
    await _run(
        pipeline,
        [
            TranscriptEvent(phrase_id=0, text="hello", is_final=False),
            TranscriptEvent(phrase_id=0, text="hello there", is_final=True),
        ],
    )

    assert len(tracer.calls) == 1, "one span per finalized phrase, not per event"
    assert tracer.calls[0][0] == 0
    assert tracer.calls[0][1] == "hello there"
    assert tracer.exited == 1
    assert emitted == []


async def test_a_successful_translation_is_recorded_on_the_span():
    tracer = RecordingTracer()

    async def translate(text, context):
        return TranslationResult(ko="안녕하세요", provider="groq", latency_ms=123)

    pipeline = Pipeline(
        translate=translate,
        emit=lambda event: asyncio.sleep(0),
        settings=make_settings(translate_interims=False),
        trace_phrase=tracer,
    )
    await _run(pipeline, [TranscriptEvent(phrase_id=0, text="hi", is_final=True)])

    assert tracer.spans[0].translations == [("안녕하세요", "groq", 123)]
    assert tracer.spans[0].errors == []


async def test_a_failed_translation_is_recorded_on_the_span_and_still_emits():
    """The span records the failure; the phrase still reaches the browser.

    Per-phrase failure isolation is the behaviour tracing must not disturb.
    """
    tracer = RecordingTracer()
    emitted = []

    async def translate(text, context):
        raise RuntimeError("provider down")

    async def emit(event):
        emitted.append(event)

    pipeline = Pipeline(
        translate=translate,
        emit=emit,
        settings=make_settings(translate_interims=False),
        trace_phrase=tracer,
    )
    await _run(pipeline, [TranscriptEvent(phrase_id=0, text="hi", is_final=True)])

    assert len(tracer.spans[0].errors) == 1
    assert isinstance(tracer.spans[0].errors[0], RuntimeError)
    assert tracer.spans[0].translations == []
    assert tracer.exited == 1, "the span closes even when the translation fails"
    # The English row and the per-phrase error both still went out.
    kinds = [type(event).__name__ for event in emitted]
    assert "TranscriptOut" in kinds and "ErrorOut" in kinds


async def test_a_broken_tracer_never_kills_the_finals_worker():
    """Tracing is additive. If it throws, captions must keep flowing.

    The finals worker is a single long-lived task: an exception escaping its
    loop body would strand every later phrase forever.
    """

    @contextmanager
    def exploding_tracer(phrase_id, text, speech_ms):
        raise RuntimeError("tracer exploded")
        yield  # pragma: no cover

    emitted = []

    async def translate(text, context):
        return TranslationResult(ko=f"[ko]{text}", provider="anthropic", latency_ms=1)

    async def emit(event):
        emitted.append(event)

    pipeline = Pipeline(
        translate=translate,
        emit=emit,
        settings=make_settings(translate_interims=False),
        trace_phrase=exploding_tracer,
    )
    await _run(
        pipeline,
        [
            TranscriptEvent(phrase_id=0, text="first", is_final=True),
            TranscriptEvent(phrase_id=1, text="second", is_final=True),
        ],
    )

    # Both phrases still published their English: the worker survived the
    # first tracer explosion and processed the second phrase.
    english = [e.en for e in emitted if type(e).__name__ == "TranscriptOut"]
    assert english == ["first", "second"]


async def test_speech_ms_is_measured_from_the_phrases_first_event():
    """The one honest stand-in for audio duration this app can report."""
    clock = {"t": 100.0}
    tracer = RecordingTracer()

    async def translate(text, context):
        return TranslationResult(ko="x", provider="anthropic", latency_ms=1)

    pipeline = Pipeline(
        translate=translate,
        emit=lambda event: asyncio.sleep(0),
        settings=make_settings(translate_interims=False),
        now=lambda: clock["t"],
        trace_phrase=tracer,
    )
    await pipeline.start()
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="hel", is_final=False))
    clock["t"] = 101.5
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="hello", is_final=True))
    await pipeline.drain()
    await pipeline.aclose()

    assert tracer.calls[0][2] == 1500
