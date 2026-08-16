# Live Caption Translator

Speak English into your microphone; see English and Korean captions side by side
in real time. Deepgram transcribes; Claude Haiku 4.5, Qwen3.6-27B, or Grok 4.20
(non-reasoning) translates — switchable mid-session so you can compare them on
the same speech.

## Setup

```bash
cd backend
cp .env.example .env
# Edit backend/.env and add DEEPGRAM_API_KEY — required, it's the
# transcription itself. Add any of ANTHROPIC_API_KEY, GROQ_API_KEY, and
# XAI_API_KEY to enable that translation model; each is optional, and the
# UI only ever shows a model whose key is actually set (see "Translation
# providers are auto-discovered" below).
# .env is git-ignored at any depth — never commit it.
pip install -e ".[dev]"
cd ../frontend && npm install && cd ..
```

`.env` belongs in `backend/` — the backend is the only thing that reads it,
and it is also the directory you run the server from. A repo-root `.env` is
accepted too (if one exists, `backend/.env` overrides it), so an older layout
keeps working; `backend/` is the recommended location.

## Run

Two terminals:

```bash
# Terminal 1 — backend on :8000
cd backend && uvicorn app.main:app --reload

# Terminal 2 — frontend on :5173
cd frontend && npm run dev
```

Open http://localhost:5173, click **Start listening**, allow microphone access.

The backend's CORS is locked to exactly `http://localhost:5173`; serving the
frontend from any other origin or port will be rejected.

### macOS presentation overlay

For a normal browser preview, click **Open overlay**. This opens the last two
English/Korean phrases in a caption-only popup and keeps it synchronized with
the control screen through `BroadcastChannel`.

For a real macOS presentation overlay that stays above Keynote or PowerPoint
full-screen, run the Electron desktop shell instead:

```bash
# Terminal 1 — backend
cd backend && uvicorn app.main:app --reload

# Terminal 2 — Vite + Electron control window
cd frontend && npm run desktop:dev
```

Click **Open overlay** in the desktop control window. The overlay:

- prefers an attached external display, falling back to the MacBook display;
- is transparent, frameless, always on top, and visible over full-screen Spaces;
- ignores mouse input, so presentation controls remain clickable underneath;
- shows only the newest two phrases, with Korean emphasized for the audience.

For the production-built frontend, use `cd frontend && npm run desktop` while
the backend is running. This builds `dist/` and launches Electron without the
Vite development server. Press `Control+C` in the launching terminal or quit
the desktop app normally when the presentation ends.

## Tests

```bash
cd backend && python -m pytest -v
```

106 tests, all passing, with no network access and no API keys set — the suite
builds `Settings` directly with placeholder key strings in-process, with the
env-file lookup disabled (`_env_file=None`), rather than reading `.env` or
hitting any provider. A global 30s per-test timeout (`pytest-timeout`,
configured in `backend/pyproject.toml`) guards against a hang in async code
silently blocking the suite.

### Frontend type-check

```bash
cd frontend && npx tsc --build --noEmit --force
```

Use the `--build` form, not plain `npx tsc --noEmit`. This project's
`tsconfig.json` is a solution-style file (`"files": []` plus `references`),
so plain `tsc --noEmit` type-checks zero files and exits `0` even when the
code has type errors. `--build` follows the references and actually compiles
`tsconfig.app.json` and `tsconfig.node.json`.

## Translation providers are auto-discovered

The backend is the single source of truth for which translation models exist.
`GET /providers` returns only the providers whose API key is actually set in
`backend/.env`; the frontend fetches that list on load and builds its model
selector from it instead of a hardcoded set. If no translation key is set,
the selector is hidden and the UI says so — transcription (Deepgram) still
works, only translation is unavailable. If `DEFAULT_PROVIDER` names a
provider whose key isn't set, the server falls back to the first configured
one and logs a warning rather than refusing to start.

## Comparing the three models

Switch the **Model** dropdown mid-session. Each transcript row shows which model
produced its Korean and how long the call took, and **Export** includes all three,
so you can compare quality and latency on identical speech. Only models with a
configured key ever appear here.

