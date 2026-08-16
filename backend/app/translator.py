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
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’"}
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_CLEAN_PASSES = 3


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


def _strip_matching_quote_pair(s: str) -> str:
    """Strip one layer of quotes, but only a genuine matching pair.

    A mismatched pair (e.g. a curly open quote with a straight apostrophe) is
    left alone: it's more likely real punctuation than an artifact wrapper.
    """
    if len(s) >= 2 and s[0] in _QUOTE_PAIRS and s[-1] == _QUOTE_PAIRS[s[0]]:
        return s[1:-1].strip()
    return s


def clean_output(raw: str) -> str:
    """Strip artifacts models emit despite instructions.

    The <think> stripping is belt-and-braces for the Groq/Qwen path: reasoning
    is disabled at the API level, but a leaked block must never reach the UI.

    A label and a quote wrapper can nest in either order (e.g. `"Korean: X"`
    or `Korean: "X"`), so the passes run repeatedly until the string stops
    changing, rather than once each in a fixed order. Bounded so a pathological
    input can never spin.
    """
    result = raw
    for _ in range(_CLEAN_PASSES):
        previous = result

        result = _THINK_BLOCK.sub("", result).strip()

        lowered = result.lower()
        for prefix in _LABEL_PREFIXES:
            if lowered.startswith(prefix):
                result = result[len(prefix) :].strip()
                break

        result = _strip_matching_quote_pair(result)

        if result == previous:
            break

    return result


class TranslatorRouter:
    """Owns the shared prompt and dispatches to the selected provider.

    All three providers must receive byte-identical prompts, otherwise
    comparing them measures the scaffolding rather than the models.
    """

    def __init__(
        self,
        providers: dict[ProviderName, Provider],
        default: ProviderName | None,
        settings: Settings,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = providers
        self._settings = settings
        self._now = now
        self._system = build_system_prompt(settings)
        # default=None means no provider is configured at all (see
        # main.py's _default_router_factory): the session must still open,
        # it just cannot translate until one is available.
        self._current: ProviderName | None = None
        if default is not None:
            self.set_provider(default)

    @property
    def current_provider(self) -> ProviderName | None:
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
        if self._current is None:
            # Caught by pipeline.py's final worker (per-phrase, non-fatal)
            # and by the interim path (silently dropped, as any interim
            # failure is) — no provider key is configured, so translation is
            # simply unavailable this session.
            raise RuntimeError("no translation provider is configured")
        provider = self._providers[self._current]
        prompt = build_user_prompt(text, context)

        started = self._now()
        raw = await provider.complete(self._system, prompt)
        elapsed_ms = int((self._now() - started) * 1000)

        return TranslationResult(
            ko=clean_output(raw), provider=provider.name, latency_ms=elapsed_ms
        )
