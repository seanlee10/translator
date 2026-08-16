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


async def test_anthropic_sends_model_system_and_no_forbidden_parameters():
    client = FakeAnthropicClient()
    provider = AnthropicProvider(client, make_settings())

    result = await provider.complete("SYSTEM", "PROMPT")

    assert result == "안녕하세요"
    assert provider.name == "anthropic"
    assert client.kwargs["model"] == "claude-haiku-4-5"
    assert client.kwargs["max_tokens"] == 512
    assert client.kwargs["system"] == "SYSTEM"
    assert client.kwargs["messages"] == [{"role": "user", "content": "PROMPT"}]
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


async def test_xai_sends_the_model_and_no_reasoning_parameters():
    client = FakeXAIClient()
    provider = XAIProvider(client, make_settings())

    result = await provider.complete("SYSTEM", "PROMPT")

    assert result == "반갑습니다"
    assert provider.name == "xai"
    assert client.kwargs["model"] == "grok-4.20-0309-non-reasoning"
    # max_tokens is deprecated by xAI in favor of max_completion_tokens;
    # a new integration must use the non-deprecated parameter.
    assert client.kwargs["max_completion_tokens"] == 512
    assert "max_tokens" not in client.kwargs
    # Unlike Groq/Qwen, this is the non-reasoning variant — there is no
    # reasoning pass to disable, so these must never be sent.
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
        "anthropic": "Claude Haiku 4.5",
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
