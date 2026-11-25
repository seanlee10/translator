"""Compare the three translation providers on one audio file.

Manual script — real keys, real network, real money. Not part of the suite.

The measurement only means something if every provider sees the same source
transcript, so the audio is transcribed **once** and the resulting phrase
list is reused. Transcribing per provider would measure Deepgram's
run-to-run variation as much as the models.

Four stages, each cached in the work directory so a later stage can be redone
without paying for the earlier ones again:

    transcribe  ffmpeg -> PCM16 -> DeepgramSource -> phrases.json
    translate   phrases x 3 providers             -> translations.json
    judge       one blind A/B/C comparison/phrase -> judgments.json
    report      merged metrics                    -> results.json

Usage:
    python scripts/benchmark_translation.py --minutes 5
    python scripts/benchmark_translation.py            # whole file
    python scripts/benchmark_translation.py --stage report   # re-render only
"""

import argparse
import asyncio
import json
import logging
import random
import statistics
import sys
import time
import uuid
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.deepgram_client import DeepgramSource  # noqa: E402
from app.direction import DIRECTIONS  # noqa: E402
from app.models import ContextPair  # noqa: E402
from app.providers import build_providers  # noqa: E402
from app.tracing import setup_tracing, shutdown_tracing, tracing_enabled  # noqa: E402
from app.translator import TranslatorRouter  # noqa: E402

logger = logging.getLogger("benchmark")

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # 16-bit mono
CHUNK_BYTES = 3200  # 100 ms, the same order as the browser's worklet chunks
# Real time, and do not "optimize" this.
#
# Pushing a file at the stream faster than real time looks free — endpointing
# is supposed to run off audio timestamps — but it is not. Measured on the
# same two-minute excerpt, phrases recovered:
#
#     1x -> 19    2x -> 11    4x -> 5    8x -> 0
#
# At 8x the stream produced nothing at all until the tail flush. Whatever the
# server-side reason, transcription quality degrades monotonically with push
# rate, so a benchmark run costs one wall-clock minute per audio minute.
DEFAULT_SPEED = 1.0
# How long to keep the stream open after the last chunk, so the trailing
# phrase gets its is_final.
TAIL_WAIT_S = 6.0
# Cap on the replay gap between phrases (see translate_all).
PACE_CAP_S = 5.0

# Who scores the translations. OpenAI by default precisely because it is the
# one option outside the compared set — anthropic, groq and xai all either
# score their own output or their own vendor's, and shuffled A/B/C labels do
# not fix that. Overridable, since an account running out of credit must not
# be able to take the quality half of the benchmark down with it.
JUDGE_PROVIDER = "openai"
JUDGE_CONCURRENCY = 6

JUDGE_SYSTEM_TEMPLATE = """You are evaluating {target_label} translations of \
{source_label} speech captions from a live-captioning system.

You will see one {source_label} line and three candidate {target_label} \
translations, labelled A, B and C. The labels are shuffled and carry no \
information — judge only the text.

Score each candidate 1 to 5, where 5 is best:
- accuracy: does it convey the {source_label} faithfully, without adding, \
dropping, or inventing content?
- fluency: does it read as natural {target_label} rather than translationese?
{script_note}{register_axis}
The {source_label} may be an unfinished fragment. A translation that \
correctly stays a fragment is CORRECT; one that invents an ending is an \
accuracy failure.

Respond with ONLY a JSON object, no prose and no code fence:
{{"A": {{"accuracy": n, "fluency": n{register_field}}},
  "B": {{"accuracy": n, "fluency": n{register_field}}},
  "C": {{"accuracy": n, "fluency": n{register_field}}},
  "rank": ["best label", "middle label", "worst label"],
  "note": "one short sentence naming the deciding difference"}}"""

# The register axis only makes sense on the direction whose target is
# Korean (see Direction.applies_register in app/direction.py) — asking a
# judge to score English output against `korean_register` would itself be
# the kind of meaningless-score bug this parameterization exists to avoid.
_REGISTER_AXIS = "- register: is it consistently in the {register} speech level?\n"
_REGISTER_FIELD = ', "register": n'
# The en>zh audience is in Taiwan: a fluent answer in Simplified characters
# or Mainland vocabulary is still wrong for them, and a judge not told so
# would happily reward it.
_TAIWAN_SCRIPT_NOTE = (
    "  Simplified characters or Mainland-only vocabulary count as fluency "
    "failures: the audience reads Traditional Chinese as used in Taiwan.\n"
)


