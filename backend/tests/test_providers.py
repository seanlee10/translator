from app.config import Settings
from app.providers import PROVIDER_REGISTRY, build_providers, configured_providers
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.groq_provider import GroqProvider
from app.providers.xai_provider import XAIProvider


def make_settings(**overrides) -> Settings:
    base = dict(
        deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g", xai_api_key="x"
    )
    base.update(overrides)
    # _env_file=None keeps the suite hermetic: Settings now resolves a real
    # repo-root .env, and a developer's local one must not change defaults.
    return Settings(_env_file=None, **base)


# ---- Anthropic ----


class FakeAnthropicBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeAnthropicMessage:
    def __init__(self, text):
        self.content = [FakeAnthropicBlock(text)]


class FakeAnthropicClient:
    def __init__(self, reply="안녕하세요"):
        self.reply = reply
        self.kwargs = None
        self.messages = self

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeAnthropicMessage(self.reply)


async def test_anthropic_defaults_to_haiku_5_5_with_thinking_off():
    client = FakeAnthropicClient()
    provider = AnthropicProvider(client, make_settings())

    result = await provider.complete("SYSTEM", "PROMPT")

    assert result == "안녕하세요"
    assert provider.name == "anthropic"
    assert client.kwargs["model"] == "claude-haiku-5-5"
    assert client.kwargs["max_tokens"] == 512
    assert client.kwargs["system"] == "SYSTEM"
    assert client.kwargs["messages"] == [{"role": "user", "content": "PROMPT"}]
    # Haiku 5.5 thinks by default; a caption cannot afford that pass.
    # `disabled` is only accepted at effort high or below.
    assert client.kwargs["thinking"] == {"type": "disabled"}
    assert client.kwargs["output_config"] == {"effort": "low"}
    # Haiku 5.5 rejects non-default sampling parameters.
    for forbidden in ("temperature", "top_p", "top_k", "effort"):
        assert forbidden not in client.kwargs


async def test_anthropic_on_haiku_4_5_sends_no_thinking_or_effort():
    client = FakeAnthropicClient()
    provider = AnthropicProvider(client, make_settings(anthropic_model="claude-haiku-4-5"))

    await provider.complete("SYSTEM", "PROMPT")

    assert client.kwargs["model"] == "claude-haiku-4-5"
    # Haiku 4.5 rejects these outright.
    assert "effort" not in client.kwargs
    assert "thinking" not in client.kwargs
    assert "output_config" not in client.kwargs


async def test_anthropic_returns_empty_string_when_no_text_block():
    class NoText:
        content = []

    class Client:
        def __init__(self):
            self.messages = self

        async def create(self, **kwargs):
            return NoText()

    assert await AnthropicProvider(Client(), make_settings()).complete("s", "p") == ""


# ---- Groq ----


class FakeGroqMessage:
    def __init__(self, content):
        self.content = content


class FakeGroqChoice:
    def __init__(self, content):
        self.message = FakeGroqMessage(content)


class FakeGroqCompletion:
    def __init__(self, content):
        self.choices = [FakeGroqChoice(content)]


class FakeGroqClient:
    def __init__(self, reply="반갑습니다"):
        self.reply = reply
        self.kwargs = None
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeGroqCompletion(self.reply)


async def test_groq_disables_thinking_which_is_mandatory_for_latency():
    client = FakeGroqClient()
    provider = GroqProvider(client, make_settings())

    result = await provider.complete("SYSTEM", "PROMPT")

    assert result == "반갑습니다"
    assert provider.name == "groq"
    assert client.kwargs["model"] == "qwen/qwen3.6-27b"
    # Without both of these, every caption pays a hidden reasoning pass.
    assert client.kwargs["reasoning_effort"] == "none"
    assert client.kwargs["reasoning_format"] == "hidden"


async def test_groq_sends_the_system_prompt_as_an_openai_style_message():
    client = FakeGroqClient()

    await GroqProvider(client, make_settings()).complete("SYSTEM", "PROMPT")

    assert client.kwargs["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "PROMPT"},
    ]


async def test_groq_handles_a_null_content_response():
    client = FakeGroqClient(reply=None)

    assert await GroqProvider(client, make_settings()).complete("s", "p") == ""


# ---- xAI ----
#
# Reuses the same OpenAI-style fakes as Groq (both are OpenAI-compatible
# chat.completions clients).


class FakeXAIClient:
    def __init__(self, reply="반갑습니다"):
        self.reply = reply
        self.kwargs = None
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeGroqCompletion(self.reply)


async def test_xai_on_grok_4_3_turns_reasoning_off():
    client = FakeXAIClient()
    provider = XAIProvider(client, make_settings(xai_model="grok-4.3"))

    result = await provider.complete("SYSTEM", "PROMPT")

    assert result == "반갑습니다"
    assert provider.name == "xai"
    assert client.kwargs["model"] == "grok-4.3"
    # max_tokens is deprecated by xAI in favor of max_completion_tokens;
    # a new integration must use the non-deprecated parameter.
    assert client.kwargs["max_completion_tokens"] == 512
    assert "max_tokens" not in client.kwargs
    # grok-4.3 reasons by default (~8 s per caption); "none" is mandatory.
    assert client.kwargs["reasoning_effort"] == "none"
    # Groq-only parameter.
    assert "reasoning_format" not in client.kwargs


