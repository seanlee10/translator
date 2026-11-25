import pytest

from app.config import Settings
from app.direction import DIRECTIONS, EN_TO_KO, EN_TO_ZH, KO_TO_EN
from app.models import ContextPair
from app.providers import PROVIDER_REGISTRY
from app.translator import (
    TranslatorRouter,
    build_system_prompt,
    build_user_prompt,
    clean_output,
)


def make_settings(**overrides) -> Settings:
    base = dict(
        deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g", xai_api_key="x"
    )
    base.update(overrides)
    # _env_file=None keeps the suite hermetic: Settings now resolves a real
    # repo-root .env, and a developer's local one must not change defaults.
    return Settings(_env_file=None, **base)


class FakeProvider:
    def __init__(self, name, reply="안녕하세요"):
        self.name = name
        self.reply = reply
        self.seen: list[tuple[str, str]] = []

    async def complete(self, system, prompt):
        self.seen.append((system, prompt))
        return self.reply


class StepClock:
    """Advances 0.25s per call so latency is deterministic."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        value = self.t
        self.t += 0.25
        return value


def test_system_prompt_carries_the_configured_register():
    assert "해요체" in build_system_prompt(
        make_settings(korean_register="해요체"), EN_TO_KO
    )


def test_user_prompt_orders_context_before_the_text_to_translate():
    prompt = build_user_prompt(
        "three",
        [ContextPair(source="one", target="하나"), ContextPair(source="two", target="둘")],
        EN_TO_KO,
    )

    assert prompt.index("one") < prompt.index("two") < prompt.index("three")
    assert "하나" in prompt and "둘" in prompt


def test_user_prompt_without_context_is_still_valid():
    assert "hello" in build_user_prompt("hello", [], EN_TO_KO)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("안녕하세요", "안녕하세요"),
        ("  안녕하세요  ", "안녕하세요"),
        ('"안녕하세요"', "안녕하세요"),
        ("“안녕하세요”", "안녕하세요"),
        ("Korean: 안녕하세요", "안녕하세요"),
        ("번역: 안녕하세요", "안녕하세요"),
        ("<think>reasoning</think>안녕하세요", "안녕하세요"),
        ('"Korean: 안녕하세요"', "안녕하세요"),
        ('Korean: "안녕하세요"', "안녕하세요"),
        ("“안녕하세요'", "“안녕하세요'"),
        ('<think>reasoning</think>"안녕하세요"', "안녕하세요"),
    ],
)
def test_output_is_stripped_of_quotes_labels_and_think_blocks(raw, expected):
    assert clean_output(raw) == expected


async def test_router_uses_the_default_provider_and_stamps_provenance():
    anthropic = FakeProvider("anthropic")
    groq = FakeProvider("groq")
    router = TranslatorRouter(
        {"anthropic": anthropic, "groq": groq},
        default="anthropic",
        settings=make_settings(),
        direction=EN_TO_KO,
        now=StepClock(),
    )

    result = await router.translate("hello", [])

    assert result.text == "안녕하세요"
    assert result.provider == "anthropic"
    assert result.latency_ms == 250
    assert len(anthropic.seen) == 1 and not groq.seen


async def test_switching_provider_routes_subsequent_calls():
    anthropic = FakeProvider("anthropic")
    groq = FakeProvider("groq", reply="반갑습니다")
    router = TranslatorRouter(
        {"anthropic": anthropic, "groq": groq},
        default="anthropic",
        settings=make_settings(),
        direction=EN_TO_KO,
    )

    router.set_provider("groq")
    result = await router.translate("hello", [])

    assert result.provider == "groq"
    assert result.text == "반갑습니다"
    assert len(groq.seen) == 1 and not anthropic.seen


def test_unknown_provider_is_rejected():
    router = TranslatorRouter(
        {"anthropic": FakeProvider("anthropic")},
        default="anthropic",
        settings=make_settings(),
        direction=EN_TO_KO,
    )

    with pytest.raises(ValueError):
        router.set_provider("nope")

    # A rejected switch must leave the router pointing at its prior provider,
    # not at the invalid name — otherwise the next translate() would KeyError.
    assert router.current_provider == "anthropic"


@pytest.mark.parametrize("direction", list(DIRECTIONS.values()), ids=list(DIRECTIONS))
def test_all_configured_providers_receive_byte_identical_prompts(direction):
    # The app exists to compare models. If the scaffolding differs per
    # provider, the comparison measures the scaffolding. Parameterized over
    # PROVIDER_REGISTRY so a newly registered provider is covered
    # automatically, and now over DIRECTIONS too.
    settings = make_settings()
    providers = {spec.id: FakeProvider(spec.id) for spec in PROVIDER_REGISTRY}
    context = (ContextPair(source="hello", target="translated"),)

    seen = []
    for spec in PROVIDER_REGISTRY:
        router = TranslatorRouter(providers, spec.id, settings, direction)
        import asyncio

        asyncio.run(router.translate("some text", context))
        seen.append(providers[spec.id].seen[-1])

    assert len(set(seen)) == 1, "providers received different prompts"


def test_the_system_prompt_names_the_direction_it_is_translating():
    en_ko = build_system_prompt(make_settings(), EN_TO_KO)
    ko_en = build_system_prompt(make_settings(), KO_TO_EN)

    assert "English speech captions into Korean" in en_ko
    assert "Korean speech captions into English" in ko_en
    assert en_ko != ko_en


def test_the_korean_register_appears_only_when_korean_is_the_target():
    settings = make_settings(korean_register="해요체")
    assert "해요체" in build_system_prompt(settings, EN_TO_KO)
    # English has no speech-level axis, so steering it with a Korean
    # register would be meaningless instruction noise.
    assert "해요체" not in build_system_prompt(settings, KO_TO_EN)


def test_the_user_prompt_asks_for_the_target_language():
    assert "to Korean" in build_user_prompt("hi", (), EN_TO_KO)
    assert "to English" in build_user_prompt("안녕", (), KO_TO_EN)


def test_context_lines_read_source_first_in_each_direction():
    en_ko = build_user_prompt(
        "more", (ContextPair(source="hello", target="안녕하세요"),), EN_TO_KO
    )
    ko_en = build_user_prompt(
        "더", (ContextPair(source="안녕하세요", target="hello"),), KO_TO_EN
    )

    # Same pair, ordered so the model reads source then target — which is
    # the order it is being asked to produce.
    assert en_ko.index("EN: hello") < en_ko.index("KO: 안녕하세요")
    assert ko_en.index("KO: 안녕하세요") < ko_en.index("EN: hello")


def test_translation_result_carries_text_not_ko():
    # The field is direction-neutral now: it holds the TRANSLATED side,
    # which is Korean one way and English the other.
    settings = make_settings()
    providers = {"anthropic": FakeProvider("anthropic", reply="hello there")}
    router = TranslatorRouter(providers, "anthropic", settings, KO_TO_EN)

    import asyncio

    result = asyncio.run(router.translate("안녕하세요", ()))
    assert result.text == "hello there"
    assert not hasattr(result, "ko")


def test_clean_output_strips_english_side_labels():
    # Models prefix output in the target language, so the KO→EN path needs
    # its own label set or every caption reads "English: hello there".
    assert clean_output("English: hello there") == "hello there"
    assert clean_output('English: "hello there"') == "hello there"
    assert clean_output("Translation: hello there") == "hello there"


def test_clean_output_still_strips_korean_side_labels():
    assert clean_output("한국어: 안녕하세요") == "안녕하세요"
    assert clean_output("번역: 안녕하세요") == "안녕하세요"


def test_the_chinese_system_prompt_demands_taiwanese_traditional_script():
    system = build_system_prompt(make_settings(korean_register="해요체"), EN_TO_ZH)
    assert "English speech captions into Traditional Chinese (Taiwan)" in system
    assert "Simplified" in system
    # The Korean register is not a Chinese knob.
    assert "해요체" not in system


def test_the_korean_prompts_carry_no_chinese_rule():
    for direction in (EN_TO_KO, KO_TO_EN):
        assert "Simplified" not in build_system_prompt(make_settings(), direction)


def test_chinese_context_lines_are_tagged_en_then_zh():
    prompt = build_user_prompt(
        "more", (ContextPair(source="hello", target="你好"),), EN_TO_ZH
    )
    assert prompt.index("EN: hello") < prompt.index("ZH: 你好")
    assert "KO:" not in prompt
    assert "to Traditional Chinese (Taiwan)" in prompt


@pytest.mark.parametrize(
    "raw",
    [
        "Chinese: 你好",
        "Traditional Chinese: 你好",
        "Traditional Chinese (Taiwan): 你好",
        "繁體中文：你好",
        "中文: 你好",
        "翻譯：「你好」".replace("「", '"').replace("」", '"'),
        '"你好"',
    ],
)
def test_clean_output_strips_chinese_labels(raw):
    assert clean_output(raw) == "你好"