def build_judge_system(settings, direction) -> str:
    register_axis = (
        _REGISTER_AXIS.format(register=settings.korean_register)
        if direction.applies_register else ""
    )
    register_field = _REGISTER_FIELD if direction.applies_register else ""
    return JUDGE_SYSTEM_TEMPLATE.format(
        source_label=direction.source_label,
        target_label=direction.target_label,
        register_axis=register_axis,
        register_field=register_field,
        script_note=_TAIWAN_SCRIPT_NOTE if direction.target_slot == "zh" else "",
    )


# ---------------------------------------------------------------- transcribe


async def transcribe(settings, audio: Path, seconds: float | None, speed: float,
                     direction):
    """Stream the file through the real Deepgram adapter and keep the finals."""
    trim = ["-t", str(seconds)] if seconds else []
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(audio), *trim,
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", "1", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    source = DeepgramSource(settings, SAMPLE_RATE, direction)
    await source.connect()

    sent = {"bytes": 0}
    phrases: list[dict] = []

    async def collect() -> None:
        async for event in source.events():
            if not event.is_final:
                continue
            phrases.append(
                {
                    "phrase_id": event.phrase_id,
                    "en": event.text,
                    # Approximate: audio pushed when the final surfaced, not a
                    # Deepgram word timestamp. Useful for locating a phrase in
                    # the recording, not for precise alignment.
                    "audio_ms": int(sent["bytes"] / BYTES_PER_SECOND * 1000),
                }
            )
            if len(phrases) % 25 == 0:
                logger.info("  %d phrases", len(phrases))

    collector = asyncio.create_task(collect())
    pace = (CHUNK_BYTES / BYTES_PER_SECOND) / speed

    try:
        while True:
            chunk = await proc.stdout.read(CHUNK_BYTES)
            if not chunk:
                break
            await source.send_audio(chunk)
            sent["bytes"] += len(chunk)
            await asyncio.sleep(pace)
        logger.info("audio sent (%.1fs); waiting for trailing finals",
                    sent["bytes"] / BYTES_PER_SECOND)
        await asyncio.sleep(TAIL_WAIT_S)
    finally:
        await source.aclose()
        collector.cancel()
        try:
            await collector
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        if proc.returncode is None:
            proc.kill()
        await proc.wait()

    return phrases


# ---------------------------------------------------------------- translate


