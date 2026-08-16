# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Live caption translator: microphone → Deepgram streaming STT → Claude Haiku 4.5 or Qwen3.6-27B (switchable mid-session) → side-by-side English/Korean captions. See `README.md` for setup and the `backend/.env` tuning table.

## Commands

```bash
# Backend (from backend/)
pip install -e ".[dev]"
uvicorn app.main:app --reload          # :8000
python -m pytest -v                     # 90 tests
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

### `pipeline.py` — the only stateful component

It holds every timing and ordering decision, and **imports no I/O library** (no `anthropic`, `groq`, `deepgram`, `fastapi`). It receives a translate callable and an emit callable. That constraint is what makes the whole policy testable offline — do not break it.

Four invariants, each protecting something specific:

- **Finals go through a single serialized `asyncio.Queue` worker.** Translations complete out of order; this is what keeps the rolling context in phrase order. Never translate finals with `create_task`.
- **Interims are fire-and-forget with an in-flight guard** (`state.in_flight`): if a translation is already running for a phrase, the next interim is **skipped, never queued**. This makes the system self-limiting — a slow provider degrades to finals-only instead of growing a backlog.
- **Sequence guard**: `_translate_interim` re-checks `state.closed` *inside* `_emit_lock` before emitting, so a slow provisional can never land on top of the final that superseded it.
- **Debounce has no timer.** It is evaluated when each interim arrives (has `debounce_ms` elapsed since this phrase's last translation, or since it first appeared). Deterministic under an injected clock; correct in practice because if interims stop, the final follows within the endpointing window.

`handle()` must never block on a translation — its caller is the live transcript loop.

**There are two different locks; they do different jobs.** `pipeline._emit_lock` sequences provisional-vs-final captions. `main.py`'s per-session `send_lock` serializes *all* `ws.send_json` calls, because three tasks (session handler, pump, finals worker) write to the same socket and Starlette does not document that as safe.

### Translation: shared prompt, thin providers

`translator.py` owns the prompt, the output cleaning, and the latency timing. A provider module owns **only its SDK call**. This is not stylistic: the app exists to compare two models, and a test asserts both receive byte-identical prompts. Never move prompt construction into a provider.

Two API constraints that will produce confusing failures if "cleaned up":

- **Anthropic**: `claude-haiku-4-5` rejects `effort`, `thinking`, and `output_config` — plain `messages.create` with `model`/`max_tokens`/`system`/`messages` only.
- **Groq**: `qwen/qwen3.6-27b` is a reasoning model. `reasoning_effort="none"` and `reasoning_format="hidden"` are **mandatory**, not tunable — without them every caption pays a hidden reasoning pass and the latency budget is gone. `clean_output` also strips `<think>` blocks as a second line of defense.

### Deepgram adapter

- **Every `connect()` parameter must be a `str`** — the SDK types them all `Optional[str]`, so `True`/`16000` fail confusingly at request time. Pass `"true"`, `"16000"`. A test asserts this for all values.
- **Normalization goes `message_to_dict()` → `PhraseTracker`**, never through typed SDK attribute access. The wire JSON shape is documented and stable; the Fern-generated attribute names are not. This keeps one normalization point that survives SDK regeneration.
- `events()` **raises** `DeepgramStreamError` when the stream dies, rather than ending silently — `_pump` in `main.py` catches it and surfaces a fatal error. A *clean* end (Deepgram's own code-1000 idle/rotation close) makes `events()` return instead; `_pump` then emits `StatusOut(state="closed")` plus a non-fatal `ErrorOut`, and `main.py` stops forwarding audio once `pump.done()` so the user never sees a raw `sent 1000 (OK)` library string.
- **Keepalive is ours, not the SDK's.** `deepgram-sdk` 7.x has no automatic keepalive — only a manual `send_keep_alive()`. `DeepgramSource` runs a 5s ticker (`KEEPALIVE_INTERVAL_S`) started in `connect()` and cancelled in `aclose()`. Without it, extended silence ends the stream mid-session. The design doc used to claim the SDK handled this; it does not.
- `sample_rate` comes from the **browser per session**, not from `settings`. Browsers do not reliably honor a requested `AudioContext` rate, so the real one is sent in the `start` message and used to configure Deepgram. `settings.sample_rate` is only what the browser is *asked* for.
- `backend/scripts/verify_deepgram.py` is a manual script needing a real key and network. It is not part of the suite; run it by hand when touching the SDK adapter.

### Frontend

- **Captions are keyed by `phrase_id` and replaced in place**, rendered in ascending `phrase_id` order — never arrival order. A `transcript` event must not blank Korean already present for that phrase (English updates faster; blanking flickers).
- `STATUS_RANK` (interim < provisional < final) discards lower-rank updates. It uses **strict `<`** deliberately: multiple provisionals per phrase are normal, and `<=` would freeze captions at the first one.
- `sendStart`/`sendConfig` **queue until the socket is open** and flush in order on `onopen`. `App` calls `connect()` then awaits `getUserMedia()`, so the socket usually opens first — but losing that race would throw `InvalidStateError` and silently kill the session.
- **`connect()` clears the caption list.** Phrase IDs restart at 0 every session, so without the reset a restarted session's phrase 0 merges into the previous session's phrase 0 — stale Korean under new English, in solid final styling. `STATUS_RANK`'s strict `<` is correct and must stay; the reset is what makes it safe.
- **`fatal` ends the session client-side.** `useCaptionSocket` exposes it and `App` reacts by stopping capture and disconnecting, leaving the error banner visible. `connect()` resets it so a restart is clean.
- `sessionStartedRef` gates UI-driven `sendConfig`: the server rejects `config` before `start`. Pre-start changes update local state only and are read via refs when the session actually begins.
- `pcm-worklet.js` exists at **both** `frontend/src/audio/` and `frontend/public/` and the copies must stay byte-identical. AudioWorklets load by URL at runtime and cannot be bundled, so the `public/` copy is the one that actually runs — editing only `src/` silently does nothing.

## Deliberate limitations

Documented in `README.md` under "Known limitations" — not oversights. Most relevant when changing code:

- **No Deepgram reconnect.** A drop ends the session. If you add reconnect, you must clear `DeepgramSource._error` in `connect()` — otherwise the first clean close after any error raises spuriously.
- **The finals queue is unbounded with no shedding.** Under sustained provider slowness, captions fall progressively further behind rather than degrading cleanly. Fixing this needs a drop policy (oldest? staleness threshold?), which is a product decision.
- **Phrase boundaries use `is_final` only**, not `speech_final`/`UtteranceEnd`. Tune `UTTERANCE_END_MS` before changing that code.

## Working in this repo

- `docs/superpowers/specs/` holds the design doc (the binding authority) and `docs/superpowers/plans/` the implementation plan. When behavior and spec disagree, the spec wins or the spec should be amended — don't let them drift silently.
- A **global** gitignore excludes `docs`, so this repo's `.gitignore` carries a `!docs/` negation. Don't remove it; `git add docs/...` will silently fail without it.
