# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Live caption translator: microphone → Deepgram streaming STT → Claude Haiku 5.5, Qwen3.6-27B, or Grok 4.20 non-reasoning (switchable mid-session) → side-by-side captions. Direction (English→Korean, Korean→English, or English→Traditional Chinese for Taiwan) is chosen before each session and fixed for its duration. See `README.md` for setup and the `backend/.env` tuning table.

## Commands

```bash
# Backend (from backend/)
pip install -e ".[dev]"
uvicorn app.main:app --reload          # :8000
python -m pytest -v                     # 205 tests
python -m pytest tests/test_pipeline.py::test_a_late_interim_never_overwrites_a_finalized_phrase -v

# Frontend (from frontend/)
npm install
npm run dev                             # :5173
npx tsc --build --noEmit --force        # type-check
npm run build
```

**The type-check MUST use `--build`.** `tsconfig.json` is solution-style (`"files": []` + `references`), so plain `npx tsc --noEmit` checks zero files and exits `0` even with type errors present. Any claim that the frontend "type-checks clean" based on the plain form is worthless.

The backend suite runs with no network and no API keys — tests construct `Settings` in-process with placeholder strings **and `_env_file=None`**, and inject fakes. Keep it that way: a test that needs a key or a socket is a broken test, and a helper that drops `_env_file=None` lets a developer's real `.env` change the defaults under the suite. A 30s per-test timeout is configured in `backend/pyproject.toml`.

`config.py` resolves its env files **relative to its own source file**, never the process CWD: `ENV_FILES = (<repo>/.env, backend/.env)`, later wins. A bare `env_file=".env"` resolves against the CWD, and since the server is documented as running from `backend/`, that silently found nothing and every session opened with a pydantic validation error. `backend/.env` is the documented location; the repo-root path stays accepted so neither layout breaks.

## Architecture

One WebSocket (`/ws/session`) carries everything: **binary frames are PCM16 audio going up, JSON frames are control messages up and caption events down.**

```
Browser: mic → AudioWorklet (Float32→Int16, 1024-sample chunks)
   ↓ ws
FastAPI (main.py) ── audio ──→ Deepgram SDK (deepgram_client.py)
                                      ↓ TranscriptEvent
                               pipeline.py  ── debounce policy ──→ translator.py → providers/
                                      ↓ CaptionEvent
   ↑ ws ────────────────────────────────┘
```

Audio is proxied **through** the backend rather than browser→Deepgram directly, so the Deepgram key never reaches the browser and all timing logic lives in one process.

### The phrase model

Deepgram does not provide phrase IDs. `PhraseTracker` assigns them: **a phrase is every interim result up to and including one `is_final` result with non-empty text.** The counter increments *only* when a final is emitted — empty finals arrive during silence, and incrementing on those would strand the open phrase and freeze stale Korean on screen forever.

Each phrase produces: several `transcript` events (English only, free) — **including one for the `is_final` result itself** — zero or more `caption` events with `status:"provisional"`, then one `caption` with `status:"final"` that supersedes them.

The final's `transcript` event is not redundant. It is what guarantees a row exists with the correct **source text** even when the translation fails, which the spec's Error Handling section requires. `en`, `ko` and `zh` on the wire are **language** slots, not source/target roles — `en` always holds English, `ko` always Korean and `zh` always Chinese, whichever was spoken; a caption fills exactly the direction's two and leaves the third `null` (`CaptionOut`'s validator enforces exactly two) — and `Direction.to_slots` (`app/direction.py`) is the single place that mapping is decided. The client ranks `transcript` as `interim`, so it refines the source text without regressing status or blanking the translation.

**The frontend now reveals a row only once its phrase is terminal** (`status:"final"`, or a phrase-scoped `error` — see the Frontend section below), never on `transcript`/`interim` or `caption`/`provisional` alone. Those events still arrive and still update internal state (and drive the in-progress loader), but are never rendered as changing text — that's the point of this design: the backend's wire protocol (interim → zero-or-more provisional → final) is unchanged, only what the client chooses to draw from it changed.