async def translate_all(settings, phrases, run_id, direction, only=None,
                        pace: bool = True, pace_cap: float = PACE_CAP_S):
    """Every provider sees the same source text, each with its own rolling context.

    Per-provider context is deliberate: that is what the live app does, so a
    provider is measured with the history it would actually have had.

    Phrases are replayed on the timeline they were spoken on, and that is
    load-bearing rather than tidy. Firing them back to back sends roughly
    twenty times the live request rate, which is enough to trip a provider's
    rate limit — and a throttled provider looks like a slow model. It already
    happened: one run showed a provider steady at 255 ms for the first half
    and 2406 ms for the second, flipping mid-run and never recovering. Its
    real per-call latency was the fastest of the three.
    """
    providers = build_providers(settings)
    if not providers:
        raise SystemExit("no translation provider is configured")
    names = sorted(providers)
    if only:
        missing = sorted(set(only) - set(names))
        if missing:
            raise SystemExit(f"not configured: {missing}")
        names = [n for n in names if n in only]
    logger.info("providers: %s", ", ".join(names))

    routers = {
        name: TranslatorRouter(providers={name: providers[name]},
                               default=name, settings=settings,
                               direction=direction)
        for name in names
    }
    contexts = {name: deque(maxlen=settings.context_pairs) for name in names}

    tracer = None
    if tracing_enabled():
        from opentelemetry.trace import Status, StatusCode, get_tracer
        tracer = get_tracer("benchmark")

    rows = []
    previous_audio_ms = phrases[0]["audio_ms"] if phrases else 0
    for index, phrase in enumerate(phrases):
        if pace and index:
            # Wait out the gap between this phrase and the last, minus what
            # the previous round already spent. Capped so a long silence in
            # the recording does not stall the run — a pause that long has no
            # bearing on rate limiting anyway.
            gap = (phrase["audio_ms"] - previous_audio_ms) / 1000
            spent = time.monotonic() - round_started
            wait = min(gap, pace_cap) - spent
            if wait > 0:
                await asyncio.sleep(wait)
        previous_audio_ms = phrase["audio_ms"]
        round_started = time.monotonic()
        row = {**phrase, "translations": {}}

        async def one(name: str):
            router, context = routers[name], tuple(contexts[name])
            span_id = None
            started = time.monotonic()
            try:
                if tracer is None:
                    result = await router.translate(phrase["en"], context)
                else:
                    with tracer.start_as_current_span(f"translate.{name}") as sp:
                        span_id = format(sp.get_span_context().span_id, "016x")
                        sp.set_attribute("openinference.span.kind", "CHAIN")
                        sp.set_attribute("input.value", phrase["en"])
                        sp.set_attribute("session.id", run_id)
                        sp.set_attribute("translation.provider", name)
                        sp.set_attribute("phrase.id", phrase["phrase_id"])
                        sp.set_attribute("phrase.direction", direction.id)
                        try:
                            result = await router.translate(phrase["en"], context)
                        except Exception as exc:
                            # Say so explicitly. A span left UNSET reads the
                            # same as a success, so filtering to failures in
                            # Arize would silently return nothing.
                            sp.record_exception(exc)
                            sp.set_status(Status(StatusCode.ERROR, str(exc)))
                            raise
                        sp.set_attribute("output.value", result.text)
                        sp.set_attribute("translation.latency_ms",
                                         result.latency_ms)
                        sp.set_status(Status(StatusCode.OK))
                return name, {
                    "ko": result.text,
                    "latency_ms": result.latency_ms,
                    "span_id": span_id,
                    "error": None,
                }
            except Exception as exc:  # noqa: BLE001 - one provider failing is data
                return name, {
                    "ko": None,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "span_id": span_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }

        if tracer is None:
            done = await asyncio.gather(*(one(n) for n in names))
        else:
            with tracer.start_as_current_span("phrase") as ps:
                ps.set_attribute("openinference.span.kind", "CHAIN")
                ps.set_attribute("input.value", phrase["en"])
                ps.set_attribute("input.audio.transcript", phrase["en"])
                ps.set_attribute("session.id", run_id)
                ps.set_attribute("phrase.id", phrase["phrase_id"])
                ps.set_attribute("phrase.direction", direction.id)
                done = await asyncio.gather(*(one(n) for n in names))
                failed = [n for n, r in done if r["error"]]
                if failed:
                    ps.set_status(
                        Status(StatusCode.ERROR, f"failed: {','.join(failed)}")
                    )
                else:
                    ps.set_status(Status(StatusCode.OK))

        for name, payload in done:
            row["translations"][name] = payload
            # results.json's "en"/"ko" keys are ROLE keys (spoken /
            # translated), kept as-is so older runs stay readable by
            # benchmark_dashboard.py. ContextPair is role-based too, so no
            # language mapping is needed here.
            if payload["ko"]:
                contexts[name].append(
                    ContextPair(source=phrase["en"], target=payload["ko"])
                )
        rows.append(row)
        if (index + 1) % 20 == 0:
            logger.info("  translated %d/%d phrases", index + 1, len(phrases))
    return rows


# -------------------------------------------------------------------- judge


def _message_text(message) -> str:
    """The first text block, not content[0].

    The judge model thinks by default, so content[0] is a ThinkingBlock and
    has no .text at all. Reaching for it raises AttributeError and every
    judgment silently becomes a retry-then-give-up.
    """
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


