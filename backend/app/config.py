from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models import DirectionName, ProviderName

__all__ = [
    "DirectionName",
    "ProviderName",
    "Settings",
    "get_settings",
    "ENV_FILES",
    "REPO_ROOT",
]

# pydantic-settings resolves a bare "env_file" name against the *process CWD*.
# README documents starting the server from backend/ with .env at the repo
# root, so a bare ".env" is never found and every key comes back missing.
# Anchor the paths to this source file instead: config.py lives in
# backend/app/, so the repo root is two parents up from the package directory.
APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
REPO_ROOT = BACKEND_DIR.parent

# Both layouts are accepted. Later entries win, so a backend/.env (if someone
# keeps one there) overrides the repo-root file rather than being ignored.
ENV_FILES = (REPO_ROOT / ".env", BACKEND_DIR / ".env")


class Settings(BaseSettings):
    """All tunables in one place. Values come from the environment or .env."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILES, env_file_encoding="utf-8", extra="ignore"
    )

    # Secrets — never hardcode, never commit.
    # deepgram_api_key is required: Deepgram is the transcription itself, not
    # a translation provider, so without it there is no source text at all.
    # The three translation-provider keys are optional — an unset key simply
    # means that provider is not advertised (see app/providers/__init__.py's
    # registry and GET /providers). A server with none of them set still
    # starts and still transcribes; it just cannot translate.
    deepgram_api_key: str
    anthropic_api_key: str = ""
    groq_api_key: str = ""
    xai_api_key: str = ""

    # Translation providers
    default_provider: ProviderName = "anthropic"
    # Which way this server translates by default. The browser may override
    # it per session in the `start` message; it cannot be changed mid-session
    # because Deepgram's `language` is fixed at connect time and this app
    # deliberately has no reconnect path (see README's Known limitations).
    default_direction: DirectionName = "en>ko"
    anthropic_model: str = "claude-haiku-4-5"
    groq_model: str = "qwen/qwen3.6-27b"
    # Host only. The groq SDK appends "/openai/v1/chat/completions" itself, so a
    # base_url that already carries "/openai/v1" yields a doubled path and a 404
    # ("Unknown request URL: POST /openai/v1/openai/v1/chat/completions").
    # xai_base_url below is different: that provider goes through the generic
    # openai client, which needs the full versioned path.
    groq_base_url: str = "https://api.groq.com"
    xai_model: str = "grok-4.20-0309-non-reasoning"
    xai_base_url: str = "https://api.x.ai/v1"
    max_tokens: int = 512
    translation_timeout_s: float = 5.0
    korean_register: str = "합니다체"

    # Trigger policy
    # The main transcript view never shows a row until its phrase is
    # terminal (final or failed), so a "provisional" caption produced by
    # translating an interim is never rendered there — translating interims
    # by default is pure wasted API spend: 2-4x the calls per phrase for
    # output nobody in the primary UI sees. Off by default. Setting this to
    # true buys nothing in that main view, but the presentation overlay
    # renders phrases straight from the wire without the same terminal-only
    # gate, so it still shows the dimmed provisional stage when this is on.
    # The interim translation machinery itself (debounce, in-flight guard,
    # sequence guard) stays fully intact either way — this only flips
    # whether it runs.
    translate_interims: bool = False
    debounce_ms: int = 400
    context_pairs: int = 3

    # Evaluation only — never a translation provider.
    # OpenAI is deliberately absent from PROVIDER_REGISTRY: the app compares
    # three models and this key exists so the benchmark can be scored by a
    # judge from outside that set. A judge drawn from the compared models
    # either scores its own output or its own vendor's, and neither can be
    # read as an impartial ranking.
    openai_api_key: str = ""
    openai_judge_model: str = "gpt-5.5"

    # Observability (Arize AX)
    # All optional. Tracing turns itself on only when BOTH the space id and
    # the api key are non-empty — see app/tracing.py. An unset pair is the
    # normal case for tests and for anyone who does not want traces, and it
    # must never make the server fail to start.
    #
    # The space id is instance-scoped: a space that lives on app.arize.com
    # (the US instance, whose OTLP collector is otlp.arize.com) does not
    # exist on the EU instance and vice versa. arize_endpoint stays empty to
    # mean "use arize-otel's default", which is the US collector.
    arize_space_id: str = ""
    arize_api_key: str = ""
    arize_project_name: str = "live-caption-translator"
    # Where scripts/benchmark_translation.py publishes its judged results.
    # Deliberately a *separate* project: those spans carry the same
    # provider/latency attributes as live ones, so mixing them into the live
    # project would quietly skew every latency widget that filters on
    # provider.
    arize_quality_project: str = "translator-quality"
    arize_endpoint: str = ""

    # Transcription
    deepgram_model: str = "nova-3"
    sample_rate: int = 16000
    endpointing_ms: int = 300
    utterance_end_ms: int = 1000


@lru_cache
def get_settings() -> Settings:
    return Settings()
