import asyncio

from pydantic import BaseModel

from app.config import Settings
from app.models import CaptionOut, ErrorOut, TranscriptEvent, TranslationResult
from app.pipeline import Pipeline


def make_settings(**overrides) -> Settings:
    base = dict(deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g")
    base.update(overrides)
    return Settings(**base)


class FakeTranslator:
    """Records calls and returns a deterministic result."""

    def __init__(self, provider="anthropic"):
        self.provider = provider
        self.calls: list[tuple[str, list[tuple[str, str]]]] = []

    async def __call__(self, text, context):
        self.calls.append((text, [(c.en, c.ko) for c in context]))
        return TranslationResult(
            ko=f"[ko]{text}", provider=self.provider, latency_ms=100
        )


class Recorder:
    def __init__(self):
        self.events: list[BaseModel] = []

    async def __call__(self, event):
        self.events.append(event)

    def captions(self) -> list[CaptionOut]:
        return [e for e in self.events if isinstance(e, CaptionOut)]


async def run(events, settings=None, translator=None):
    settings = settings or make_settings()
    translator = translator or FakeTranslator()
    recorder = Recorder()
    pipeline = Pipeline(translate=translator, emit=recorder, settings=settings)
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
                ko=f"[ko]{text}", provider="anthropic", latency_ms=1
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
                ko=f"[ko]{text}", provider="anthropic", latency_ms=1
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


async def test_a_failed_phrase_does_not_enter_the_context_window():
    class OnlyGood:
        async def __call__(self, text, context):
            if text == "bad":
                raise RuntimeError("boom")
            return TranslationResult(
                ko=f"[ko]{text}", provider="anthropic", latency_ms=1
            )

    recorder, _ = await run(
        [
            TranscriptEvent(phrase_id=0, text="bad", is_final=True),
            TranscriptEvent(phrase_id=1, text="good", is_final=True),
        ],
        translator=OnlyGood(),
    )

    assert [(c.phrase_id, c.ko) for c in recorder.captions()] == [(1, "[ko]good")]
