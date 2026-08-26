import asyncio
from contextlib import contextmanager

from pydantic import BaseModel

from app.config import Settings
from app.direction import EN_TO_KO, KO_TO_EN
from app.models import (
    CaptionOut,
    ErrorOut,
    TranscriptEvent,
    TranscriptOut,
    TranslationResult,
)
from app.pipeline import Pipeline, _NullPhraseSpan


def make_settings(**overrides) -> Settings:
    base = dict(
        deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g", xai_api_key="x"
    )
    # translate_interims defaults to False on Settings itself now (the
    # frontend never renders a provisional caption, so translating interims
    # by default would just waste API calls) — but most of this file's
    # tests exist specifically to exercise the interim-translation
    # machinery (debounce, in-flight guard, sequence guard), which is still
    # correct and still re-enabled via TRANSLATE_INTERIMS/the config
    # message. Pin it on here so those tests keep covering that code
    # without every call site needing its own override; tests of the
    # interims-off behavior still pass translate_interims=False explicitly.
    base["translate_interims"] = True
    base.update(overrides)
    # _env_file=None keeps the suite hermetic: Settings now resolves a real
    # repo-root .env, and a developer's local one must not change defaults.
    return Settings(_env_file=None, **base)


class FakeTranslator:
    """Records calls and returns a deterministic result."""

    def __init__(self, provider="anthropic"):
        self.provider = provider
        self.calls: list[tuple[str, list[tuple[str, str]]]] = []

    async def __call__(self, text, context):
        self.calls.append((text, [(c.en, c.ko) for c in context]))
        return TranslationResult(
            text=f"[ko]{text}", provider=self.provider, latency_ms=100
        )


class Recorder:
    def __init__(self):
        self.events: list[BaseModel] = []

    async def __call__(self, event):
        self.events.append(event)

    def captions(self) -> list[CaptionOut]:
        return [e for e in self.events if isinstance(e, CaptionOut)]


async def run(events, settings=None, translator=None, direction=EN_TO_KO):
    settings = settings or make_settings()
    translator = translator or FakeTranslator()
    recorder = Recorder()
    pipeline = Pipeline(
        translate=translator, emit=recorder, settings=settings, direction=direction
    )
    await pipeline.start()
    for event in events:
        await pipeline.handle(event)
    await pipeline.drain()
    await pipeline.aclose()
    return recorder, translator


async def test_a_final_phrase_is_translated_and_emitted_with_provenance():
    recorder, translator = await run(
        [TranscriptEvent(phrase_id=0, text="hello there", is_final=True)]
    )

    assert translator.calls == [("hello there", [])]
    assert recorder.captions() == [
        CaptionOut(
            phrase_id=0,
            en="hello there",
            ko="[ko]hello there",
            status="final",
            provider="anthropic",
            latency_ms=100,
        )
    ]


async def test_context_accumulates_in_phrase_order():
    _, translator = await run(
        [
            TranscriptEvent(phrase_id=0, text="one", is_final=True),
            TranscriptEvent(phrase_id=1, text="two", is_final=True),
            TranscriptEvent(phrase_id=2, text="three", is_final=True),
        ]
    )

    assert translator.calls[0][1] == []
    assert translator.calls[1][1] == [("one", "[ko]one")]
    assert translator.calls[2][1] == [("one", "[ko]one"), ("two", "[ko]two")]


async def test_context_window_is_capped_at_the_configured_size():
    _, translator = await run(
        [TranscriptEvent(phrase_id=i, text=str(i), is_final=True) for i in range(4)],
        settings=make_settings(context_pairs=2),
    )

    assert translator.calls[3][1] == [("1", "[ko]1"), ("2", "[ko]2")]


async def test_final_translations_are_emitted_in_order_despite_uneven_latency():
    class SlowFirst:
        def __init__(self):
            self.n = 0

        async def __call__(self, text, context):
            self.n += 1
            await asyncio.sleep(0.05 if self.n == 1 else 0)
            return TranslationResult(
                text=f"[ko]{text}", provider="anthropic", latency_ms=1
            )

    recorder, _ = await run(
        [
            TranscriptEvent(phrase_id=0, text="first", is_final=True),
            TranscriptEvent(phrase_id=1, text="second", is_final=True),
        ],
        translator=SlowFirst(),
    )

    assert [c.phrase_id for c in recorder.captions()] == [0, 1]