async def test_xai_defaults_to_the_non_reasoning_model_with_no_reasoning_parameters():
    client = FakeXAIClient()
    provider = XAIProvider(client, make_settings())

    await provider.complete("SYSTEM", "PROMPT")

    assert client.kwargs["model"] == "grok-4.20-0309-non-reasoning"
    assert "reasoning_effort" not in client.kwargs
    assert "reasoning_format" not in client.kwargs


async def test_xai_sends_the_system_prompt_as_an_openai_style_message():
    client = FakeXAIClient()

    await XAIProvider(client, make_settings()).complete("SYSTEM", "PROMPT")

    assert client.kwargs["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "PROMPT"},
    ]


async def test_xai_handles_a_null_content_response():
    client = FakeXAIClient(reply=None)

    assert await XAIProvider(client, make_settings()).complete("s", "p") == ""


# ---- registry-driven construction ----


def test_registry_labels_match_the_design():
    labels = {spec.id: spec.label for spec in PROVIDER_REGISTRY}
    assert labels == {
        "anthropic": "Claude Haiku 5.5",
        "groq": "Qwen 3.6 · Groq",
        "xai": "Grok 4.20 · xAI",
    }


def test_build_providers_constructs_all_three_when_all_keys_are_configured():
    providers = build_providers(make_settings())

    assert set(providers) == {"anthropic", "groq", "xai"}
    assert isinstance(providers["anthropic"], AnthropicProvider)
    assert isinstance(providers["groq"], GroqProvider)
    assert isinstance(providers["xai"], XAIProvider)


def test_build_providers_constructs_only_the_configured_subset():
    settings = make_settings(groq_api_key="", xai_api_key="")

    providers = build_providers(settings)

    assert set(providers) == {"anthropic"}


def test_build_providers_returns_an_empty_dict_when_no_key_is_configured():
    settings = make_settings(
        anthropic_api_key="", groq_api_key="", xai_api_key=""
    )

    assert build_providers(settings) == {}


def test_configured_providers_filters_by_key_and_keeps_registry_order():
    settings = make_settings(anthropic_api_key="", xai_api_key="")

    specs = configured_providers(settings)

    assert [spec.id for spec in specs] == ["groq"]


def test_a_whitespace_only_key_is_reported_as_not_configured():
    # A copied .env.example with a stray blank value (or trailing
    # whitespace) must not be treated as "configured" — bare truthiness on
    # an unstripped string would report this as usable and the button would
    # 401 the moment it's clicked.
    settings = make_settings(anthropic_api_key="   ", groq_api_key="", xai_api_key="")

    specs = configured_providers(settings)

    assert specs == []
    assert build_providers(settings) == {}


def test_provider_base_urls_match_what_each_sdk_expects():
    """Each SDK owns a different amount of the URL path.

    The groq SDK appends "/openai/v1/chat/completions" to its base_url, so a
    base_url that already carries "/openai/v1" produces a doubled path and a
    live 404: "Unknown request URL: POST /openai/v1/openai/v1/chat/completions".
    That shipped and was only caught by a user running the app.

    The xAI provider goes through the generic openai client instead, which does
    NOT add a version segment — so there the full versioned path is required.
    The two are asymmetric on purpose; this pins both halves.
    """
    settings = make_settings()

    assert settings.groq_base_url == "https://api.groq.com"
    assert not settings.groq_base_url.rstrip("/").endswith("/openai/v1")
    assert settings.xai_base_url.rstrip("/").endswith("/v1")


def test_constructed_groq_client_resolves_to_a_single_openai_v1_segment():
    """Guards the same bug at the layer that actually builds the client."""
    from groq import AsyncGroq

    settings = make_settings()
    client = AsyncGroq(api_key="unused", base_url=settings.groq_base_url)

    resolved = f"{str(client.base_url).rstrip('/')}/openai/v1/chat/completions"

    assert resolved == "https://api.groq.com/openai/v1/chat/completions"
    assert resolved.count("/openai/v1") == 1


async def test_anthropic_sends_thinking_and_effort_only_to_haiku_5():
    # Other Claude models reject these: Sonnet 4.5 rejects `effort`, Opus
    # 5.5 / Sonnet 5.5 reject `thinking: disabled`. An ANTHROPIC_MODEL
    # override must fall back to the plain request that works everywhere.
    for model in ("claude-sonnet-4-5", "claude-opus-5-5", "claude-sonnet-5-5"):
        client = FakeAnthropicClient()
        await AnthropicProvider(client, make_settings(anthropic_model=model)).complete("s", "p")
        assert client.kwargs["model"] == model
        assert "thinking" not in client.kwargs, model
        assert "output_config" not in client.kwargs, model


async def test_xai_sends_reasoning_effort_only_to_grok_4_3():
    # grok-4.5/4.6/4.7 cannot disable reasoning and may reject "none"; an
    # unknown id must get the plain request rather than a guessed parameter.
    for model in ("grok-4.5", "grok-4.7", "grok-4.20-0309-reasoning"):
        client = FakeXAIClient()
        await XAIProvider(client, make_settings(xai_model=model)).complete("s", "p")
        assert "reasoning_effort" not in client.kwargs, model
    client = FakeXAIClient()
    await XAIProvider(client, make_settings(xai_model="grok-4.3-latest")).complete("s", "p")
    assert client.kwargs["reasoning_effort"] == "none"