def _parse_judgment(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


def build_judge(settings, provider: str, model: str | None):
    """Return (call, model_id) for the chosen judge provider.

    The judge is deliberately pluggable rather than pinned to one vendor: an
    account that runs out of credit takes the whole quality half of the
    benchmark down with it, and the run should not have to be abandoned over
    that.

    `openai` is the one option outside the compared set, and it is the right
    default when its key is configured. Every other choice scores either its
    own output or its own vendor's, which is not an impartial ranking no
    matter how the candidates are shuffled.
    """
    if provider == "openai":
        if not settings.openai_api_key.strip():
            raise SystemExit("judging with openai needs OPENAI_API_KEY")
        from openai import AsyncOpenAI

        model = model or settings.openai_judge_model
        client = AsyncOpenAI(api_key=settings.openai_api_key)

        async def call(system: str, prompt: str) -> str:
            completion = await client.chat.completions.create(
                model=model,
                # max_tokens is rejected by this generation; the reasoning
                # models also need headroom beyond the visible answer, since
                # reasoning tokens are billed against the same ceiling.
                max_completion_tokens=4000,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
            )
            return completion.choices[0].message.content or ""

        return call, model

    providers = build_providers(settings)
    if provider not in providers:
        raise SystemExit(
            f"judge provider {provider!r} is not configured; "
            f"available: {sorted(providers)} (+ openai for judging)"
        )

    if provider == "anthropic":
        from anthropic import AsyncAnthropic

        model = model or "claude-opus-5"
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)

        async def call(system: str, prompt: str) -> str:
            message = await client.messages.create(
                model=model, max_tokens=700, system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return _message_text(message)

        return call, model

    from openai import AsyncOpenAI

    if provider == "xai":
        model = model or settings.xai_model
        client = AsyncOpenAI(api_key=settings.xai_api_key,
                             base_url=settings.xai_base_url)
        extra = {}
    else:  # groq
        model = model or settings.groq_model
        client = AsyncOpenAI(api_key=settings.groq_api_key,
                             base_url=f"{settings.groq_base_url}/openai/v1")
        # Qwen is a reasoning model; without these every judgment pays a
        # hidden reasoning pass. Same constraint as the translation path.
        extra = {"reasoning_effort": "none", "reasoning_format": "hidden"}

    async def call(system: str, prompt: str) -> str:
        completion = await client.chat.completions.create(
            model=model, max_completion_tokens=900,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            **extra,
        )
        return completion.choices[0].message.content or ""

    return call, model


async def judge_all(settings, rows, provider: str, model: str | None, direction):
    """One blind three-way comparison per phrase.

    Labels are shuffled per phrase and the provider names are never shown, so
    the judge cannot favour a model by reputation. Scoring all three in one
    call also makes the comparison relative, which is far more stable than
    three independent absolute scores.
    """
    call, model = build_judge(settings, provider, model)
    logger.info("judge: %s (%s)", provider, model)
    system = build_judge_system(settings, direction)
    gate = asyncio.Semaphore(JUDGE_CONCURRENCY)
    labels = ["A", "B", "C"]

    async def judge(index: int, row: dict):
        usable = {n: t["ko"] for n, t in row["translations"].items() if t["ko"]}
        if len(usable) < 2:
            return index, None
        names = sorted(usable)
        # Seeded on the phrase index so a re-run reproduces the same shuffle.
        random.Random(index).shuffle(names)
        mapping = dict(zip(labels, names))
        candidates = "\n".join(f"{lab}: {usable[mapping[lab]]}"
                               for lab in labels[: len(names)])
        prompt = (
            f"{direction.source_label}:\n{row['en']}\n\nCandidates:\n{candidates}"
        )

        async with gate:
            for attempt in range(2):
                try:
                    verdict = _parse_judgment(await call(system, prompt))
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 1:
                        logger.warning("judge failed on phrase %d: %s", index, exc)
                        return index, None
                    await asyncio.sleep(1.0)

        scores = {}
        for label, name in mapping.items():
            got = verdict.get(label) or {}
            scores[name] = {
                axis: got.get(axis) for axis in ("accuracy", "fluency", "register")
            }
        ranking = [mapping[lab] for lab in verdict.get("rank", []) if lab in mapping]
        return index, {"scores": scores, "rank": ranking,
                       "note": verdict.get("note", ""), "labels": mapping}

    results = await asyncio.gather(*(judge(i, r) for i, r in enumerate(rows)))
    for index, verdict in results:
        rows[index]["judgment"] = verdict
    return rows


# ------------------------------------------------------------------- report


def summarize(rows, providers):
    def pct(values, p):
        if not values:
            return None
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(round(p / 100 * len(ordered))) )]

    summary = {}
    for name in providers:
        lat = [r["translations"][name]["latency_ms"]
               for r in rows
               if name in r["translations"] and r["translations"][name]["ko"]]
        fails = sum(1 for r in rows
                    if name in r["translations"] and r["translations"][name]["error"])
        axes = {"accuracy": [], "fluency": [], "register": []}
        wins = 0
        judged = 0
        for row in rows:
            verdict = row.get("judgment")
            if not verdict or name not in verdict["scores"]:
                continue
            judged += 1
            for axis, bucket in axes.items():
                value = verdict["scores"][name].get(axis)
                if isinstance(value, (int, float)):
                    bucket.append(value)
            if verdict["rank"] and verdict["rank"][0] == name:
                wins += 1
        summary[name] = {
            "n": len(lat),
            "failures": fails,
            "latency_ms": {
                "mean": round(statistics.fmean(lat), 1) if lat else None,
                "p50": pct(lat, 50), "p95": pct(lat, 95),
                "min": min(lat) if lat else None, "max": max(lat) if lat else None,
            },
            "quality": {
                axis: round(statistics.fmean(v), 2) if v else None
                for axis, v in axes.items()
            },
            "wins": wins,
            "judged": judged,
            "win_rate": round(wins / judged, 3) if judged else None,
        }
        overall = [v for axis in axes.values() for v in axis]
        summary[name]["quality"]["overall"] = (
            round(statistics.fmean(overall), 2) if overall else None
        )
    return summary


