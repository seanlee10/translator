import asyncio
import contextlib
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.deepgram_client import DeepgramSource
from app.direction import Direction, DIRECTIONS
from app.models import ErrorOut, ProviderInfo, StatusOut
from app.pipeline import Pipeline
from app.providers import build_providers, configured_providers
from app.tracing import phrase_span, setup_tracing, shutdown_tracing
from app.translator import TranslatorRouter

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Tracing is configured once, before any provider client exists, because
    # the SDK instrumentors patch those clients — and clients are built
    # per-session in _default_router_factory. Wrapped because observability
    # failing is never a reason for the server not to transcribe.
    try:
        setup_tracing(_settings())
    except Exception:  # noqa: BLE001 - never block startup
        logger.exception("tracing setup failed; continuing without tracing")
    yield
    shutdown_tracing()


app = FastAPI(title="Live Caption Translator", lifespan=lifespan)

# How long teardown waits for queued final translations before giving up.
DRAIN_TIMEOUT_S = 2.0

TRANSCRIPTION_ENDED_MESSAGE = (
    "Transcription ended: the speech-to-text connection was closed. "
    "Press Stop, then start listening again to resume."
)


def _default_source_factory(settings: Settings, sample_rate: int, direction: Direction):
    return DeepgramSource(settings, sample_rate, direction)


def _default_router_factory(settings: Settings) -> TranslatorRouter:
    providers = build_providers(settings)
    default: str | None = settings.default_provider
    if default not in providers:
        # DEFAULT_PROVIDER names a provider whose key isn't configured (or
        # the setting is simply the class default with that key unset). Fall
        # back to the first configured provider in registry order rather
        # than failing startup — if we required the key instead, the server
        # would refuse to start rather than just hiding that provider, and
        # "provider not configured" would become unreachable.
        fallback = next(iter(providers), None)
        if fallback is not None:
            logger.warning(
                "DEFAULT_PROVIDER %r is not configured; falling back to %r",
                default,
                fallback,
            )
        else:
            logger.warning(
                "no translation provider is configured; the session will "
                "still open but translation will fail per-phrase"
            )
        default = fallback
    return TranslatorRouter(
        providers=providers,
        default=default,
        settings=settings,
        direction=DIRECTIONS[settings.default_direction],
    )


app.state.settings = None  # resolved lazily so imports never require a key
app.state.source_factory = _default_source_factory
app.state.router_factory = _default_router_factory


_TRUE_STRINGS = {"1", "true", "t", "yes", "y", "on"}