async def test_a_failing_final_emits_an_error_retries_once_and_keeps_going():
    class Flaky:
        def __init__(self):
            self.n = 0

        async def __call__(self, text, context):
            self.n += 1
            if text == "bad":
                raise RuntimeError("boom")
            return TranslationResult(
                text=f"[ko]{text}", provider="anthropic", latency_ms=1
            )

    flaky = Flaky()
    recorder, _ = await run(
        [
            TranscriptEvent(phrase_id=0, text="bad", is_final=True),
            TranscriptEvent(phrase_id=1, text="good", is_final=True),
        ],
        translator=flaky,
    )

    assert any(isinstance(e, ErrorOut) and e.phrase_id == 0 for e in recorder.events)
    assert flaky.n == 3  # "bad" attempted twice, "good" once
    assert [c.phrase_id for c in recorder.captions()] == [1]


async def test_context_order_is_correct_despite_uneven_latency():
    class SlowFirst:
        def __init__(self):
            self.n = 0
            self.calls: list[tuple[str, list[tuple[str, str]]]] = []

        async def __call__(self, text, context):
            self.n += 1
            self.calls.append((text, [(c.en, c.ko) for c in context]))
            await asyncio.sleep(0.05 if self.n == 1 else 0)
            return TranslationResult(
                text=f"[ko]{text}", provider="anthropic", latency_ms=1
            )

    _, translator = await run(
        [
            TranscriptEvent(phrase_id=0, text="first", is_final=True),
            TranscriptEvent(phrase_id=1, text="second", is_final=True),
        ],
        translator=SlowFirst(),
    )

    # A naive create_task-per-final implementation would let "second"'s
    # translation call race ahead of "first"'s and see an empty context.
    # The serialized worker guarantees phrase order regardless of latency.
    assert translator.calls[1][1] == [("first", "[ko]first")]


async def test_handle_never_blocks_on_translation():
    gate = asyncio.Event()

    class Gated:
        async def __call__(self, text, context):
            await gate.wait()
            return TranslationResult(
                text=f"[ko]{text}", provider="anthropic", latency_ms=1
            )

    settings = make_settings()
    translator = Gated()
    recorder = Recorder()
    pipeline = Pipeline(
        translate=translator, emit=recorder, settings=settings, direction=EN_TO_KO
    )
    await pipeline.start()

    # handle() must return as soon as the final is enqueued, not once it is
    # translated -- the caller is the live transcript read loop.
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="hello", is_final=True))

    assert recorder.captions() == []

    gate.set()
    await pipeline.drain()
    await pipeline.aclose()

    assert [c.phrase_id for c in recorder.captions()] == [0]


async def test_a_failed_phrase_does_not_enter_the_context_window():
    class OnlyGood:
        async def __call__(self, text, context):
            if text == "bad":
                raise RuntimeError("boom")
            return TranslationResult(
                text=f"[ko]{text}", provider="anthropic", latency_ms=1
            )

    recorder, _ = await run(
        [
            TranscriptEvent(phrase_id=0, text="bad", is_final=True),
            TranscriptEvent(phrase_id=1, text="good", is_final=True),
        ],
        translator=OnlyGood(),
    )

    assert [(c.phrase_id, c.ko) for c in recorder.captions()] == [(1, "[ko]good")]


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000.0


async def run_with_clock(items, settings=None, translator=None, direction=EN_TO_KO):
    """items: TranscriptEvent to handle, or int milliseconds to advance."""
    settings = settings or make_settings()
    translator = translator or FakeTranslator()
    recorder = Recorder()
    clock = FakeClock()
    pipeline = Pipeline(
        translate=translator,
        emit=recorder,
        settings=settings,
        direction=direction,
        now=clock,
    )
    await pipeline.start()
    for item in items:
        if isinstance(item, int):
            clock.advance(item)
        else:
            await pipeline.handle(item)
            await pipeline.drain()
    await pipeline.drain()
    await pipeline.aclose()
    return recorder, translator


async def test_english_interims_publish_without_any_translation_call():
    recorder, translator = await run_with_clock(
        [TranscriptEvent(phrase_id=0, text="hel", is_final=False)],
        settings=make_settings(translate_interims=False),
    )

    assert [e.en for e in recorder.events if isinstance(e, TranscriptOut)] == ["hel"]
    assert translator.calls == []


async def test_first_interim_is_not_translated_before_the_debounce_window():
    _, translator = await run_with_clock(
        [TranscriptEvent(phrase_id=0, text="hel", is_final=False)]
    )

    assert translator.calls == []


