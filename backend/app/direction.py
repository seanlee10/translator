"""The translation direction, as data.

One frozen dataclass per direction plus a registry, mirroring the pattern
`app/providers/__init__.py` already establishes: one place declares the set,
and everything else derives from it rather than hardcoding the pair.

This module imports no I/O library, and that is load-bearing rather than
incidental: `app/pipeline.py` imports it, and pipeline.py's whole offline
testability rests on depending on no SDK. Keep it to dataclasses and typing.
"""

from dataclasses import dataclass

from app.models import DirectionName, LanguageSlot

__all__ = [
    "Direction",
    "DIRECTIONS",
    "EN_TO_KO",
    "EN_TO_ZH",
    "KO_TO_EN",
    "resolve_direction",
]


@dataclass(frozen=True)
class Direction:
    """One translation direction and everything derived from it."""

    id: DirectionName
    # Deepgram's `language` connect parameter. Korean is supported on
    # nova-3 for streaming, so no model change is needed for either value.
    # No direction transcribes Chinese: zh>en is deliberately not offered.
    deepgram_language: str
    # The wire LANGUAGE slots the spoken and the produced text land in.
    source_slot: LanguageSlot
    target_slot: LanguageSlot
    # Rendered into the shared system prompt.
    source_label: str
    target_label: str
    # `korean_register` steers Korean speech level, so it belongs in the
    # prompt only when Korean is the side being produced. English has no
    # equivalent axis and deliberately gets no knob of its own.
    applies_register: bool
    # Extra system-prompt rule for this target, or "". Used for Chinese,
    # where naming the language is not enough: the audience is in Taiwan, so
    # Simplified characters or Mainland vocabulary are wrong even when the
    # translation is otherwise correct.
    target_rule: str = ""

    def to_slots(
        self, source_text: str, translated_text: str | None
    ) -> dict[LanguageSlot, str | None]:
        """Map (source, translated) onto the en/ko/zh LANGUAGE slots.

        Returns every slot, ready to splat into TranscriptOut / CaptionOut.
        `en` always carries English, `ko` Korean and `zh` Chinese, whichever
        was actually spoken; the slot this direction does not use is None.
        Keeping the slots tied to languages rather than roles keeps existing
        Arize attributes and benchmark JSON comparable across directions.

        `translated_text` is None for the transcript case, where Deepgram
        text exists but no translation has been attempted yet. The source
        slot is still filled: it is what keeps a row on screen when the
        translation later fails.

        This is the ONLY place the mapping is decided. Re-deriving it at a
        call site is how the two sides silently disagree.
        """
        slots: dict[LanguageSlot, str | None] = {"en": None, "ko": None, "zh": None}
        slots[self.source_slot] = source_text
        slots[self.target_slot] = translated_text
        return slots


EN_TO_KO = Direction(
    id="en>ko",
    deepgram_language="en",
    source_slot="en",
    target_slot="ko",
    source_label="English",
    target_label="Korean",
    applies_register=True,
)

KO_TO_EN = Direction(
    id="ko>en",
    deepgram_language="ko",
    source_slot="ko",
    target_slot="en",
    source_label="Korean",
    target_label="English",
    applies_register=False,
)

EN_TO_ZH = Direction(
    id="en>zh",
    deepgram_language="en",
    source_slot="en",
    target_slot="zh",
    source_label="English",
    target_label="Traditional Chinese (Taiwan)",
    applies_register=False,
    target_rule=(
        "- Write Traditional Chinese characters (繁體中文) with the vocabulary "
        "and phrasing used in Taiwan (e.g. 軟體, 影片, 資訊, 網路). Never use "
        "Simplified characters or Mainland-specific terms.\n"
    ),
)

DIRECTIONS: dict[DirectionName, Direction] = {
    EN_TO_KO.id: EN_TO_KO,
    KO_TO_EN.id: KO_TO_EN,
    EN_TO_ZH.id: EN_TO_ZH,
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
