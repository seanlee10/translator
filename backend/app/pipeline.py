import asyncio
import logging
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Awaitable, Callable, ContextManager, Iterator, Protocol, Sequence

from pydantic import BaseModel

from app.config import Settings
from app.direction import Direction
from app.models import (
    CaptionOut,
    ContextPair,
    ErrorOut,
    TranscriptEvent,
    TranscriptOut,
    TranslationResult,
)

logger = logging.getLogger(__name__)

Translate = Callable[[str, Sequence[ContextPair]], Awaitable[TranslationResult]]
Emit = Callable[[BaseModel], Awaitable[None]]

FINAL_RETRY_DELAY_S = 0.25


class PhraseSpan(Protocol):
    """What a tracing backend hands back for one phrase.

    Deliberately tiny: the pipeline reports two facts about a phrase and
    knows nothing about spans, exporters, or attribute names.
    """

    def set_translation(self, text: str, provider: str, latency_ms: int) -> None: ...

    def set_error(self, exc: BaseException) -> None: ...


class _NullPhraseSpan:
    def set_translation(self, text: str, provider: str, latency_ms: int) -> None:
        pass

    def set_error(self, exc: BaseException) -> None:
        pass


@contextmanager
def null_trace_phrase(
    phrase_id: int, text: str, speech_ms: int, direction_id: str
) -> Iterator[PhraseSpan]:
    """The default. Tracing is injected, never imported.

    This module holds every timing and ordering decision in the system and
    imports no I/O library, which is what makes the whole policy testable
    offline. An OTLP exporter is exactly the kind of dependency that would
    break that, so app/tracing.py is passed in by main.py instead.
    """
    yield _NullPhraseSpan()


TracePhrase = Callable[[int, str, int, str], ContextManager[PhraseSpan]]


@dataclass
class PhraseState:
    created_at: float
    last_translation_at: float | None = None
    in_flight: bool = False
    closed: bool = False