async def test_interim_is_translated_once_the_debounce_window_elapses():
    recorder, translator = await run_with_clock(
        [
            TranscriptEvent(phrase_id=0, text="hel", is_final=False),
            500,
            TranscriptEvent(phrase_id=0, text="hello th", is_final=False),
        ]
    )

    assert translator.calls == [("hello th", [])]
    assert recorder.captions() == [
        CaptionOut(
            phrase_id=0,
            en="hello th",
            ko="[ko]hello th",
            status="provisional",
            provider="anthropic",
            latency_ms=100,
        )
    ]


async def test_interims_inside_the_debounce_window_are_skipped():
    _, translator = await run_with_clock(
        [
            TranscriptEvent(phrase_id=0, text="a", is_final=False),
            500,
            TranscriptEvent(phrase_id=0, text="a b", is_final=False),
            100,  # too soon after the previous translation
            TranscriptEvent(phrase_id=0, text="a b c", is_final=False),
            500,
            TranscriptEvent(phrase_id=0, text="a b c d", is_final=False),
        ]
    )

    assert [text for text, _ in translator.calls] == ["a b", "a b c d"]


async def test_an_in_flight_interim_suppresses_a_new_one_rather_than_queueing():
    gate = asyncio.Event()
    calls: list[str] = []

    class Blocking:
        async def __call__(self, text, context):
            calls.append(text)
            await gate.wait()
            return TranslationResult(
                text=f"[ko]{text}", provider="anthropic", latency_ms=1
            )

    recorder = Recorder()
    clock = FakeClock()
    pipeline = Pipeline(
        translate=Blocking(),
        emit=recorder,
        settings=make_settings(),
        direction=EN_TO_KO,
        now=clock,
    )
    await pipeline.start()

    await pipeline.handle(TranscriptEvent(phrase_id=0, text="a", is_final=False))
    clock.advance(500)
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="a b", is_final=False))
    await asyncio.sleep(0)  # let the interim task start and block
    clock.advance(500)
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="a b c", is_final=False))
    await asyncio.sleep(0)

    assert calls == ["a b"]  # the second was skipped, not queued

    gate.set()
    await pipeline.drain()
    await pipeline.aclose()

    # If the second interim had merely been deferred (queued) instead of
    # dropped, it would run once the gate opens and "a b c" would appear
    # here. Re-checking after drain distinguishes dropped-forever from
    # merely-deferred.
    assert calls == ["a b"]


async def test_a_late_interim_never_overwrites_a_finalized_phrase():
    gate = asyncio.Event()

    class SlowInterim:
        async def __call__(self, text, context):
            if not text.endswith("!"):
                await gate.wait()
            return TranslationResult(
                text=f"[ko]{text}", provider="anthropic", latency_ms=1
            )

    recorder = Recorder()
    clock = FakeClock()
    pipeline = Pipeline(
        translate=SlowInterim(),
        emit=recorder,
        settings=make_settings(),
        direction=EN_TO_KO,
        now=clock,
    )
    await pipeline.start()

    await pipeline.handle(TranscriptEvent(phrase_id=0, text="hel", is_final=False))
    clock.advance(500)
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="hello", is_final=False))
    await asyncio.sleep(0)  # let the interim task start and block on the gate
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="hello!", is_final=True))

    # Release the stalled interim BEFORE draining, so it actually resumes,
    # reaches the sequence guard, sees state.closed is True, and skips its
    # emit. Draining first (as an earlier version of this test did) would
    # deadlock the gate release behind drain()'s wait, so the interim would
    # only ever "complete" via the real-clock translation_timeout_s timeout
    # -- masking the guard entirely.
    gate.set()
    await pipeline.drain()
    await pipeline.aclose()

    assert [(c.status, c.ko) for c in recorder.captions()] == [("final", "[ko]hello!")]


async def test_a_failing_interim_is_silent_and_never_retried():
    class AlwaysFails:
        def __init__(self):
            self.n = 0

        async def __call__(self, text, context):
            self.n += 1
            raise RuntimeError("boom")

    failing = AlwaysFails()
    recorder, _ = await run_with_clock(
        [
            TranscriptEvent(phrase_id=0, text="a", is_final=False),
            500,
            TranscriptEvent(phrase_id=0, text="a b", is_final=False),
        ],
        translator=failing,
    )

    assert failing.n == 1  # no retry
    assert not [e for e in recorder.events if isinstance(e, ErrorOut)]


