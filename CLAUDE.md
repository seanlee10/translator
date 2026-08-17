# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Live caption translator: microphone → Deepgram streaming STT → Claude Haiku 4.5, Qwen3.6-27B, or Grok 4.20 non-reasoning (switchable mid-session) → side-by-side English/Korean captions. See `README.md` for setup and the `backend/.env` tuning table.

## Commands

```bash
# Backend (from backend/)
pip install -e ".[dev]"
uvicorn app.main:app --reload          # :8000
python -m pytest -v                     # 109 tests
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

The final's `transcript` event is not redundant. It is what guarantees a row exists with the correct English even when the translation fails, which the spec's Error Handling section requires. The client ranks `transcript` as `interim`, so it refines English without regressing status or blanking Korean.

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

- **Anthropic**: `claude-haiku-4-5` rejects `effort`, `thinking`, and `output_config` — plain `messages.create` with `model`/`max_tokens`/`system`/`messages` only.
- **Groq**: `qwen/qwen3.6-27b` is a reasoning model. `reasoning_effort="none"` and `reasoning_format="hidden"` are **mandatory**, not tunable — without them every caption pays a hidden reasoning pass and the latency budget is gone. `clean_output` also strips `<think>` blocks as a second line of defense.
- **xAI**: `grok-4.20-0309-non-reasoning` is the non-reasoning variant built for speed-critical requests. Unlike Groq/Qwen, there is no reasoning pass to disable — `reasoning_effort`/`reasoning_format` must never be sent to it. It is OpenAI-compatible (`https://api.x.ai/v1`, `/chat/completions`) and uses the `openai` package's `AsyncOpenAI` client rather than the `groq` SDK with a swapped base URL, so the two services can diverge without silently breaking each other.

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

### Frontend

- **Captions are keyed by `phrase_id` and replaced in place**, rendered in ascending `phrase_id` order — never arrival order. A `transcript` event must not blank Korean already present for that phrase (English updates faster; blanking flickers).
- `STATUS_RANK` (interim < provisional < final, with `error` ranked equal to `final`) discards lower-rank updates. It uses **strict `<`** deliberately: multiple provisionals per phrase are normal, and `<=` would freeze captions at the first one. `final` and `error` never need ranking against each other — the finals worker emits exactly one of `CaptionOut(final)` or a phrase-scoped `ErrorOut` per phrase, never both.
- **A row only ever renders once its phrase is terminal.** `src/phraseDisplay.ts` (`isTerminalPhrase`/`visiblePhrases`/`hasInFlightPhrase`) is the single, unit-tested source of truth for "terminal": `status === "final" OR status === "error"`. **Not** `status === "final"` alone — that would silently drop every phrase whose translation failed, which is the exact bug the final's `transcript` event exists to prevent (see "The phrase model" above). `TranscriptView` renders `visiblePhrases(phrases)` for rows and shows a quiet, `prefers-reduced-motion`-aware pulsing-dots loader (`.phrase-loader`) whenever `hasInFlightPhrase(phrases)` is true, instead of ever rendering interim/provisional text.
- **`useCaptionSocket`'s `Phrase.status` includes `"error"`**, set by `markPhraseError` when a phrase-scoped `ErrorOut` (`phrase_id` non-null) arrives. It preserves whatever English the phrase's prior `transcript` events already established — the wire's `ErrorOut` carries no English at all — and explicitly clears `ko`/`provider`/`latencyMs` to `null` rather than falling back to a stale provisional's values. `upsert`'s general merge only preserves existing `ko`/`provider`/`latencyMs` on an `"interim"` update now; every other status (`provisional`, `final`, `error`) is a real update that replaces those fields outright, including replacing them with `null`.
- **The presentation overlay does not share the terminal-only reveal.** `OverlayView.tsx` (via `lib/overlay.ts`'s `createOverlaySnapshot`) renders straight from `socket.phrases` — the last two phrases, unfiltered by `phraseDisplay.ts` — and still styles a non-`"final"` phrase as `is-provisional` with a "번역 중…" placeholder. If `TRANSLATE_INTERIMS=true`, the overlay will still show that dimmed provisional stage even though the main transcript view never does. Keep this in mind if the overlay's display logic changes — the two are two independent readers of the same phrase stream, not one shared component.
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
- A **global** gitignore excludes `docs`, so this repo's `.gitignore` carries a `!docs/` negation. Don't remove it; `git add docs/...` will silently fail without it.
