from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, model_validator

ProviderName = Literal["anthropic", "groq", "xai"]
# The known translation directions, as ids that ride on the wire and on the
# `phrase.direction` tracing attribute. Declared here, next to ProviderName,
# and imported into config.py — a duplicated literal means adding a
# direction takes three edits instead of two, and a missed copy fails
# silently rather than loudly.
DirectionName = Literal["en>ko", "ko>en", "en>zh"]
# The wire's LANGUAGE slots. One field per language on TranscriptOut and
# CaptionOut; Direction.to_slots is the only place text is mapped onto them.
LanguageSlot = Literal["en", "ko", "zh"]

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
    """A finalized (spoken, translated) pair, fed back as translation context.

    Role-based, unlike the wire's language slots: it never leaves the
    process, and a session has exactly one direction, so the prompt builder
    can label each side from the Direction rather than this type needing a
    field per language.
    """

    source: str
    target: str


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
    """Source text only, published the instant Deepgram produces it. No API cost.

    Exactly one slot is populated, chosen by Direction.to_slots: `en` always
    holds English, `ko` always Korean and `zh` always Chinese, whichever was
    spoken. All are optional because only the source side exists at this
    point — the translation has not been attempted yet.

    This event is what guarantees a row exists with the correct SOURCE text
    even when the translation fails, which the spec's Error Handling section
    requires. The client ranks it as `interim`, so it refines the source
    text without regressing status or blanking the translation.
    """

    type: Literal["transcript"] = "transcript"
    phrase_id: int
    en: str | None = None
    ko: str | None = None
    zh: str | None = None
    status: Literal["interim"] = "interim"


class CaptionOut(BaseModel):
    """A translated phrase. 'provisional' may be superseded by 'final'.

    Exactly two language slots are populated — the session direction's
    source and target — and the third is null. Which two is decided only by
    Direction.to_slots. No single pair of slots is required across every
    direction, so the fields are optional and the validator below enforces
    the real guarantee instead: by the time a caption exists, both sides do.
    """

    type: Literal["caption"] = "caption"
    phrase_id: int
    en: str | None = None
    ko: str | None = None
    zh: str | None = None
    status: Literal["provisional", "final"]
    provider: ProviderName
    latency_ms: int

    @model_validator(mode="after")
    def _exactly_two_slots(self) -> "CaptionOut":
        filled = [s for s in (self.en, self.ko, self.zh) if s is not None]
        if len(filled) != 2:
            raise ValueError(
                "a caption carries exactly two language slots (source and "
                f"target); got {len(filled)}"
            )
        return self


class ErrorOut(BaseModel):
    type: Literal["error"] = "error"
    phrase_id: int | None = None
    message: str
    fatal: bool = False