async def test_set_translate_interims_takes_effect_immediately():
    recorder = Recorder()
    clock = FakeClock()
    translator = FakeTranslator()
    pipeline = Pipeline(
        translate=translator,
        emit=recorder,
        settings=make_settings(),
        direction=EN_TO_KO,
        now=clock,
    )
    await pipeline.start()

    pipeline.set_translate_interims(False)
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="a", is_final=False))
    clock.advance(500)
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="a b", is_final=False))
    await pipeline.drain()

    assert translator.calls == []

    pipeline.set_translate_interims(True)
    clock.advance(500)
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="a b c", is_final=False))
    await pipeline.drain()
    await pipeline.aclose()

    assert [text for text, _ in translator.calls] == ["a b c"]


async def test_a_final_publishes_its_english_even_when_its_translation_fails():
    # Spec, Error Handling: "Publish the phrase with its English and an error
    # marker, no Korean." Before this, a finalized phrase's English reached
    # the browser only inside a successful CaptionOut, so a run of provider
    # failures produced no transcript rows at all — just a global banner each
    # new failure overwrote. Most visible with "Finals only" checked, where
    # no interim TranscriptOut precedes the final either.
    class AlwaysFails:
        async def __call__(self, text, context):
            raise RuntimeError("provider is down")

    recorder, _ = await run(
        [TranscriptEvent(phrase_id=0, text="Hello there.", is_final=True)],
        translator=AlwaysFails(),
    )

    transcripts = [e for e in recorder.events if isinstance(e, TranscriptOut)]
    assert [(e.phrase_id, e.en) for e in transcripts] == [(0, "Hello there.")]

    errors = [e for e in recorder.events if isinstance(e, ErrorOut)]
    assert [e.phrase_id for e in errors] == [0]

    assert recorder.captions() == []  # no Korean, as required


async def test_the_finals_english_is_published_before_it_is_enqueued():
    # Ordering matters: the row must exist while the translation is still in
    # flight, not only once it resolves.
    gate = asyncio.Event()

    class Gated:
        async def __call__(self, text, context):
            await gate.wait()
            return TranslationResult(text=f"[ko]{text}", provider="anthropic", latency_ms=1)

    recorder = Recorder()
    pipeline = Pipeline(
        translate=Gated(), emit=recorder, settings=make_settings(), direction=EN_TO_KO
    )
    await pipeline.start()

    await pipeline.handle(TranscriptEvent(phrase_id=0, text="hello", is_final=True))

    assert [e.en for e in recorder.events if isinstance(e, TranscriptOut)] == ["hello"]
    assert recorder.captions() == []

    gate.set()
    await pipeline.drain()
    await pipeline.aclose()


async def test_a_raising_emit_does_not_kill_the_final_worker():
    # The worker wraps its whole loop body precisely so a failing emit (a
    # socket write that loses the race with a disconnect, say) cannot take
    # the worker down and silently stop translating for the rest of the
    # session. Deleting that guard must fail this test.
    class ExplodingEmit:
        def __init__(self):
            self.events: list[BaseModel] = []

        async def __call__(self, event):
            if isinstance(event, CaptionOut) and event.phrase_id == 0:
                raise RuntimeError("socket write failed")
            self.events.append(event)

        def captions(self) -> list[CaptionOut]:
            return [e for e in self.events if isinstance(e, CaptionOut)]

    emit = ExplodingEmit()
    pipeline = Pipeline(
        translate=FakeTranslator(),
        emit=emit,
        settings=make_settings(),
        direction=EN_TO_KO,
    )
    await pipeline.start()

    await pipeline.handle(TranscriptEvent(phrase_id=0, text="first", is_final=True))
    # Bounded: a dead worker never calls task_done(), so an unguarded loop
    # makes drain() hang rather than fail. wait_for turns that into a fast,
    # legible failure instead of a 30s suite timeout.
    await asyncio.wait_for(pipeline.drain(), timeout=2)

    # The worker must still be alive and must still be consuming the queue.
    assert pipeline._final_worker is not None and not pipeline._final_worker.done()

    await pipeline.handle(TranscriptEvent(phrase_id=1, text="second", is_final=True))
    await asyncio.wait_for(pipeline.drain(), timeout=2)
    await pipeline.aclose()

    assert [(c.phrase_id, c.ko) for c in emit.captions()] == [(1, "[ko]second")]


