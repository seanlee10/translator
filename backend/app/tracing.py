"""Arize AX tracing. Entirely optional and entirely additive.

Nothing in the caption pipeline depends on this module. `pipeline.py` takes a
`trace_phrase` callable and defaults it to a stdlib no-op, so the policy code
keeps its "imports no I/O library" property and the test suite keeps running
offline with no exporter, no credentials, and no socket.

Two things are switched on here:

- **Auto-instrumentation** for the three translation SDKs (anthropic, groq,
  openai-for-xAI). Those instrumentors wrap the *client*, so they produce one
  LLM span per API call with the prompt, the completion, and token counts.
- **Manual spans** for what the instrumentors cannot see: the phrase itself.
  A phrase is an application-level concept — several Deepgram interim results
  collapsing into one final, then one translation — and no SDK wrapper knows
  that those belong together.

The resulting tree, one per finalized phrase:

    phrase                [CHAIN]  input=EN, output=KO, phrase.id, speech_ms,
    │                              input.audio.transcript=EN
    └─ <sdk span>         [LLM]    emitted by the provider instrumentor

Known gaps, stated rather than papered over:

- **There is no voice/AUDIO span kind here, and it is not for lack of
  trying.** Arize's voice-application docs describe an `AUDIO` span kind,
  but this deployment's read API rejects the value outright — see the
  comment on `_TRANSCRIPTION_SPAN_KIND` for the exact 422. The transcript is
  therefore an attribute on an ordinary CHAIN span.
- The app never produces audio, so `output.audio.*` and time-to-first-token
  do not exist here and are not faked.
- The app never retains input audio either: PCM chunks are proxied straight
  to Deepgram and dropped. So there is no `input.audio.url` — the
  transcription span carries only the transcript.
"""

import logging
from contextlib import contextmanager
from typing import Iterator

from app.config import Settings

logger = logging.getLogger(__name__)

__all__ = ["setup_tracing", "shutdown_tracing", "phrase_span", "tracing_enabled"]

try:
    from openinference.semconv.trace import AudioAttributes, SpanAttributes
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import Status, StatusCode

    _IMPORTS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the extras
    _IMPORTS_AVAILABLE = False


if _IMPORTS_AVAILABLE:
    _SPAN_KIND = SpanAttributes.OPENINFERENCE_SPAN_KIND
    _INPUT_VALUE = SpanAttributes.INPUT_VALUE
    _OUTPUT_VALUE = SpanAttributes.OUTPUT_VALUE
    # The doc's `input.audio.transcript` is the "input." prefix applied to the
    # audio attribute, exactly as `input.value` is to `value`. Built from the
    # constant rather than hardcoded so a semconv rename is a build error.
    _INPUT_AUDIO_TRANSCRIPT = f"input.{AudioAttributes.AUDIO_TRANSCRIPT}"

_CHAIN_SPAN_KIND = "CHAIN"

# The transcription span is a CHAIN, not an "AUDIO".
#
# "AUDIO" was tried first, because Arize's voice-application docs describe it
# as a span kind. It does not work, in two escalating ways:
#
#   1. It is not a member of OpenInferenceSpanKindValues in
#      openinference-semantic-conventions 0.1.32 — only the audio
#      *attributes* (audio.transcript, audio.url, audio.mime_type) ship.
#   2. More seriously, Arize's own read API rejects it. Spans exported with
#      openinference.span.kind="AUDIO" are ingested, but any later
#      `ax traces export` over that project fails with:
#
#        422 Validation Error; kind: Input should be 'LLM', 'CHAIN',
#        'RETRIEVER', 'EMBEDDING', 'AGENT', 'TOOL', 'RERANKER',
#        'GUARDRAIL', 'EVALUATOR', 'PROMPT' or 'UNKNOWN'
#
#      So an "AUDIO" span does not merely fail to render as a voice span —
#      it poisons trace export for every other span in the project.
#
# The audio attributes are kept: they are valid, and they are what carries
# the transcript. Only the kind is downgraded to a value the platform
# accepts. Revisit if Arize adds AUDIO to that enum.
_TRANSCRIPTION_SPAN_KIND = _CHAIN_SPAN_KIND

# Every span kind this Arize deployment's read API will accept. Asserted by
# the test suite so a future edit cannot silently reintroduce the 422.
SUPPORTED_SPAN_KINDS = frozenset(
    {
        "LLM",
        "CHAIN",
        "RETRIEVER",
        "EMBEDDING",
        "AGENT",
        "TOOL",
        "RERANKER",
        "GUARDRAIL",
        "EVALUATOR",
        "PROMPT",
        "UNKNOWN",
    }
)

_tracer = None
_provider = None


def tracing_enabled() -> bool:
    return _tracer is not None


class _NullSpan:
    """What callers get whenever tracing is off. Same shape, no cost."""

    def set_translation(self, text: str, provider: str, latency_ms: int) -> None:
        pass

    def set_error(self, exc: BaseException) -> None:
        pass


class _PhraseSpan:
    def __init__(self, span) -> None:
        self._span = span

    def set_translation(self, text: str, provider: str, latency_ms: int) -> None:
        self._span.set_attribute(_OUTPUT_VALUE, text)
        self._span.set_attribute("translation.provider", provider)
        self._span.set_attribute("translation.latency_ms", latency_ms)
        # Success has to be stated. An OTel span defaults to UNSET, not OK,
        # so a span that only ever gets ERROR on the failure path leaves
        # every successful phrase indistinguishable from one that was never
        # resolved — which makes "filter to failures" useless in Arize.
        self._span.set_status(Status(StatusCode.OK))

    def set_error(self, exc: BaseException) -> None:
        # A failed translation is a failed phrase, but never a failed session:
        # pipeline.py turns it into a per-phrase ErrorOut. Recording it as a
        # span error is what makes that visible in Arize without changing
        # anything about how the app behaves.
        self._span.record_exception(exc)
        self._span.set_status(Status(StatusCode.ERROR, str(exc)))