def _as_bool(value) -> bool:
    """JSON control values may be real booleans or strings.

    Plain bool() makes the string "false" evaluate to True, which silently
    inverted the 'Finals only' toggle for any client that sent it as text.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return bool(value)


def _settings() -> Settings:
    if app.state.settings is None:
        app.state.settings = get_settings()
    return app.state.settings


# Fixed at import time rather than read from Settings: middleware must be
# registered before startup, and Settings requires API keys that tests do not set.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/providers", response_model=list[ProviderInfo])
def providers() -> list[ProviderInfo]:
    """The set of translation providers this server can actually use.

    The frontend renders its model selector from this list instead of a
    hardcoded trio, and only ever advertises a provider whose API key is
    configured — see app/providers/__init__.py's registry.
    """
    settings = _settings()
    return [
        ProviderInfo(
            id=spec.id, label=spec.label, model=getattr(settings, spec.model_attr)
        )
        for spec in configured_providers(settings)
    ]


@app.websocket("/ws/session")
async def session(ws: WebSocket) -> None:
    # A single lock serializes every write to this socket. The pipeline's own
    # lock only orders CaptionOut emits against each other; TranscriptOut,
    # StatusOut, and ErrorOut all bypass it, so without this lock up to three
    # tasks (this handler, the pump, the pipeline's final worker) could call
    # ws.send_json concurrently.
    send_lock = asyncio.Lock()

    async def emit(event: BaseModel) -> None:
        async with send_lock:
            await ws.send_json(event.model_dump())

    source = None
    router = None
    pipeline = None
    pump: asyncio.Task | None = None

    try:
        # accept() and the first emit are inside the try so a client that
        # vanishes immediately after connecting takes the same clean
        # WebSocketDisconnect path as every other disconnect.
        await ws.accept()
        settings = _settings()
        await emit(StatusOut(state="connected"))

        while True:
            message = await ws.receive()

            if message["type"] == "websocket.disconnect":
                break

            text = message.get("text")
            if text is not None:
                try:
                    control = json.loads(text)
                    kind = control.get("type")
                except Exception as exc:  # noqa: BLE001 - a bad frame is never fatal
                    await emit(ErrorOut(message=f"malformed control message: {exc}"))
                    continue

                if kind == "start":
                    if source is not None:
                        continue  # a second 'start' is a no-op, not an error
                    try:
                        sample_rate = int(
                            control.get("sample_rate", settings.sample_rate)
                        )
                    except (TypeError, ValueError) as exc:
                        await emit(ErrorOut(message=f"invalid sample_rate: {exc}"))
                        continue
                    direction = DIRECTIONS[settings.default_direction]
                    source = app.state.source_factory(settings, sample_rate, direction)
                    await source.connect()

                    router = app.state.router_factory(settings)
                    pipeline = Pipeline(
                        translate=router.translate,
                        emit=emit,
                        settings=settings,
                        # A no-op unless setup_tracing() actually came up.
                        trace_phrase=phrase_span,
                    )
                    await pipeline.start()
                    pump = asyncio.create_task(_pump(source, pipeline, emit))
                    await emit(StatusOut(state="listening"))

                elif kind == "config":
                    if source is None:
                        await emit(
                            ErrorOut(message="cannot configure before 'start'")
                        )
                        continue
                    if "translate_interims" in control:
                        pipeline.set_translate_interims(
                            _as_bool(control["translate_interims"])
                        )
                    if "provider" in control:
                        try:
                            router.set_provider(control["provider"])
                        except ValueError as exc:
                            await emit(ErrorOut(message=str(exc)))

                elif kind == "stop":
                    break

            else:
                chunk = message.get("bytes")
                # Once the pump has finished the transcription stream is over
                # (clean close or error) and the user has already been told.
                # Pushing more audio at a closed socket only produces a raw
                # library exception like "sent 1000 (OK); then received
                # 1000 (OK)" on top of the intelligible message.
                if (
                    chunk is not None
                    and source is not None
                    and (pump is None or not pump.done())
                ):
                    await source.send_audio(chunk)

    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - report, then close cleanly
        logger.exception("session failed")
        try:
            await emit(ErrorOut(message=str(exc), fatal=True))
        except Exception:  # noqa: BLE001 - socket may already be gone
            pass
    finally:
        # Each step gets its own try/except: a failure in one must not skip
        # the others, and the pump is awaited (not just cancelled) so it
        # cannot still be mid-handle() while the pipeline it feeds is closed.
        if pump is not None:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
        if pipeline is not None:
            try:
                # Let the queued finals finish first: without this, pressing
                # Stop discards the last phrase's translation and the closing
                # sentence never gets its Korean. Bounded, so a wedged
                # provider cannot hang the shutdown path.
                await asyncio.wait_for(pipeline.drain(), timeout=DRAIN_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.warning("pipeline drain timed out; closing anyway")
            except Exception:  # noqa: BLE001 - aclose must still run
                logger.exception("pipeline drain failed")
            try:
                await pipeline.aclose()
            except Exception:  # noqa: BLE001 - source cleanup must still run
                logger.exception("pipeline close failed")
        if source is not None:
            try:
                await source.aclose()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.exception("source close failed")
        try:
            # Closes the socket on every normal exit path too (e.g. 'stop'),
            # not just on error: returning from the handler without this
            # never sends a close frame, so a client-initiated 'stop' would
            # otherwise leave its connection open indefinitely.
            await ws.close()
        except Exception:  # noqa: BLE001 - socket may already be gone
            pass


async def _pump(source, pipeline: Pipeline, emit) -> None:
    """Feed transcript events into the pipeline until the source ends."""
    try:
        async for event in source.events():
            await pipeline.handle(event)
    except Exception as exc:  # noqa: BLE001 - a dead source must not kill the socket
        logger.exception("transcript source failed")
        try:
            await emit(ErrorOut(message=f"transcription failed: {exc}", fatal=True))
        except Exception:  # noqa: BLE001 - socket may already be gone
            pass
        return

    # The source finished without an error: a clean close (code 1000), which
    # Deepgram sends on its own for idle timeouts, rotation, and max stream
    # duration. Returning silently here left the browser with no signal at
    # all — status still "listening", nothing in the transcript — until the
    # next audio chunk hit the closed socket and surfaced a raw library
    # exception. Not fatal: the session is still usable and restartable.
    logger.info("transcript source ended cleanly")
    try:
        await emit(StatusOut(state="closed"))
        await emit(ErrorOut(message=TRANSCRIPTION_ENDED_MESSAGE, fatal=False))
    except Exception:  # noqa: BLE001 - socket may already be gone
        pass
