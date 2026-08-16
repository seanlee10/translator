from anthropic import AsyncAnthropic
from groq import AsyncGroq
from openai import AsyncOpenAI

from app.config import Settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.groq_provider import GroqProvider
from app.providers.xai_provider import XAIProvider

__all__ = ["AnthropicProvider", "GroqProvider", "XAIProvider", "build_providers"]


def build_providers(settings: Settings) -> dict:
    """Constructs real SDK clients. Tests inject fakes instead of calling this."""
    return {
        "anthropic": AnthropicProvider(
            AsyncAnthropic(api_key=settings.anthropic_api_key), settings
        ),
        "groq": GroqProvider(
            AsyncGroq(api_key=settings.groq_api_key, base_url=settings.groq_base_url),
            settings,
        ),
        "xai": XAIProvider(
            AsyncOpenAI(api_key=settings.xai_api_key, base_url=settings.xai_base_url),
            settings,
        ),
    }