class Pipeline:
    """Turns transcript events into caption events.

    Holds every timing and ordering decision in the system, and deliberately
    depends on no I/O library so the whole policy is testable offline.
    """

    def __init__(
        self,
        translate: Translate,
        emit: Emit,
        settings: Settings,
        direction: Direction,
        now: Callable[[], float] = time.monotonic,
        trace_phrase: TracePhrase = null_trace_phrase,
    ) -> None:
        self._translate = translate
        self._emit = emit
        self._s = settings
        self._direction = direction
        self._now = now
        self._trace_phrase = trace_phrase
        self._translate_interims = settings.translate_interims

        self._phrases: dict[int, PhraseState] = {}
        self._context: deque[ContextPair] = deque(maxlen=settings.context_pairs)
        self._final_queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self._final_worker: asyncio.Task | None = None
        self._interim_tasks: set[asyncio.Task] = set()
        # Serializes CaptionOut emission so a stale provisional can never be
        # written to the wire after the final that superseded it.
        self._emit_lock = asyncio.Lock()

    def set_translate_interims(self, value: bool) -> None:
        """Runtime toggle driven by the browser's config message."""
        self._translate_interims = value

    async def start(self) -> None:
        if self._final_worker is not None:
            return
        self._final_worker = asyncio.create_task(self._run_final_worker())

    async def handle(self, event: TranscriptEvent) -> None:
        """Never blocks on a translation — the caller is the transcript loop."""
        state = self._phrases.get(event.phrase_id)
        if state is None:
            state = PhraseState(created_at=self._now())
            self._phrases[event.phrase_id] = state

        en, ko = self._direction.to_slots(event.text, None)
        await self._emit(TranscriptOut(phrase_id=event.phrase_id, en=en, ko=ko))

        if event.is_final:
            # The source-language text is published for finals too, *before*
            # the translation is enqueued. Otherwise a finalized phrase
            # reaches the browser only inside a successful CaptionOut, and a
            # failed translation leaves no row at all — just a global error
            # banner. The spec requires the phrase be published with its
            # source text and an error marker. The client's rank guard
            # treats this as an interim, so it refines the source-language
            # text of an already-displayed provisional without regressing
            # its status or blanking the translation.
            state.closed = True
            await self._final_queue.put((event.phrase_id, event.text))
            return

        self._maybe_translate_interim(event, state)

    # ---- finals: serialized so context stays in phrase order ----

    async def _run_final_worker(self) -> None:
        while True:
            phrase_id, text = await self._final_queue.get()
            try:
                # First interim to final: how long this phrase took to say.
                state = self._phrases.get(phrase_id)
                speech_ms = (
                    int((self._now() - state.created_at) * 1000)
                    if state is not None
                    else 0
                )
                # The translation is awaited inside this block so the
                # provider SDK's own span nests under the phrase.
                with self._trace_phrase(
                    phrase_id, text, speech_ms, self._direction.id
                ) as span:
                    try:
                        result = await self._translate_final(text)
                    except (
                        Exception
                    ) as exc:  # noqa: BLE001 - shown in the UI, never fatal
                        span.set_error(exc)
                        await self._emit(
                            ErrorOut(
                                phrase_id=phrase_id,
                                message=f"translation failed: {exc}",
                            )
                        )
                    else:
                        span.set_translation(
                            result.text, result.provider, result.latency_ms
                        )
                        en, ko = self._direction.to_slots(text, result.text)
                        # to_slots returns Optionals for the transcript case;
                        # here both sides exist by construction, and CaptionOut
                        # requires both. Narrow at this one site rather than
                        # loosening the model.
                        assert en is not None and ko is not None
                        self._context.append(ContextPair(en=en, ko=ko))
                        async with self._emit_lock:
                            await self._emit(
                                CaptionOut(
                                    phrase_id=phrase_id,
                                    en=en,
                                    ko=ko,
                                    status="final",
                                    provider=result.provider,
                                    latency_ms=result.latency_ms,
                                )
                            )
            except Exception:  # noqa: BLE001 - a broken emit must not kill the worker
                logger.exception(
                    "final worker: unhandled error for phrase %s", phrase_id
                )
            finally:
                self._final_queue.task_done()

    async def _translate_final(self, text: str) -> TranslationResult:
        """Finals matter, so retry once. Interims never retry."""
        context = tuple(self._context)
        try:
            return await self._call(text, context)
        except Exception:
            await asyncio.sleep(FINAL_RETRY_DELAY_S)
            return await self._call(text, context)

    async def _call(
        self, text: str, context: Sequence[ContextPair]
    ) -> TranslationResult:
        return await asyncio.wait_for(
            self._translate(text, context), timeout=self._s.translation_timeout_s
        )

    # ---- interims: fire-and-forget, guarded ----

    def _maybe_translate_interim(
        self, event: TranscriptEvent, state: PhraseState
    ) -> None:
        if not self._translate_interims or state.closed or state.in_flight:
            return

        reference = (
            state.last_translation_at
            if state.last_translation_at is not None
            else state.created_at
        )
        if (self._now() - reference) * 1000 < self._s.debounce_ms:
            return

        state.in_flight = True
        state.last_translation_at = self._now()
        task = asyncio.create_task(self._translate_interim(event, state))
        self._interim_tasks.add(task)
        task.add_done_callback(self._interim_tasks.discard)

    async def _translate_interim(
        self, event: TranscriptEvent, state: PhraseState
    ) -> None:
        try:
            result = await self._call(event.text, tuple(self._context))
        except Exception:  # noqa: BLE001 - interims are about to be superseded
            return
        finally:
            state.in_flight = False

        # The lock serializes against the finals worker's own CaptionOut emit,
        # so the recheck below is airtight: no final can slip an emit between
        # this check and this task's own emit call.
        async with self._emit_lock:
            if state.closed:
                return  # sequence guard: a final already won this phrase

            en, ko = self._direction.to_slots(event.text, result.text)
            assert en is not None and ko is not None
            await self._emit(
                CaptionOut(
                    phrase_id=event.phrase_id,
                    en=en,
                    ko=ko,
                    status="provisional",
                    provider=result.provider,
                    latency_ms=result.latency_ms,
                )
            )

    # ---- lifecycle ----

    async def drain(self) -> None:
        """Wait for all in-flight work. Used by tests and by shutdown."""
        for _ in range(3):
            if self._interim_tasks:
                await asyncio.gather(*list(self._interim_tasks), return_exceptions=True)
            await self._final_queue.join()

    async def aclose(self) -> None:
        tasks = list(self._interim_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._final_worker is not None:
            self._final_worker.cancel()
            try:
                await self._final_worker
            except asyncio.CancelledError:
                pass