async def test_transcript_events_fill_the_source_language_slot():
    emitted = []

    async def emit(message):
        emitted.append(message)

    async def translate(text, context):
        raise AssertionError("no translation for a bare interim")

    pipeline = Pipeline(translate, emit, make_settings(), KO_TO_EN)
    await pipeline.start()
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="안녕하세요", is_final=False))
    await pipeline.drain()
    await pipeline.aclose()

    transcript = [m for m in emitted if isinstance(m, TranscriptOut)][0]
    assert transcript.ko == "안녕하세요"
    assert transcript.en is None


async def test_final_captions_place_each_language_in_its_own_slot():
    emitted = []

    async def emit(message):
        emitted.append(message)

    async def translate(text, context):
        return TranslationResult(text="hello there", provider="anthropic", latency_ms=12)

    pipeline = Pipeline(translate, emit, make_settings(), KO_TO_EN)
    await pipeline.start()
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="안녕하세요", is_final=True))
    await pipeline.drain()
    await pipeline.aclose()

    caption = [m for m in emitted if isinstance(m, CaptionOut)][0]
    assert caption.ko == "안녕하세요"   # what was spoken
    assert caption.en == "hello there"  # what was produced
    assert caption.status == "final"


async def test_the_english_to_korean_direction_is_unchanged():
    # Regression guard: the existing direction must behave exactly as before.
    emitted = []

    async def emit(message):
        emitted.append(message)

    async def translate(text, context):
        return TranslationResult(text="안녕하세요", provider="anthropic", latency_ms=12)

    pipeline = Pipeline(translate, emit, make_settings(), EN_TO_KO)
    await pipeline.start()
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="hello there", is_final=True))
    await pipeline.drain()
    await pipeline.aclose()

    caption = [m for m in emitted if isinstance(m, CaptionOut)][0]
    assert caption.en == "hello there"
    assert caption.ko == "안녕하세요"


async def test_rolling_context_stores_each_language_in_its_own_slot():
    # ContextPair(en=, ko=) stays literally true in both directions, which
    # is what lets build_user_prompt render it without knowing how it was
    # produced.
    seen_context = []

    async def emit(message):
        pass

    async def translate(text, context):
        seen_context.append(tuple(context))
        return TranslationResult(text="hello", provider="anthropic", latency_ms=1)

    pipeline = Pipeline(translate, emit, make_settings(), KO_TO_EN)
    await pipeline.start()
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="안녕", is_final=True))
    await pipeline.drain()
    await pipeline.handle(TranscriptEvent(phrase_id=1, text="반가워", is_final=True))
    await pipeline.drain()
    await pipeline.aclose()

    pair = seen_context[-1][0]
    assert pair.ko == "안녕"
    assert pair.en == "hello"


async def test_a_failed_translation_still_leaves_the_source_text_published():
    # The invariant this whole design rests on, stated direction-neutrally:
    # the SOURCE text survives a failed translation. Previously phrased as
    # "the English survives", which is only true one way round.
    emitted = []

    async def emit(message):
        emitted.append(message)

    async def translate(text, context):
        raise RuntimeError("provider exploded")

    pipeline = Pipeline(translate, emit, make_settings(), KO_TO_EN)
    await pipeline.start()
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="안녕하세요", is_final=True))
    await pipeline.drain()
    await pipeline.aclose()

    transcripts = [m for m in emitted if isinstance(m, TranscriptOut)]
    errors = [m for m in emitted if isinstance(m, ErrorOut)]
    assert transcripts[-1].ko == "안녕하세요"
    assert errors and errors[0].phrase_id == 0
    assert not [m for m in emitted if isinstance(m, CaptionOut)]


async def test_the_phrase_span_receives_the_direction_id():
    recorded = {}

    @contextmanager
    def trace_phrase(phrase_id, text, speech_ms, direction_id):
        recorded["direction_id"] = direction_id
        recorded["text"] = text
        yield _NullPhraseSpan()

    async def emit(message):
        pass

    async def translate(text, context):
        return TranslationResult(text="hello", provider="anthropic", latency_ms=1)

    pipeline = Pipeline(
        translate, emit, make_settings(), KO_TO_EN, trace_phrase=trace_phrase
    )
    await pipeline.start()
    await pipeline.handle(TranscriptEvent(phrase_id=0, text="안녕", is_final=True))
    await pipeline.drain()
    await pipeline.aclose()

    assert recorded["direction_id"] == "ko>en"
    # The span's input is the SOURCE text, in both directions.
    assert recorded["text"] == "안녕"
