"""Send one real phrase through the real pipeline and export it to Arize.

Manual, like scripts/verify_deepgram.py: it needs real credentials, real
network, and it spends real money on one translation call. It is not part of
the test suite and must never become part of it.

What it exercises is the actual production path, not a mock of it:
app/tracing.py's setup, the provider instrumentors, app/pipeline.py's finals
worker, and the manual phrase spans. The only thing faked is Deepgram — a
TranscriptEvent is constructed directly instead of spoken into a microphone.

    python scripts/verify_arize.py
    python scripts/verify_arize.py --provider groq --text "Hello there."
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.direction import EN_TO_KO  # noqa: E402
from app.main import _default_router_factory  # noqa: E402
from app.models import TranscriptEvent  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.tracing import phrase_span, setup_tracing, shutdown_tracing  # noqa: E402

DEFAULT_TEXT = "The quick brown fox jumps over the lazy dog."


async def main(text: str, provider: str | None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()

    print("space id :", settings.arize_space_id or "<unset>")
    print("project  :", settings.arize_project_name)
    print("endpoint :", settings.arize_endpoint or "<arize-otel default>")

    if not setup_tracing(settings):
        print("\nFAIL: tracing did not come up. Set ARIZE_SPACE_ID and ARIZE_API_KEY.")
        return 1

    router = _default_router_factory(settings, EN_TO_KO)
    if provider is not None:
        router.set_provider(provider)
    if router.current_provider is None:
        print("\nFAIL: no translation provider is configured.")
        shutdown_tracing()
        return 1
    print("provider :", router.current_provider)

    emitted: list = []

    async def emit(event) -> None:
        emitted.append(event)

    pipeline = Pipeline(
        translate=router.translate,
        emit=emit,
        settings=settings,
        direction=EN_TO_KO,
        trace_phrase=phrase_span,
    )
    await pipeline.start()
    # A phrase is interims followed by one final. Sending both is what makes
    # phrase.speech_ms a real measurement rather than zero.
    await pipeline.handle(
        TranscriptEvent(phrase_id=0, text=text.split(" ", 3)[0], is_final=False)
    )
    await asyncio.sleep(0.4)
    await pipeline.handle(TranscriptEvent(phrase_id=0, text=text, is_final=True))
    await pipeline.drain()
    await pipeline.aclose()

    print("\n--- emitted events ---")
    for event in emitted:
        print(" ", type(event).__name__, event.model_dump())

    # Without this the process exits before the batch exporter's background
    # flush, and nothing ever reaches Arize.
    print("\nflushing spans...")
    shutdown_tracing()

    captions = [e for e in emitted if type(e).__name__ == "CaptionOut"]
    if not captions:
        print("\nFAIL: the translation did not produce a final caption.")
        return 1
    print("\nOK: spans exported. Check the Arize project for a 'phrase' trace.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--provider", default=None, help="anthropic | groq | xai")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.text, args.provider)))