def arize_annotations(rows):
    """Judge scores keyed by span id, ready for `ax spans annotate`.

    Written to a file rather than uploaded here on purpose: uploading needs
    the `ax` CLI pointed at the right profile, and switching the active
    profile is a mutation of the user's environment that a benchmark script
    has no business making silently.

    Uploading it takes three steps, in this order, none of them optional:

    1. Create the annotation configs once per space, or every upload fails
       with `no annotation config with name 'translation_accuracy' exists`:

           ax annotation-configs create -n translation_accuracy -s <SPACE> \
              -t CONTINUOUS --min-score 1 --max-score 5 \
              --optimization-direction MAXIMIZE

       Repeat for translation_fluency and translation_register; use
       translation_rank with 1..3 and MINIMIZE.

    2. Filter to span ids Arize actually holds. The API rejects the *entire*
       batch if one id is missing from the lookup window. Get the real set
       with `ax spans export <PROJECT> -s <SPACE> --all` — plain export caps
       at 100 rows and silently looks like a 96% miss rate. Note the bulk
       export uses a flat schema: the id is `context.span_id`, not a nested
       `context` object.

    3. Send in batches of at most 1000:

           ax spans annotate <PROJECT> -s <SPACE> -f batch.json --days 1
    """
    records = []
    for row in rows:
        verdict = row.get("judgment")
        if not verdict:
            continue
        for name, scores in verdict["scores"].items():
            span_id = row["translations"].get(name, {}).get("span_id")
            if not span_id:
                continue
            values = [
                {"name": f"translation_{axis}", "score": value}
                for axis, value in scores.items()
                if isinstance(value, (int, float))
            ]
            if verdict["rank"]:
                place = (
                    verdict["rank"].index(name) + 1
                    if name in verdict["rank"]
                    else None
                )
                if place is not None:
                    values.append(
                        {
                            "name": "translation_rank",
                            "score": place,
                            "label": "win" if place == 1 else "loss",
                        }
                    )
            if values:
                records.append({"record_id": span_id, "values": values})
    return records



