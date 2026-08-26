"""The translation direction, as data.

One frozen dataclass per direction plus a registry, mirroring the pattern
`app/providers/__init__.py` already establishes: one place declares the set,
and everything else derives from it rather than hardcoding the pair.

This module imports no I/O library, and that is load-bearing rather than
incidental: `app/pipeline.py` imports it, and pipeline.py's whole offline
testability rests on depending on no SDK. Keep it to dataclasses and typing.
"""

from dataclasses import dataclass

from app.models import DirectionName

__all__ = [
    "Direction",
    "DIRECTIONS",
    "EN_TO_KO",
    "KO_TO_EN",
    "resolve_direction",
]


@dataclass(frozen=True)
class Direction:
    """One translation direction and everything derived from it."""

    id: DirectionName
    # Deepgram's `language` connect parameter. Korean is supported on
    # nova-3 for streaming, so no model change is needed for either value.
    deepgram_language: str
    # Rendered into the shared system prompt.
    source_label: str
    target_label: str
    # `korean_register` steers Korean speech level, so it belongs in the
    # prompt only when Korean is the side being produced. English has no
    # equivalent axis and deliberately gets no knob of its own.
    applies_register: bool

    def to_slots(
        self, source_text: str, translated_text: str | None
    ) -> tuple[str | None, str | None]:
        """Map (source, translated) onto the (en, ko) LANGUAGE slots.

        `en` always carries the English text and `ko` always the Korean,
        whichever of the two was actually spoken. Keeping the slots tied to
        languages rather than roles is what lets ContextPair stay literally
        true and keeps existing Arize attributes and benchmark JSON
        comparable across this change.

        `translated_text` is None for the transcript case, where Deepgram
        text exists but no translation has been attempted yet. The source
        slot is still filled: it is what keeps a row on screen when the
        translation later fails.

        This is the ONLY place the mapping is decided. Re-deriving it at a
        call site is how the two sides silently disagree.
        """
        if self.deepgram_language == "en":
            return source_text, translated_text
        return translated_text, source_text


EN_TO_KO = Direction(
    id="en>ko",
    deepgram_language="en",
    source_label="English",
    target_label="Korean",
    applies_register=True,
)

KO_TO_EN = Direction(
    id="ko>en",
    deepgram_language="ko",
    source_label="Korean",
    target_label="English",
    applies_register=False,
)

DIRECTIONS: dict[DirectionName, Direction] = {
    EN_TO_KO.id: EN_TO_KO,
    KO_TO_EN.id: KO_TO_EN,
}


def resolve_direction(
    value: object, fallback: Direction
) -> tuple[Direction, str | None]:
    """Resolve a client-supplied direction, never raising.

    Returns the direction to use plus an error message when the supplied
    value could not be used. An unusable direction must not cost the user
    their session — main.py emits the message as a non-fatal ErrorOut and
    starts with the fallback, exactly as _default_router_factory does when
    DEFAULT_PROVIDER names a provider whose key is not configured.
    """
    if isinstance(value, str) and value in DIRECTIONS:
        return DIRECTIONS[value], None
    known = ", ".join(sorted(DIRECTIONS))
    return fallback, (
        f"unknown direction {value!r}; expected one of {known}. "
        f"Falling back to {fallback.id}."
    )