def setup_tracing(settings: Settings) -> bool:
    """Wire up Arize once. Returns whether tracing actually came up.

    Never raises: an observability failure must not stop the server from
    transcribing. Every failure path logs and returns False.
    """
    global _tracer, _provider

    if _tracer is not None:
        return True

    space_id = settings.arize_space_id.strip()
    api_key = settings.arize_api_key.strip()
    if not (space_id and api_key):
        logger.info(
            "Arize tracing disabled: ARIZE_SPACE_ID and ARIZE_API_KEY are not both set"
        )
        return False

    if not _IMPORTS_AVAILABLE:
        logger.warning(
            "Arize tracing disabled: OpenTelemetry/OpenInference packages are "
            "missing. Reinstall with: pip install -e '.[dev]'"
        )
        return False

    try:
        from arize.otel import register
    except ImportError:
        logger.warning("Arize tracing disabled: arize-otel is not installed")
        return False

    try:
        kwargs = {
            "space_id": space_id,
            "api_key": api_key,
            # Required. Arize rejects spans with HTTP 500 when the project
            # name is absent from the resource — service.name is not accepted
            # in its place. register() sets it as a resource attribute.
            "project_name": settings.arize_project_name,
        }
        if settings.arize_endpoint.strip():
            kwargs["endpoint"] = settings.arize_endpoint.strip()
        _provider = register(**kwargs)
    except Exception:  # noqa: BLE001 - tracing must never break the server
        logger.exception("Arize tracing disabled: register() failed")
        return False

    _instrument_providers()

    _tracer = _provider.get_tracer(__name__)
    logger.info(
        "Arize tracing enabled (project=%s)",
        settings.arize_project_name,
    )
    return True


def _instrument_providers() -> None:
    """Attach the SDK instrumentors.

    All three are attached regardless of which API keys are configured: an
    instrumentor patches the SDK, and patching a library nobody calls costs
    nothing. Tying this to configured_providers() would mean a provider added
    at runtime silently loses its LLM spans.

    Each is guarded separately so one missing or incompatible instrumentor
    does not cost the other two their spans.
    """
    instrumentors = (
        ("anthropic", "openinference.instrumentation.anthropic", "AnthropicInstrumentor"),
        ("groq", "openinference.instrumentation.groq", "GroqInstrumentor"),
        ("openai/xai", "openinference.instrumentation.openai", "OpenAIInstrumentor"),
    )
    for label, module_path, class_name in instrumentors:
        try:
            module = __import__(module_path, fromlist=[class_name])
            getattr(module, class_name)().instrument(tracer_provider=_provider)
        except Exception:  # noqa: BLE001 - one bad instrumentor must not stop the rest
            logger.warning("could not instrument %s SDK", label, exc_info=True)


def shutdown_tracing() -> None:
    """Flush pending spans. Safe to call when tracing never came up."""
    global _tracer, _provider

    if _provider is None:
        return
    try:
        _provider.force_flush()
        _provider.shutdown()
    except Exception:  # noqa: BLE001 - best-effort on the way out
        logger.exception("Arize tracing shutdown failed")
    finally:
        _tracer = None
        _provider = None


@contextmanager
def phrase_span(
    phrase_id: int, text: str, speech_ms: int, direction_id: str
) -> Iterator[object]:
    """One span tree per finalized phrase. A no-op when tracing is off.

    The provider instrumentor's LLM span lands inside this one because
    start_as_current_span attaches to the ambient context, and the caller
    awaits its translation inside the `with` body on the same task.
    """
    if _tracer is None:
        yield _NullSpan()
        return

    with _tracer.start_as_current_span("phrase") as chain:
        chain.set_attribute(_SPAN_KIND, _CHAIN_SPAN_KIND)
        chain.set_attribute(_INPUT_VALUE, text)
        chain.set_attribute("phrase.id", phrase_id)
        # How long this phrase took to say: first interim to final. This is
        # the closest thing to an audio duration the app can honestly report,
        # since it never holds the audio itself.
        chain.set_attribute("phrase.speech_ms", speech_ms)
        # Latency and quality are not comparable across directions: a widget
        # that aggregates the project without filtering on this is averaging
        # two different tasks.
        chain.set_attribute("phrase.direction", direction_id)
        # The transcript rides on the phrase span rather than on a child of
        # its own. A separate "transcription" span was tried and removed:
        # the transcription happens upstream in Deepgram and the pipeline
        # only ever sees its result, so the span had no duration to report,
        # and zero-length spans do not survive the trip reliably — in
        # testing one provider's run lost it outright while two others kept
        # it, with nothing to distinguish them. Since the AUDIO span kind is
        # unusable anyway (see _TRANSCRIPTION_SPAN_KIND), that child carried
        # nothing its parent did not already have.
        chain.set_attribute(_INPUT_AUDIO_TRANSCRIPT, text)

        yield _PhraseSpan(chain)


def reset_for_tests() -> None:
    """Drop module state without flushing. Tests only."""
    global _tracer, _provider
    _tracer = None
    _provider = None
