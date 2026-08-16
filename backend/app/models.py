from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

ProviderName = Literal["anthropic", "groq"]

# ---- Internal domain types (never cross the wire directly) ----


@dataclass(frozen=True)
class TranscriptEvent:
    """One transcript result from Deepgram, tagged with its phrase.

    A phrase is the span of interim results terminated by one is_final result.
    """

    phrase_id: int
    text: str
    is_final: bool


@dataclass(frozen=True)
class ContextPair:
    """A finalized English/Korean pair, fed back as translation context."""

    en: str
    ko: str


@dataclass(frozen=True)
class TranslationResult:
    """A translation plus the provenance needed to compare providers."""

    ko: str
    provider: ProviderName
    latency_ms: int


# ---- Outgoing wire messages (server -> browser) ----


class StatusOut(BaseModel):
    type: Literal["status"] = "status"
    state: Literal["connected", "listening", "closed", "reconnecting"]


class TranscriptOut(BaseModel):
    """English only, published the instant Deepgram produces it. No API cost."""

    type: Literal["transcript"] = "transcript"
    phrase_id: int
    en: str
    status: Literal["interim"] = "interim"


class CaptionOut(BaseModel):
    """A translated phrase. 'provisional' may be superseded by 'final'."""

    type: Literal["caption"] = "caption"
    phrase_id: int
    en: str
    ko: str
    status: Literal["provisional", "final"]
    provider: ProviderName
    latency_ms: int


class ErrorOut(BaseModel):
    type: Literal["error"] = "error"
    phrase_id: int | None = None
    message: str
    fatal: bool = False
