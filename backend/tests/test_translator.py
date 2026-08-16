import pytest

from app.config import Settings
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
    assert "해요체" in build_system_prompt(make_settings(korean_register="해요체"))


def test_user_prompt_orders_context_before_the_text_to_translate():
    prompt = build_user_prompt(
        "three", [ContextPair(en="one", ko="하나"), ContextPair(en="two", ko="둘")]
    )

    assert prompt.index("one") < prompt.index("two") < prompt.index("three")
    assert "하나" in prompt and "둘" in prompt


def test_user_prompt_without_context_is_still_valid():
    assert "hello" in build_user_prompt("hello", [])


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
        now=StepClock(),
    )

    result = await router.translate("hello", [])

    assert result.ko == "안녕하세요"
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
    )

    router.set_provider("groq")
    result = await router.translate("hello", [])

    assert result.provider == "groq"
    assert result.ko == "반갑습니다"
    assert len(groq.seen) == 1 and not anthropic.seen


async def test_all_registered_providers_receive_byte_identical_prompts():
    # Parameterized over PROVIDER_REGISTRY rather than hardcoding the three
    # ids: a fourth registry entry must automatically be covered by this
    # invariant, not silently skipped.
    ids = [spec.id for spec in PROVIDER_REGISTRY]
    fakes = {provider_id: FakeProvider(provider_id) for provider_id in ids}
    router = TranslatorRouter(
        dict(fakes),
        default=ids[0],
        settings=make_settings(),
    )
    context = [ContextPair(en="one", ko="하나")]

    for provider_id in ids:
        router.set_provider(provider_id)
        await router.translate("hello", context)

    seen_prompts = [fakes[provider_id].seen[0] for provider_id in ids]
    # Any quality difference must be the model, not the scaffolding. Every
    # configured provider must match, not just a pair.
    assert all(prompt == seen_prompts[0] for prompt in seen_prompts)


def test_unknown_provider_is_rejected():
    router = TranslatorRouter(
        {"anthropic": FakeProvider("anthropic")},
        default="anthropic",
        settings=make_settings(),
    )

    with pytest.raises(ValueError):
        router.set_provider("nope")

    # A rejected switch must leave the router pointing at its prior provider,
    # not at the invalid name — otherwise the next translate() would KeyError.
    assert router.current_provider == "anthropic"