### `pipeline.py` — the only stateful component

It holds every timing and ordering decision, and **imports no I/O library** (no `anthropic`, `groq`, `deepgram`, `fastapi`). It receives a translate callable and an emit callable. That constraint is what makes the whole policy testable offline — do not break it.

Four invariants, each protecting something specific:

- **Finals go through a single serialized `asyncio.Queue` worker.** Translations complete out of order; this is what keeps the rolling context in phrase order. Never translate finals with `create_task`.
- **Interims are fire-and-forget with an in-flight guard** (`state.in_flight`): if a translation is already running for a phrase, the next interim is **skipped, never queued**. This makes the system self-limiting — a slow provider degrades to finals-only instead of growing a backlog. `settings.translate_interims` (`TRANSLATE_INTERIMS`) defaults to **`false`**: the frontend's terminal-only reveal never renders a provisional caption in the main view, so translating interims by default was pure wasted API spend. The debounce/in-flight/sequence-guard machinery itself is untouched and fully covered by tests with the flag explicitly on — it's still correct, just off by default. Toggle it via env or the `config` message's `translate_interims` field (the backend still handles that field; only the frontend's UI toggle for it was removed — see Frontend below).
- **Sequence guard**: `_translate_interim` re-checks `state.closed` *inside* `_emit_lock` before emitting, so a slow provisional can never land on top of the final that superseded it.
- **Debounce has no timer.** It is evaluated when each interim arrives (has `debounce_ms` elapsed since this phrase's last translation, or since it first appeared). Deterministic under an injected clock; correct in practice because if interims stop, the final follows within the endpointing window.

`handle()` must never block on a translation — its caller is the live transcript loop.

**There are two different locks; they do different jobs.** `pipeline._emit_lock` sequences provisional-vs-final captions. `main.py`'s per-session `send_lock` serializes *all* `ws.send_json` calls, because three tasks (session handler, pump, finals worker) write to the same socket and Starlette does not document that as safe.

### Translation: shared prompt, thin providers

`translator.py` owns the prompt, the output cleaning, and the latency timing. A provider module owns **only its SDK call**. This is not stylistic: the app exists to compare models, and a test — parameterized over `PROVIDER_REGISTRY` so a newly registered provider is covered automatically — asserts all configured providers receive byte-identical prompts. Never move prompt construction into a provider.

Three API constraints that will produce confusing failures if "cleaned up":

- **Anthropic**: `claude-haiku-5-5` (default) thinks by default, so the provider sends `thinking={"type":"disabled"}` with `output_config={"effort":"low"}` — `disabled` is only accepted at effort `high` or below. It rejects non-default `temperature`/`top_p`/`top_k` and assistant prefill, so never send those. These two parameters go **only** to `claude-haiku-5*` ids (an allowlist): other Claude models disagree about them — `claude-haiku-4-5` and Sonnet 4.5 reject `effort`, Opus 5.5 and Sonnet 5.5 reject `thinking: disabled` — so any other `ANTHROPIC_MODEL` gets a plain `messages.create` with `model`/`max_tokens`/`system`/`messages` only.
- **Groq**: `qwen/qwen3.6-27b` is a reasoning model. `reasoning_effort="none"` and `reasoning_format="hidden"` are **mandatory**, not tunable — without them every caption pays a hidden reasoning pass and the latency budget is gone. `clean_output` also strips `<think>` blocks as a second line of defense.
- **xAI**: the default `grok-4.20-0309-non-reasoning` has no reasoning mode, so the provider sends it nothing reasoning-related. It stays the default because on en>zh it beat grok-4.3-with-reasoning-off on latency (p50 820 vs 909 ms) at no quality cost. `grok-4.3` (via `XAI_MODEL`) reasons by default — measured ~8 s and ~600 reasoning tokens for one caption line — so `reasoning_effort="none"` is **mandatory**, as for Groq/Qwen. It goes **only** to `grok-4.3*` ids (an allowlist); every other id, including grok-4.5/4.6/4.7, gets the plain request rather than a guessed parameter. xAI now serves the retired `grok-4-*-fast-non-reasoning` ids with grok-4.3 behind the scenes, so those are not a faster option; grok-4.5/4.6/4.7 cannot disable reasoning at all. `reasoning_format` is Groq-only and never sent to xAI. It is OpenAI-compatible (`https://api.x.ai/v1`, `/chat/completions`) and uses the `openai` package's `AsyncOpenAI` client rather than the `groq` SDK with a swapped base URL, so the two services can diverge without silently breaking each other.

### Direction: `app/direction.py`

`DIRECTIONS` declares the three directions (`en>ko`, `ko>en`, `en>zh`) the same way `PROVIDER_REGISTRY` declares the providers — one place, everything derives. A `Direction` carries the Deepgram language, its source/target wire slots, the prompt's source/target labels, whether `korean_register` applies, an optional extra `target_rule` for the system prompt, and `to_slots`.

- **`en>zh` is Traditional Chinese as used in Taiwan, not generic Chinese.** Its `target_rule` forbids Simplified characters and Mainland vocabulary; the benchmark judge is told the same, and the frontend tags the text `lang="zh-Hant-TW"` so the browser picks a TC glyph set. There is deliberately no `zh>en` — no direction transcribes Chinese.
- **`ContextPair` is role-based (`source`, `target`)**, unlike the wire. It never leaves the process, and `build_user_prompt` labels each side `EN:`/`KO:`/`ZH:` from the direction's slots.
- **Benchmark `results.json` row keys `"en"`/`"ko"` are role keys** (spoken / translated), not languages — in a `ko>en` or `en>zh` run `"ko"` holds the translation. Kept that way so old runs still render; don't "fix" it without migrating the dashboard.
- **The module imports no I/O library, and that is load-bearing.** `pipeline.py` imports it, and pipeline.py's offline testability rests on depending on no SDK.
- **Direction is session-scoped.** It rides in the `start` message and cannot be changed by `config` — Deepgram's `language` is fixed at connect time and this app has no reconnect path. The `config` handler rejects it out loud rather than ignoring it.
- **An unknown direction never costs the user their session.** `resolve_direction` returns the fallback plus a message, which `main.py` emits as a non-fatal `ErrorOut` — the same posture `_default_router_factory` takes for an unconfigured `DEFAULT_PROVIDER`.
- `korean_register` applies only when Korean is the target. English and Chinese get no tone knob.

### Provider discovery: the backend is the single source of truth

`app/providers/__init__.py`'s `PROVIDER_REGISTRY` is the one place the provider set is declared — id, label, the `Settings` attribute holding its model id, the attribute holding its API key, and how to build its client. `build_providers`, `GET /providers`, and `configured_providers()` all derive from it; nothing else hardcodes the trio.

- **Provider keys are optional; `deepgram_api_key` is not.** `anthropic_api_key`, `groq_api_key`, and `xai_api_key` default to `""` in `config.py`. `deepgram_api_key` stays required — it's the transcription itself, not a translation provider. A server with zero translation keys set still starts and still transcribes; it just can't translate.
- **`build_providers` only constructs clients for configured providers** (non-empty key). An unset key never produces a client that would fail at call time — it simply isn't offered.
- **`GET /providers`** returns `[{"id", "label", "model"}, ...]` for the configured subset only (a pydantic `ProviderInfo` model, per-provider `model` read from its `model_attr`). The frontend fetches this on mount and builds its model selector from it — there is no hardcoded provider list left anywhere in the frontend.
- **`DEFAULT_PROVIDER` pointing at an unconfigured provider never fails startup.** `main.py`'s `_default_router_factory` falls back to the first configured provider (registry order) and logs a warning. If *no* provider is configured, the session still opens with `TranslatorRouter.current_provider is None`; `translate()` then raises `RuntimeError("no translation provider is configured")`, which the pipeline's per-phrase failure isolation (`_run_final_worker`'s `try/except` around `_translate_final`) turns into a non-fatal `ErrorOut` — never a dead session.
- `ProviderName` is the full set of *known* provider ids — a type-level concern for the wire schema. Whether one is actually *available* is a runtime concern that belongs to the registry, not the type. It is defined once in `models.py` and imported into `config.py`; keep it that way — a duplicated literal means adding a fourth provider requires three edits instead of two, and if only one copy is updated the failure is silent (`build_providers` builds a client for the new id, but `CaptionOut(provider=<new id>)` raises a `ValidationError` that the pipeline's per-phrase error handling swallows).

### Deepgram adapter

- **Every `connect()` parameter must be a `str`** — the SDK types them all `Optional[str]`, so `True`/`16000` fail confusingly at request time. Pass `"true"`, `"16000"`. A test asserts this for all values.
- **Normalization goes `message_to_dict()` → `PhraseTracker`**, never through typed SDK attribute access. The wire JSON shape is documented and stable; the Fern-generated attribute names are not. This keeps one normalization point that survives SDK regeneration.
- `events()` **raises** `DeepgramStreamError` when the stream dies, rather than ending silently — `_pump` in `main.py` catches it and surfaces a fatal error. A *clean* end (Deepgram's own code-1000 idle/rotation close) makes `events()` return instead; `_pump` then emits `StatusOut(state="closed")` plus a non-fatal `ErrorOut`, and `main.py` stops forwarding audio once `pump.done()` so the user never sees a raw `sent 1000 (OK)` library string.
- **Keepalive is ours, not the SDK's.** `deepgram-sdk` 7.x has no automatic keepalive — only a manual `send_keep_alive()`. `DeepgramSource` runs a 5s ticker (`KEEPALIVE_INTERVAL_S`) started in `connect()` and cancelled in `aclose()`. Without it, extended silence ends the stream mid-session. The design doc used to claim the SDK handled this; it does not.
- `sample_rate` comes from the **browser per session**, not from `settings`. Browsers do not reliably honor a requested `AudioContext` rate, so the real one is sent in the `start` message and used to configure Deepgram. `settings.sample_rate` is only what the browser is *asked* for.
- `backend/scripts/verify_deepgram.py` is a manual script needing a real key and network. It is not part of the suite; run it by hand when touching the SDK adapter.

### Arize AX tracing (`app/tracing.py`)

Additive and optional. `setup_tracing()` returns `False` and logs — never raises — unless **both** `ARIZE_SPACE_ID` and `ARIZE_API_KEY` are non-empty after stripping. A server with neither behaves exactly as it did before: no exporter, no network, no cost.

- **`pipeline.py` still imports no I/O library**, and that is not incidental. Tracing is *injected* as a `trace_phrase` context manager alongside the existing `translate`/`emit`/`now` seams, defaulting to `null_trace_phrase` (pure `contextlib`). An OTLP exporter imported into the policy module is exactly the dependency that would end its offline testability. Do not "simplify" this by importing `app.tracing` from `pipeline.py`.
- **One span tree per finalized phrase**, opened in the finals worker so the provider SDK's own LLM span nests inside it (the translation is awaited inside the `with`, same task, so the OTel context propagates):

  ```
  phrase          [CHAIN]  input=EN, output=KO, phrase.id, phrase.speech_ms
  ├─ transcription [CHAIN]  input.audio.transcript=EN
  └─ messages.create [LLM]  ← the instrumentor's, with tokens and cost
  ```

- **The transcription span is a `CHAIN`, not an `AUDIO`, and that is a platform limit rather than a preference.** Arize's voice docs describe an `AUDIO` span kind; it is not in `OpenInferenceSpanKindValues` (semconv 0.1.32), and worse, Arize's *read* API rejects the value. Spans written with `openinference.span.kind="AUDIO"` ingest silently and then make every later `ax traces export` **and** unfiltered `ax spans export` over that project fail with `422 Validation Error; kind: Input should be 'LLM', 'CHAIN', ...`. One unreadable span poisons trace export for the whole project, and nothing at write time warns you. `SUPPORTED_SPAN_KINDS` plus a test pins this. **Consequence: there is no voice-agent span surface here.** The app also emits no audio and retains no input audio, so `output.audio.*`, `input.audio.url`, and time-to-first-token cannot exist on any path — `phrase.speech_ms` (first interim → final) is the only honest duration available.
- **All three SDK instrumentors are attached regardless of which keys are configured.** Patching a library nobody calls costs nothing; tying this to `configured_providers()` would mean a provider enabled later silently loses its LLM spans.
- **The space id is instance-scoped.** `app.arize.com` (US, collector `otlp.arize.com`) and `app.eu-west-1a.arize.com` (EU) have disjoint space id ranges. Credentials from one instance pointed at the other's collector drop every span with no error. `ARIZE_ENDPOINT` stays blank to mean "arize-otel's default", which is US. `ax spaces list` defaults to `--limit 15`, so a missing space there is not evidence the space does not exist.
- `backend/scripts/verify_arize.py` is a manual script — real keys, real network, one real translation call. It drives the production path end to end (only Deepgram is faked, by constructing a `TranscriptEvent`) and calls `shutdown_tracing()` before exit, without which the batch exporter never flushes and nothing arrives.

### Benchmarking the three models (`scripts/benchmark_*.py`)

`benchmark_translation.py` runs an audio file through the real Deepgram adapter once, replays the resulting phrases past every configured provider, scores them blind, and writes `results.json`. `benchmark_dashboard.py` renders that into a self-contained HTML page. Both are manual — real keys, real network, real spend — and neither is in the suite. Outputs land in `backend/benchmarks/`, which is git-ignored: a run's `phrases.json` is a verbatim transcript of whatever was measured, and this repo is public.

Four things in there are load-bearing and were each learned the hard way:

- **Transcribe once, translate many.** Per-provider transcription would measure Deepgram's run-to-run variance as much as the models. The phrase list is cached per **spoken** language (`phrases-en.json`, `phrases-ko.json`, via `phrases_cache_path`), so later stages re-run free and en>ko / en>zh share one English transcript. Older per-direction (`phrases-en-ko.json`) and original `phrases.json` caches are still read.
- **Push audio at 1x, never faster.** Endpointing is supposed to key off audio timestamps, so faster-than-real-time looks free. It is not: on one 2-minute excerpt, phrases recovered were 19 (1x), 11 (2x), 5 (4x), **0 (8x)**. A run costs one wall-clock minute per audio minute.
- **Replay phrases on their spoken timeline.** Firing them back to back is ~20x the live request rate and trips provider rate limits. One run had a provider steady at 255 ms for the first half, then 2406 ms for the second, never recovering — it looked like the slowest model when it was in fact the fastest. `benchmark_dashboard.py` compares the two halves of a run and flags that step rather than silently correcting the number.
- **Judge from outside the compared set.** `OPENAI_API_KEY` exists only for this and is deliberately absent from `PROVIDER_REGISTRY`; anthropic, groq and xai each score their own vendor's output, which shuffled A/B/C labels do not fix. Cross-checked on identical translations: Opus 5, Grok 4.20 and GPT-5.5 landed within 0.07 of each other on every model and produced the same ranking, with 80% per-phrase agreement between the two independent judges.

Uploading judge scores to Arize needs annotation configs created first, and the span-id filter must come from `ax spans export --all` — plain export caps at 100 rows and makes a complete upload look 96% missing. `arize_annotations()` in the script carries the exact commands.

### Frontend

- **Captions are keyed by `phrase_id` and replaced in place**, rendered in ascending `phrase_id` order — never arrival order. A `transcript` event must not blank the translation already present for that phrase (the source language updates faster; blanking flickers).
- **`Phrase.en`/`Phrase.ko` are nullable, `Phrase.zh` optional-and-nullable** (`src/hooks/useCaptionSocket.ts`), mirroring `TranscriptOut`'s language slots (`zh` is optional so older `Phrase` literals still type-check; missing means `null`): only the spoken side is guaranteed to exist until a translation lands. `markPhraseError` preserves whichever slot is the current direction's source and explicitly clears the target slot to `null` rather than leaving a stale value. The transcript columns are role-based, not language-based — `TranscriptView` renders `caption-col source`/`caption-col target` (each also carrying a `language-tag lang-en`/`lang-ko`/`lang-zh`), so the left/right layout and styling follow the session's `Direction`, not a hardcoded English/Korean assumption.
- **The direction set is a frontend constant** (`src/directions.ts`'s `DIRECTIONS`), not a fetched endpoint. Unlike providers, whose availability varies at runtime with which API keys are configured, the direction set has no such variance — an endpoint would imitate a pattern whose justification doesn't apply here. `displayDirection(phraseCount, pending, session)` shows the pending selection while no phrases exist yet and the session's actual direction once they do, since the direction toggle is disabled while a session is live.
- `STATUS_RANK` (interim < provisional < final, with `error` ranked equal to `final`) discards lower-rank updates. It uses **strict `<`** deliberately: multiple provisionals per phrase are normal, and `<=` would freeze captions at the first one. `final` and `error` never need ranking against each other — the finals worker emits exactly one of `CaptionOut(final)` or a phrase-scoped `ErrorOut` per phrase, never both.
- **A row only ever renders once its phrase is terminal.** `src/phraseDisplay.ts` (`isTerminalPhrase`/`visiblePhrases`/`hasInFlightPhrase`) is the single, unit-tested source of truth for "terminal": `status === "final" OR status === "error"`. **Not** `status === "final"` alone — that would silently drop every phrase whose translation failed, which is the exact bug the final's `transcript` event exists to prevent (see "The phrase model" above). `TranscriptView` renders `visiblePhrases(phrases)` for rows and shows a quiet, `prefers-reduced-motion`-aware pulsing-dots loader (`.phrase-loader`) whenever `hasInFlightPhrase(phrases)` is true, instead of ever rendering interim/provisional text.
- **`useCaptionSocket`'s `Phrase.status` includes `"error"`**, set by `markPhraseError` when a phrase-scoped `ErrorOut` (`phrase_id` non-null) arrives. It preserves whatever source text the phrase's prior `transcript` events already established — the wire's `ErrorOut` carries no text at all, in either language — and explicitly clears the target slot plus `provider`/`latencyMs` to `null` rather than falling back to a stale provisional's values. `upsert`'s general merge (`mergePhrase`) only preserves whichever of `en`/`ko` a transcript update didn't just supply, plus `provider`/`latencyMs`, on an `"interim"` update; every other status (`provisional`, `final`, `error`) is a real update that replaces those fields outright, including replacing them with `null`.
- **The presentation overlay does not share the terminal-only reveal.** `OverlayView.tsx` (via `lib/overlay.ts`'s `createOverlaySnapshot`) renders straight from `socket.phrases` — the last two phrases, unfiltered by `phraseDisplay.ts` — and still styles a non-`"final"` phrase as `is-provisional` with a direction-specific placeholder (`Direction.translatingPlaceholder`: "번역 중…" for en>ko, "Translating…" for ko>en, "翻譯中…" for en>zh). If `TRANSLATE_INTERIMS=true`, the overlay will still show that dimmed provisional stage even though the main transcript view never does. Keep this in mind if the overlay's display logic changes — the two are two independent readers of the same phrase stream, not one shared component. The direction itself travels **inside** `OverlaySnapshot` (not a separate channel), so it always arrives atomically with the phrases it describes.
- **The "Finals only" UI toggle is gone.** It drove `translate_interims` for the current session, which the terminal-only reveal made meaningless to the user (provisional Korean was never shown either way). `TRANSLATE_INTERIMS` is env-only now. `sendConfig` no longer accepts a `translateInterims` field at all — the backend's `config` message handling for `translate_interims` is untouched and still reachable (by env restart, or by hand), only the frontend plumbing that sent it was removed.
- `sendStart`/`sendConfig` **queue until the socket is open** and flush in order on `onopen`. `App` calls `connect()` then awaits `getUserMedia()`, so the socket usually opens first — but losing that race would throw `InvalidStateError` and silently kill the session.
- **`connect()` clears the caption list.** Phrase IDs restart at 0 every session, so without the reset a restarted session's phrase 0 merges into the previous session's phrase 0 — stale Korean under new English, in solid final styling. `STATUS_RANK`'s strict `<` is correct and must stay; the reset is what makes it safe.
- **`fatal` ends the session client-side.** `useCaptionSocket` exposes it and `App` reacts by stopping capture and disconnecting, leaving the error banner visible. `connect()` resets it so a restart is clean.
- `sessionStartedRef` gates UI-driven `sendConfig`: the server rejects `config` before `start`. Pre-start changes update local state only and are read via refs when the session actually begins.
- `pcm-worklet.js` exists at **both** `frontend/src/audio/` and `frontend/public/` and the copies must stay byte-identical. AudioWorklets load by URL at runtime and cannot be bundled, so the `public/` copy is the one that actually runs — editing only `src/` silently does nothing.
- **The model selector is fetched, not hardcoded.** `useProviders` (`src/hooks/useProviders.ts`) calls `GET http://localhost:8000/providers` on mount and exposes `refetch`; `Controls` renders its buttons from that list. `useCaptionSocket`'s `Provider` type is a plain `string` alias now — the set is dynamic, so a union type can't describe it. An **empty** list hides the model selector and shows a message instead of an empty button group; a **failed fetch** surfaces an error rather than leaving the UI silently blank. There is no polling — `App` calls `refetch` when a session connects (a natural retry point if the frontend started before the backend), and the **Start listening** control is disabled while the fetch is in flight so the highlighted model can't desync from what the backend actually uses.
- **Caption provider labels come from the server, never a hardcoded map.** `TranscriptView` no longer has its own `PROVIDER_LABEL`; it derives a label map from the fetched provider list (`src/providers.ts`'s `toLabelMap`) and falls back to the raw provider id if that id isn't in the current list (e.g. a caption translated before a key was removed from the server config).

## Deliberate limitations

Documented in `README.md` under "Known limitations" — not oversights. Most relevant when changing code:

- **No Deepgram reconnect.** A drop ends the session. If you add reconnect, you must clear `DeepgramSource._error` in `connect()` — otherwise the first clean close after any error raises spuriously.
- **The finals queue is unbounded with no shedding.** Under sustained provider slowness, captions fall progressively further behind rather than degrading cleanly. Fixing this needs a drop policy (oldest? staleness threshold?), which is a product decision.
- **Phrase boundaries use `is_final` only**, not `speech_final`/`UtteranceEnd`. Tune `UTTERANCE_END_MS` before changing that code.

## Working in this repo

- `docs/superpowers/specs/` holds the design doc (the binding authority) and `docs/superpowers/plans/` the implementation plan. When behavior and spec disagree, the spec wins or the spec should be amended — don't let them drift silently.
- **`docs/` is intentionally untracked here.** A global gitignore excludes `docs`, and this repo's `.gitignore` re-affirms it (`docs/`, line 10) rather than negating it — specs and plans are written to disk and reviewed in place, never committed. `git add docs/...` will fail, and that is the intended behaviour: don't "fix" it with `!docs/` or `git add -f`. The same global gitignore also excludes `CLAUDE.md`, which is why this file needs the `!CLAUDE.md` negation — don't remove that one.