def publish_quality(settings, rows, run_id: str) -> int:
    """Publish judged results to Arize as ordinary span attributes.

    This is what makes the Arize dashboard stand on its own. Latency and cost
    already arrive natively from the live app's tracing; quality did not, and
    backfilling it by hand after every run makes the dashboard a snapshot
    somebody has to maintain rather than something a run keeps current.

    Deliberately NOT Arize's evaluation machinery. That path (dataset ->
    experiment -> eval task) mixed outputs between experiments: judged
    explanations quoted Korean that belonged to a different model, 87% of the
    time for one provider, which inverted the ranking. Plain numeric span
    attributes become spanProperty dimensions — the same mechanism
    translation.latency_ms already uses — and widgets read them directly.

    A separate project, because these spans carry the same provider and
    latency attributes as live ones and would otherwise skew every live
    latency widget that filters on provider.
    """
    space = settings.arize_space_id.strip()
    key = settings.arize_api_key.strip()
    if not (space and key):
        logger.info("skipping quality publish: Arize credentials are not set")
        return 0

    from arize.otel import register
    from opentelemetry.trace import Status, StatusCode

    kwargs = {
        "space_id": space,
        "api_key": key,
        "project_name": settings.arize_quality_project,
        # Never take over the global provider: the benchmark's own tracing
        # (app/tracing.py) owns it and points at the live project.
        "set_global_tracer_provider": False,
    }
    if settings.arize_endpoint.strip():
        kwargs["endpoint"] = settings.arize_endpoint.strip()
    provider = register(**kwargs)
    tracer = provider.get_tracer("benchmark-quality")

    published = 0
    for row in rows:
        verdict = row.get("judgment") or {}
        scores = verdict.get("scores") or {}
        ranking = verdict.get("rank") or []
        for name, t in row["translations"].items():
            if not t.get("ko"):
                continue
            axes = scores.get(name) or {}
            with tracer.start_as_current_span("translation") as span:
                span.set_attribute("openinference.span.kind", "CHAIN")
                span.set_attribute("input.value", row["en"])
                span.set_attribute("output.value", t["ko"])
                span.set_attribute("provider", name)
                span.set_attribute("model_id", getattr(settings, f"{name}_model", name))
                span.set_attribute("latency_ms", t["latency_ms"])
                span.set_attribute("phrase_id", row["phrase_id"])
                span.set_attribute("session.id", run_id)
                usable = []
                for axis in ("accuracy", "fluency", "register"):
                    value = axes.get(axis)
                    if isinstance(value, (int, float)):
                        span.set_attribute(f"quality_{axis}", float(value))
                        usable.append(float(value))
                if usable:
                    span.set_attribute("quality_overall", sum(usable) / len(usable))
                if ranking:
                    span.set_attribute(
                        "rank", ranking.index(name) + 1 if name in ranking else 0
                    )
                    span.set_attribute("is_winner", 1.0 if ranking[0] == name else 0.0)
                span.set_status(Status(StatusCode.OK))
                published += 1

    # Without the flush the process exits before the batch exporter runs and
    # nothing reaches Arize.
    provider.force_flush()
    provider.shutdown()
    logger.info("published %d quality spans to project %s",
                published, settings.arize_quality_project)
    return published


# --------------------------------------------------------------------- main


