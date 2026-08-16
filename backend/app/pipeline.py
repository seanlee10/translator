import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

from pydantic import BaseModel

from app.config import Settings
from app.models import (
    CaptionOut,
    ContextPair,
    ErrorOut,
    TranscriptEvent,
    TranscriptOut,
    TranslationResult,
)

Translate = Callable[[str, Sequence[ContextPair]], Awaitable[TranslationResult]]
Emit = Callable[[BaseModel], Awaitable[None]]


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
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._translate = translate
        self._emit = emit
        self._s = settings
        self._now = now
        self._translate_interims = settings.translate_interims

        self._phrases: dict[int, PhraseState] = {}
        self._context: deque[ContextPair] = deque(maxlen=settings.context_pairs)
        self._final_queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self._final_worker: asyncio.Task | None = None
        self._interim_tasks: set[asyncio.Task] = set()

    def set_translate_interims(self, value: bool) -> None:
        """Runtime toggle driven by the browser's config message."""
        self._translate_interims = value

    async def start(self) -> None:
        self._final_worker = asyncio.create_task(self._run_final_worker())

    async def handle(self, event: TranscriptEvent) -> None:
        """Never blocks on a translation — the caller is the transcript loop."""
        state = self._phrases.get(event.phrase_id)
        if state is None:
            state = PhraseState(created_at=self._now())
            self._phrases[event.phrase_id] = state

        if event.is_final:
            state.closed = True
            await self._final_queue.put((event.phrase_id, event.text))
            return

        await self._emit(TranscriptOut(phrase_id=event.phrase_id, en=event.text))
        self._maybe_translate_interim(event, state)

    # ---- finals: serialized so context stays in phrase order ----

    async def _run_final_worker(self) -> None:
        while True:
            phrase_id, text = await self._final_queue.get()
            try:
                result = await self._translate_final(text)
            except Exception as exc:  # noqa: BLE001 - shown in the UI, never fatal
                await self._emit(
                    ErrorOut(phrase_id=phrase_id, message=f"translation failed: {exc}")
                )
            else:
                self._context.append(ContextPair(en=text, ko=result.ko))
                await self._emit(
                    CaptionOut(
                        phrase_id=phrase_id,
                        en=text,
                        ko=result.ko,
                        status="final",
                        provider=result.provider,
                        latency_ms=result.latency_ms,
                    )
                )
            finally:
                self._final_queue.task_done()

    async def _translate_final(self, text: str) -> TranslationResult:
        """Finals matter, so retry once. Interims never retry."""
        context = tuple(self._context)
        try:
            return await self._call(text, context)
        except Exception:
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

        if state.closed:
            return  # sequence guard: a final already won this phrase

        await self._emit(
            CaptionOut(
                phrase_id=event.phrase_id,
                en=event.text,
                ko=result.ko,
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
        for task in list(self._interim_tasks):
            task.cancel()
        if self._final_worker is not None:
            self._final_worker.cancel()
            try:
                await self._final_worker
            except asyncio.CancelledError:
                pass
