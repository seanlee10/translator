from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

ProviderName = Literal["anthropic", "groq", "xai"]
# The known translation directions, as ids that ride on the wire and on the
# `phrase.direction` tracing attribute. Declared here, next to ProviderName,
# and imported into config.py — a duplicated literal means adding a
# direction takes three edits instead of two, and a missed copy fails
# silently rather than loudly.
DirectionName = Literal["en>ko", "ko>en"]

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
    """A translation plus the provenance needed to compare providers.

    `text` is the TRANSLATED side, whichever language that is in the current
    direction. It is deliberately not named `ko`: the pipeline maps it onto
    the en/ko wire slots via Direction.to_slots, and a language-specific name
    here invited exactly the confusion that mapping exists to prevent.
    """

    text: str
    provider: ProviderName
    latency_ms: int


# ---- Outgoing wire messages (server -> browser) ----


class ProviderInfo(BaseModel):
    """One entry of GET /providers — a translation provider the server can
    actually use, because its API key is configured."""

    id: str
    label: str
    model: str


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