Note that Qwen3.6-27B is on Groq's **preview** tier and may be withdrawn at short
notice. Provider failures are isolated per phrase and never end the session —
if one model starts failing, switch to another from the **Model** dropdown at
any time.

Grok 4.20 is the **non-reasoning** variant (`grok-4.20-0309-non-reasoning`),
served through xAI's OpenAI-compatible endpoint. Unlike Qwen on Groq, there is
no reasoning pass to disable for this model — it never receives Groq-specific
parameters like `reasoning_effort`.

## Tuning

All knobs live in `backend/.env` (see `backend/.env.example`); every variable
below has a default in `backend/app/config.py` and can be overridden without
touching code.

| Variable | Default | Effect |
|---|---|---|
| `DEFAULT_PROVIDER` | `anthropic` | Which model (`anthropic`, `groq`, or `xai`) a new session starts with — falls back to the first configured provider (with a warning) if this one's key isn't set |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5` | Claude model used for translation |
| `GROQ_MODEL` | `qwen/qwen3.6-27b` | Groq model used for translation |
| `XAI_MODEL` | `grok-4.20-0309-non-reasoning` | xAI Grok model used for translation |
| `XAI_BASE_URL` | `https://api.x.ai/v1` | xAI's OpenAI-compatible API base URL |
| `DEEPGRAM_MODEL` | `nova-3` | Deepgram transcription model |
| `TRANSLATE_INTERIMS` | `true` | `false` translates only completed phrases — Korean lags more, costs less |
| `DEBOUNCE_MS` | `400` | Minimum gap between translations of the same in-progress phrase |
| `CONTEXT_PAIRS` | `3` | Prior phrases sent as context for terminology consistency |
| `KOREAN_REGISTER` | `합니다체` | Korean speech level, e.g. `해요체` for a softer register |
| `ENDPOINTING_MS` | `300` | Lower = shorter phrases, faster finals, more API calls |
| `UTTERANCE_END_MS` | `1000` | Deepgram silence duration used to detect utterance end |

The **Finals only** checkbox toggles `TRANSLATE_INTERIMS` for the current
session, so you can feel the latency difference without a restart.

## Manual verification checklist

Automated tests cover backend logic only. Before relying on the app, verify
the full pipeline by hand with both servers running and real API keys in
`backend/.env`:

1. `http://localhost:8000/health` returns `{"status":"ok"}`.
2. The page loads showing status `idle`.
3. **Start listening** prompts for microphone permission; status becomes `listening`.
4. Speaking produces English text within roughly 300 ms.
5. Dimmed italic Korean appears within roughly 1 second and turns solid at each phrase boundary.
6. Each row shows a model label and a latency figure.
7. Switching the **Model** dropdown mid-session changes the label on subsequent rows, with no reconnect and no lost audio.
8. **Finals only** visibly removes the dimmed intermediate stage.
9. Scrolling up reveals **Jump to live**; clicking re-pins.
10. **Export** downloads a transcript containing both languages plus provenance.

If provisional Korean feels sluggish, lower `DEBOUNCE_MS`; if it feels jumpy
or costly, raise it or use finals-only.

## Known limitations (by design)

- **No Deepgram reconnect.** A dropped Deepgram connection ends the session
  with a fatal error rather than retrying.
- **The finals queue is unbounded and sheds nothing.** Completed phrases are
  translated one at a time through a single queue to keep context in order.
  If a provider is persistently slower than speech, that queue grows and
  captions fall progressively further behind rather than degrading cleanly —
  there is no drop policy. Switch models or enable **Finals only** if you see
  it. (Interim translations are self-limiting and do not queue.)
- **No hosting, authentication, or rate limiting.** This is a local
  single-user dev tool.
- **English source, Korean target only.**
- **`is_final` only.** Phrase boundaries rely on Deepgram's `is_final` flag,
  not `speech_final` / `UtteranceEnd`. If phrases run short in practice, tune
  `UTTERANCE_END_MS` before changing code.
- **Shared prompt across providers.** Claude, Groq, and xAI receive a
  byte-identical translation prompt by design, to keep the comparison fair.
