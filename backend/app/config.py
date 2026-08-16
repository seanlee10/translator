from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["anthropic", "groq"]


class Settings(BaseSettings):
    """All tunables in one place. Values come from the environment or .env."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Secrets — never hardcode, never commit.
    deepgram_api_key: str
    anthropic_api_key: str
    groq_api_key: str

    # Translation providers
    default_provider: ProviderName = "anthropic"
    anthropic_model: str = "claude-haiku-4-5"
    groq_model: str = "qwen/qwen3.6-27b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    max_tokens: int = 512
    translation_timeout_s: float = 5.0
    korean_register: str = "합니다체"

    # Trigger policy
    translate_interims: bool = True
    debounce_ms: int = 400
    context_pairs: int = 3

    # Transcription
    deepgram_model: str = "nova-3"
    sample_rate: int = 16000
    endpointing_ms: int = 300
    utterance_end_ms: int = 1000


@lru_cache
def get_settings() -> Settings:
    return Settings()
