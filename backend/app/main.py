import asyncio
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.deepgram_client import DeepgramSource
from app.models import ErrorOut, StatusOut
from app.pipeline import Pipeline
from app.providers import build_providers
from app.translator import TranslatorRouter

logger = logging.getLogger(__name__)

app = FastAPI(title="Live Caption Translator")


def _default_source_factory(settings: Settings, sample_rate: int):
    return DeepgramSource(settings, sample_rate)


def _default_router_factory(settings: Settings) -> TranslatorRouter:
    return TranslatorRouter(
        providers=build_providers(settings),
        default=settings.default_provider,
        settings=settings,
    )


app.state.settings = None  # resolved lazily so imports never require a key
app.state.source_factory = _default_source_factory
app.state.router_factory = _default_router_factory


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


@app.websocket("/ws/session")
async def session(ws: WebSocket) -> None:
    await ws.accept()
    settings = _settings()

    async def emit(event: BaseModel) -> None:
        await ws.send_json(event.model_dump())

    await emit(StatusOut(state="connected"))

    source = None
    router = None
    pipeline = None
    pump: asyncio.Task | None = None

    try:
        while True:
            message = await ws.receive()

            if message["type"] == "websocket.disconnect":
                break

            text = message.get("text")
            if text is not None:
                control = json.loads(text)
                kind = control.get("type")

                if kind == "start":
                    if source is not None:
                        continue
                    sample_rate = int(control.get("sample_rate", settings.sample_rate))
                    source = app.state.source_factory(settings, sample_rate)
                    await source.connect()

                    router = app.state.router_factory(settings)
                    pipeline = Pipeline(
                        translate=router.translate, emit=emit, settings=settings
                    )
                    await pipeline.start()
                    pump = asyncio.create_task(_pump(source, pipeline, emit))
                    await emit(StatusOut(state="listening"))

                elif kind == "config":
                    if pipeline is not None and "translate_interims" in control:
                        pipeline.set_translate_interims(
                            bool(control["translate_interims"])
                        )
                    if router is not None and "provider" in control:
                        try:
                            router.set_provider(control["provider"])
                        except ValueError as exc:
                            await emit(ErrorOut(message=str(exc)))

                elif kind == "stop":
                    break

            else:
                chunk = message.get("bytes")
                if chunk is not None and source is not None:
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
        if pump is not None:
            pump.cancel()
        if pipeline is not None:
            await pipeline.aclose()
        if source is not None:
            await source.aclose()


async def _pump(source, pipeline: Pipeline, emit) -> None:
    """Feed transcript events into the pipeline until the source ends."""
    try:
        async for event in source.events():
            await pipeline.handle(event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a dead source must not kill the socket
        logger.exception("transcript source failed")
        await emit(ErrorOut(message=f"transcription failed: {exc}", fatal=True))
