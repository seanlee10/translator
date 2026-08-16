from anthropic import AsyncAnthropic
from groq import AsyncGroq

from app.config import Settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.groq_provider import GroqProvider

__all__ = ["AnthropicProvider", "GroqProvider", "build_providers"]


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
    }
