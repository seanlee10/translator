from app.config import Settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.groq_provider import GroqProvider


def make_settings(**overrides) -> Settings:
    base = dict(deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g")
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
