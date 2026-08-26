import pytest

from app.config import Settings
from app.direction import (
    DIRECTIONS,
    EN_TO_KO,
    KO_TO_EN,
    Direction,
    resolve_direction,
)


def make_settings(**overrides) -> Settings:
    base = dict(
        deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g", xai_api_key="x"
    )
    base.update(overrides)
    # _env_file=None keeps the suite hermetic: Settings resolves a real
    # repo-root .env, and a developer's local one must not change defaults.
    return Settings(_env_file=None, **base)


def test_the_registry_holds_exactly_the_two_known_directions():
    assert set(DIRECTIONS) == {"en>ko", "ko>en"}
    assert DIRECTIONS["en>ko"] is EN_TO_KO
    assert DIRECTIONS["ko>en"] is KO_TO_EN


def test_each_direction_id_matches_its_registry_key():
    # A mismatch here would make resolve_direction return a Direction whose
    # id disagrees with the key it was looked up by, and that id is what
    # rides on the tracing attribute and the wire.
    for key, direction in DIRECTIONS.items():
        assert direction.id == key


def test_en_to_ko_transcribes_english_and_applies_the_korean_register():
    assert EN_TO_KO.deepgram_language == "en"
    assert EN_TO_KO.source_label == "English"
    assert EN_TO_KO.target_label == "Korean"
    # The register setting steers Korean speech level; it only means
    # something when Korean is what we are producing.
    assert EN_TO_KO.applies_register is True


def test_ko_to_en_transcribes_korean_and_applies_no_register():
    assert KO_TO_EN.deepgram_language == "ko"
    assert KO_TO_EN.source_label == "Korean"
    assert KO_TO_EN.target_label == "English"
    assert KO_TO_EN.applies_register is False


def test_to_slots_puts_english_first_when_translating_into_korean():
    assert EN_TO_KO.to_slots("hello there", "안녕하세요") == ("hello there", "안녕하세요")


def test_to_slots_puts_korean_first_when_translating_into_english():
    # The slots are LANGUAGE slots, not role slots: `en` is always the
    # English text regardless of which side was spoken. This is the single
    # place that mapping is decided.
    assert KO_TO_EN.to_slots("안녕하세요", "hello there") == ("hello there", "안녕하세요")


def test_to_slots_leaves_the_target_slot_empty_for_an_untranslated_transcript():
    # This is the TranscriptOut case: Deepgram text exists, translation does
    # not yet. The source slot must still be populated, because it is what
    # keeps a row on screen when the translation later fails.
    assert EN_TO_KO.to_slots("hello", None) == ("hello", None)
    assert KO_TO_EN.to_slots("안녕", None) == (None, "안녕")


def test_resolve_direction_accepts_a_known_id_with_no_error():
    direction, error = resolve_direction("ko>en", fallback=EN_TO_KO)
    assert direction is KO_TO_EN
    assert error is None


@pytest.mark.parametrize("bad", ["fr>en", "", "en-ko", 7, None, {"id": "ko>en"}])
def test_resolve_direction_falls_back_and_explains_rather_than_raising(bad):
    # An unusable direction must never cost the user their session: main.py
    # surfaces the message as a non-fatal ErrorOut and starts anyway, the
    # same posture _default_router_factory takes for an unconfigured
    # DEFAULT_PROVIDER.
    direction, error = resolve_direction(bad, fallback=EN_TO_KO)
    assert direction is EN_TO_KO
    assert error is not None
    assert "en>ko" in error and "ko>en" in error


def test_resolve_direction_uses_the_given_fallback_not_a_hardcoded_default():
    # A `start` message with no direction field never reaches this function
    # (main.py only calls it when the field is present), so absence is not
    # modelled here. What matters is that the fallback is whatever the
    # server default says, not a constant baked in.
    direction, error = resolve_direction("nonsense", fallback=KO_TO_EN)
    assert direction is KO_TO_EN
    assert error is not None


def test_settings_default_direction_is_english_to_korean():
    assert make_settings().default_direction == "en>ko"


def test_settings_default_direction_is_overridable():
    assert make_settings(default_direction="ko>en").default_direction == "ko>en"


def test_settings_rejects_an_unknown_default_direction():
    # DirectionName is a Literal, so pydantic validates it at construction —
    # a typo in .env fails loudly at startup rather than at first phrase.
    with pytest.raises(Exception):
        make_settings(default_direction="fr>en")


def test_direction_is_frozen():
    with pytest.raises(Exception):
        EN_TO_KO.deepgram_language = "fr"  # type: ignore[misc]


def test_direction_imports_no_io_library():
    # app/direction.py is imported BY pipeline.py, the one module in this
    # app that must depend on no I/O library at all — that is what keeps the
    # whole timing policy testable offline. An SDK import added here breaks
    # that silently, surfacing only as the suite suddenly needing a network.
    #
    # Scan the IMPORT STATEMENTS, not the raw text: the docstrings in this
    # module legitimately discuss Deepgram and the provider SDKs by name,
    # and a substring scan over prose is a false-positive machine.
    import ast

    import app.direction as module

    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    banned = {"anthropic", "groq", "deepgram", "fastapi", "openai", "httpx"}
    assert not (imported & banned), f"direction.py must not import {imported & banned}"
    # Only stdlib and app.models — anything else deserves a second look.
    assert imported <= {"dataclasses", "typing", "app"}, imported


def test_direction_is_a_dataclass_instance():
    assert isinstance(EN_TO_KO, Direction)
