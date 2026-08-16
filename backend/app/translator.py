import re
import time
from typing import Callable, Protocol, Sequence

from app.config import Settings
from app.models import ContextPair, ProviderName, TranslationResult

SYSTEM_TEMPLATE = """You translate live English speech captions into Korean.

Rules:
- Output ONLY the Korean translation. No preamble, no quotes, no commentary, \
no romanization, no explanation.
- The input may be an unfinished fragment of a sentence. Translate it AS a \
fragment. Never invent or complete the rest of the sentence.
- Preserve proper nouns, product names, and acronyms.
- Use the {register} speech level consistently.
- Recent context is provided only to keep terminology and register consistent. \
Do not translate the context; translate only the final line.
"""

_LABEL_PREFIXES = ("korean:", "translation:", "번역:", "한국어:")
_QUOTES = "\"'“”‘’"
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class Provider(Protocol):
    """One model behind one call. Owns no prompt logic."""

    name: ProviderName

    async def complete(self, system: str, prompt: str) -> str: ...


def build_system_prompt(settings: Settings) -> str:
    return SYSTEM_TEMPLATE.format(register=settings.korean_register)


def build_user_prompt(text: str, context: Sequence[ContextPair]) -> str:
    parts: list[str] = []
    if context:
        lines = "\n".join(f"EN: {p.en}\nKO: {p.ko}" for p in context)
        parts.append(f"Recent context (do not translate):\n{lines}")
    parts.append(f"Translate this line to Korean:\n{text}")
    return "\n\n".join(parts)


def clean_output(raw: str) -> str:
    """Strip artifacts models emit despite instructions.

    The <think> stripping is belt-and-braces for the Groq/Qwen path: reasoning
    is disabled at the API level, but a leaked block must never reach the UI.
    """
    result = _THINK_BLOCK.sub("", raw).strip()

    lowered = result.lower()
    for prefix in _LABEL_PREFIXES:
        if lowered.startswith(prefix):
            result = result[len(prefix) :].strip()
            break

    if len(result) >= 2 and result[0] in _QUOTES and result[-1] in _QUOTES:
        result = result[1:-1].strip()

    return result


class TranslatorRouter:
    """Owns the shared prompt and dispatches to the selected provider.

    Both providers must receive byte-identical prompts, otherwise comparing
    them measures the scaffolding rather than the models.
    """

    def __init__(
        self,
        providers: dict[ProviderName, Provider],
        default: ProviderName,
        settings: Settings,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = providers
        self._settings = settings
        self._now = now
        self._system = build_system_prompt(settings)
        self._current: ProviderName = default
        self.set_provider(default)

    @property
    def current_provider(self) -> ProviderName:
        return self._current

    def set_provider(self, name: str) -> None:
        if name not in self._providers:
            raise ValueError(
                f"unknown provider {name!r}; available: {sorted(self._providers)}"
            )
        self._current = name  # type: ignore[assignment]

    async def translate(
        self, text: str, context: Sequence[ContextPair]
    ) -> TranslationResult:
        provider = self._providers[self._current]
        prompt = build_user_prompt(text, context)

        started = self._now()
        raw = await provider.complete(self._system, prompt)
        elapsed_ms = int((self._now() - started) * 1000)

        return TranslationResult(
            ko=clean_output(raw), provider=provider.name, latency_ms=elapsed_ms
        )
