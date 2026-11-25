import asyncio
import logging
from typing import AsyncIterator

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

from app.config import Settings
from app.direction import Direction
from app.models import TranscriptEvent

logger = logging.getLogger(__name__)

# Deepgram closes an idle stream after ~10s of no audio. The SDK does NOT
# send keepalives on its own — v7 exposes only a manual send_keep_alive() —
# so this module runs its own ticker while connected.
KEEPALIVE_INTERVAL_S = 5.0


class DeepgramStreamError(RuntimeError):
    """Raised from events() when the stream ended because of an error,
    as opposed to a graceful close."""


def connect_params(
    settings: Settings, sample_rate: int, direction: Direction
) -> dict[str, str]:
    """Every value is a string — the SDK types these as Optional[str]."""
    return {
        "model": settings.deepgram_model,
        # The direction decides what we are listening to, not what we are
        # producing. nova-3 supports Korean streaming, so the model is the
        # same either way and only this value changes.
        "language": direction.deepgram_language,
        "encoding": "linear16",
        "sample_rate": str(sample_rate),
        "channels": "1",
        "interim_results": "true",
        "punctuate": "true",
        "smart_format": "true",
        "endpointing": str(settings.endpointing_ms),
        "utterance_end_ms": str(settings.utterance_end_ms),
    }


def message_to_dict(message) -> dict:
    """Normalize any SDK message object to the raw wire dict.

    The wire JSON shape is documented and stable; the SDK's generated attribute
    names are not. Converting here means PhraseTracker never has to change when
    the SDK regenerates.
    """
    if isinstance(message, dict):
        return message
    for method in ("model_dump", "dict", "to_dict"):
        converter = getattr(message, method, None)
        if callable(converter):
            try:
                return converter()
            except Exception:  # noqa: BLE001 - fall through to the next strategy
                continue
    return dict(getattr(message, "__dict__", {}))


class PhraseTracker:
    """Assigns phrase IDs. Deepgram does not provide them.

    A phrase spans every interim result up to and including one is_final
    result with non-empty text.
    """

    def __init__(self) -> None:
        self._phrase_id = 0

    def normalize(self, message: dict) -> TranscriptEvent | None:
        if message.get("type") != "Results":
            return None

        alternatives = (message.get("channel") or {}).get("alternatives") or []
        text = alternatives[0].get("transcript", "") if alternatives else ""
        if not text.strip():
            # Empty finals arrive during silence. Advancing the counter here
            # would strand the open phrase and freeze its provisional Korean.
            return None

        is_final = bool(message.get("is_final"))
        event = TranscriptEvent(phrase_id=self._phrase_id, text=text, is_final=is_final)
        if is_final:
            self._phrase_id += 1
        return event


class DeepgramSource:
    """Adapts the SDK's callback API to an async iterator of TranscriptEvents."""

    def __init__(
        self, settings: Settings, sample_rate: int, direction: Direction
    ) -> None:
        self._settings = settings
        self._sample_rate = sample_rate
        self._direction = direction
        self._tracker = PhraseTracker()
        self._queue: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        self._client = AsyncDeepgramClient(api_key=settings.deepgram_api_key)
        self._ctx = None
        self._connection = None
        self._listener: asyncio.Task | None = None
        self._keepalive: asyncio.Task | None = None
        self._error: Exception | None = None

    async def connect(self) -> None:
        self._ctx = self._client.listen.v1.connect(
            **connect_params(self._settings, self._sample_rate, self._direction)
        )
        self._connection = await self._ctx.__aenter__()

        self._connection.on(EventType.MESSAGE, self._on_message)
        self._connection.on(EventType.ERROR, self._on_error)
        self._connection.on(EventType.CLOSE, self._on_close)

        # start_listening() blocks while pumping, so it owns a background task.
        self._listener = asyncio.create_task(self._connection.start_listening())
        self._keepalive = asyncio.create_task(self._run_keepalive())

    async def _run_keepalive(self) -> None:
        """Hold the stream open through extended silence.

        deepgram-sdk 7.x has no automatic keepalive; send_keep_alive() is the
        only mechanism it offers. Without this, a pause longer than Deepgram's
        idle timeout ends the stream mid-session.
        """
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL_S)
            connection = self._connection
            if connection is None:
                return
            try:
                await connection.send_keep_alive()
            except Exception:  # noqa: BLE001 - the stream is closing anyway
                logger.debug("deepgram keepalive failed", exc_info=True)
                return

    async def _on_message(self, message) -> None:
        event = self._tracker.normalize(message_to_dict(message))
        if event is not None:
            self._queue.put_nowait(event)

    async def _on_error(self, error) -> None:
        logger.error("deepgram error: %s", error)
        self._error = error
        self._queue.put_nowait(None)  # sentinel: end the stream

    async def _on_close(self, _event=None) -> None:
        self._queue.put_nowait(None)

    async def send_audio(self, chunk: bytes) -> None:
        if self._connection is not None:
            await self._connection.send_media(chunk)

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                if self._error is not None:
                    raise DeepgramStreamError(str(self._error))
                return
            yield event

    async def aclose(self) -> None:
        tasks = [t for t in (self._listener, self._keepalive) if t is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._ctx is not None:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 - already closing
                logger.debug("deepgram close raised", exc_info=True)