async def main(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    direction = DIRECTIONS[args.direction]
    work = Path(args.out)
    work.mkdir(parents=True, exist_ok=True)

    # Transcribe once, translate many — but a cached phrase list is only
    # valid for the language it was transcribed in, so the cache filename
    # must include the direction. Reusing an English phrase list for a
    # Korean run would silently benchmark the wrong thing.
    phrases_path = work / f"phrases-{direction.id.replace('>', '-')}.json"
    translations_path = work / "translations.json"
    results_path = work / "results.json"

    stages = ["transcribe", "translate", "judge", "report"]
    start_at = stages.index(args.stage) if args.stage else 0

    if start_at <= 0:
        if phrases_path.exists() and not args.force:
            logger.info("%s exists; reusing it (--force to redo)", phrases_path)
        else:
            logger.info("transcribing %s (%s)", args.audio, direction.id)
            phrases = await transcribe(
                settings, Path(args.audio),
                args.minutes * 60 if args.minutes else None, args.speed,
                direction,
            )
            phrases_path.write_text(
                json.dumps(phrases, ensure_ascii=False, indent=2)
            )
            logger.info("  %d phrases -> %s", len(phrases), phrases_path)

    phrases = json.loads(phrases_path.read_text())
    run_id = args.run_id or f"bench-{uuid.uuid4().hex[:8]}"

    if start_at <= 1:
        if not setup_tracing(settings):
            logger.warning("tracing is off; Arize will get nothing")
        logger.info("translating %d phrases (run_id=%s)", len(phrases), run_id)
        only = [p.strip() for p in args.providers.split(",")] if args.providers else None
        rows = await translate_all(settings, phrases, run_id, direction, only=only,
                                   pace=not args.no_pace, pace_cap=args.pace_cap)
        translations_path.write_text(
            json.dumps({"run_id": run_id, "rows": rows},
                       ensure_ascii=False, indent=2)
        )
        shutdown_tracing()
        logger.info("  -> %s", translations_path)

    payload = json.loads(translations_path.read_text())
    rows, run_id = payload["rows"], payload["run_id"]

    if start_at <= 2:
        logger.info("judging %d phrases", len(rows))
        rows = await judge_all(settings, rows, args.judge_provider,
                               args.judge_model, direction)
        translations_path.write_text(
            json.dumps({"run_id": run_id, "rows": rows},
                       ensure_ascii=False, indent=2)
        )

    providers = sorted({n for r in rows for n in r["translations"]})
    results = {
        "run_id": run_id,
        "direction": direction.id,
        "audio": str(args.audio),
        "minutes": args.minutes,
        "judge_provider": args.judge_provider,
        "judge_model": args.judge_model
        or (settings.openai_judge_model if args.judge_provider == "openai"
            else getattr(settings, f"{args.judge_provider}_model", None)),
        "korean_register": settings.korean_register,
        "models": {n: getattr(settings, f"{n}_model", n) for n in providers},
        "phrases": len(rows),
        "summary": summarize(rows, providers),
        "rows": rows,
    }
    results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))

    if not args.no_publish:
        try:
            publish_quality(settings, rows, run_id)
        except Exception:  # noqa: BLE001 - publishing must not lose the run
            logger.exception("quality publish failed; results.json is unaffected")

    annotations = arize_annotations(rows)
    if annotations:
        (work / "arize_annotations.json").write_text(
            json.dumps(annotations, ensure_ascii=False, indent=2)
        )
        logger.info("%d Arize annotations -> %s",
                    len(annotations), work / "arize_annotations.json")

    print(f"\nrun_id: {run_id}   phrases: {len(rows)}")
    print(f"{'provider':10} {'p50':>7} {'p95':>7} {'quality':>8} {'win%':>6} {'fail':>5}")
    for name, s in results["summary"].items():
        q = s["quality"]["overall"]
        w = s["win_rate"]
        print(f"{name:10} {str(s['latency_ms']['p50']):>7} "
              f"{str(s['latency_ms']['p95']):>7} {str(q):>8} "
              f"{('%.0f' % (w * 100)) if w is not None else '-':>6} "
              f"{s['failures']:>5}")
    print(f"\nwrote {results_path}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--audio", default=str(
        Path.home()
        / "Downloads"
        / "Conan O'Brien Delivers Dartmouth's Commencement Address.mp3"
    ))
    p.add_argument("--minutes", type=float, default=None,
                   help="transcribe only the first N minutes")
    p.add_argument("--speed", type=float, default=DEFAULT_SPEED)
    p.add_argument(
        "--direction",
        choices=sorted(DIRECTIONS),
        default="en>ko",
        help="Translation direction. Also selects the Deepgram language.",
    )
    p.add_argument("--out", default="benchmarks/run")
    p.add_argument("--stage", choices=["transcribe", "translate", "judge", "report"])
    p.add_argument("--judge-provider", default=JUDGE_PROVIDER,
                   choices=["openai", "anthropic", "groq", "xai"])
    p.add_argument("--judge-model", default=None)
    p.add_argument("--run-id", default=None)
    p.add_argument("--providers", default=None,
                   help="comma-separated subset, e.g. groq,xai")
    p.add_argument("--no-pace", action="store_true",
                   help="fire phrases back to back; risks provider rate limits")
    p.add_argument("--pace-cap", type=float, default=PACE_CAP_S)
    p.add_argument("--no-publish", action="store_true",
                   help="skip publishing judged results to Arize")
    p.add_argument("--force", action="store_true")
    raise SystemExit(asyncio.run(main(p.parse_args())))
