from app.config import Settings


def make(**overrides) -> Settings:
    base = dict(deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g")
    base.update(overrides)
    return Settings(**base)


def test_defaults_match_the_design():
    s = make()

    assert s.anthropic_model == "claude-haiku-4-5"
    assert s.groq_model == "qwen/qwen3.6-27b"
    assert s.default_provider == "anthropic"
    assert s.deepgram_model == "nova-3"
    assert s.translate_interims is True
    assert s.debounce_ms == 400
    assert s.context_pairs == 3
    assert s.sample_rate == 16000
    assert s.korean_register == "합니다체"


def test_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("TRANSLATE_INTERIMS", "false")
    monkeypatch.setenv("DEBOUNCE_MS", "250")
    monkeypatch.setenv("DEFAULT_PROVIDER", "groq")

    s = make()

    assert s.translate_interims is False
    assert s.debounce_ms == 250
    assert s.default_provider == "groq"
