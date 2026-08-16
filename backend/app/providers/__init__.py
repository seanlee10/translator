from dataclasses import dataclass
from typing import Callable

from anthropic import AsyncAnthropic
from groq import AsyncGroq
from openai import AsyncOpenAI

from app.config import Settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.groq_provider import GroqProvider
from app.providers.xai_provider import XAIProvider

__all__ = [
    "AnthropicProvider",
    "GroqProvider",
    "XAIProvider",
    "ProviderSpec",
    "PROVIDER_REGISTRY",
    "configured_providers",
    "build_providers",
]


@dataclass(frozen=True)
class ProviderSpec:
    """One entry in the provider registry — the single source of truth.

    `build_providers`, `GET /providers`, and the availability check all
    derive from this list instead of each hardcoding the provider set.
    """

    id: str
    label: str
    model_attr: str  # Settings attribute name holding this provider's model id
    key_attr: str  # Settings attribute name holding this provider's API key
    build: Callable[[Settings], object]  # constructs the provider instance


PROVIDER_REGISTRY: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="anthropic",
        label="Claude Haiku 4.5",
        model_attr="anthropic_model",
        key_attr="anthropic_api_key",
        build=lambda settings: AnthropicProvider(
            AsyncAnthropic(api_key=settings.anthropic_api_key), settings
        ),
    ),
    ProviderSpec(
        id="groq",
        label="Qwen 3.6 · Groq",
        model_attr="groq_model",
        key_attr="groq_api_key",
        build=lambda settings: GroqProvider(
            AsyncGroq(api_key=settings.groq_api_key, base_url=settings.groq_base_url),
            settings,
        ),
    ),
    ProviderSpec(
        id="xai",
        label="Grok 4.20 · xAI",
        model_attr="xai_model",
        key_attr="xai_api_key",
        build=lambda settings: XAIProvider(
            AsyncOpenAI(api_key=settings.xai_api_key, base_url=settings.xai_base_url),
            settings,
        ),
    ),
)


def _is_configured(spec: ProviderSpec, settings: Settings) -> bool:
    # Strip before testing: a whitespace-only value (e.g. a stray space left
    # in a .env file) is truthy under bare `bool()` but is not a usable key.
    return bool(getattr(settings, spec.key_attr).strip())


def configured_providers(settings: Settings) -> list[ProviderSpec]:
    """Registry entries whose API key is actually set, in registry order."""
    return [spec for spec in PROVIDER_REGISTRY if _is_configured(spec, settings)]


def build_providers(settings: Settings) -> dict:
    """Constructs real SDK clients — only for configured providers.

    An unset key must never produce a client: that client would only fail at
    call time instead of the provider simply never being offered. Tests
    inject fakes instead of calling this.
    """
    return {spec.id: spec.build(settings) for spec in configured_providers(settings)}
