import logging

import pytest

from app.config import Settings
from app.main import _default_router_factory


def make_settings(**overrides) -> Settings:
    base = dict(
        deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g", xai_api_key="x"
    )
    base.update(overrides)
    # _env_file=None keeps the suite hermetic: Settings now resolves a real
    # repo-root .env, and a developer's local one must not change defaults.
    return Settings(_env_file=None, **base)


def test_default_provider_pointing_at_an_unconfigured_provider_falls_back(caplog):
    # DEFAULT_PROVIDER=xai but no xai key: must not raise, must land on a
    # provider that is actually configured, and must log a warning so the
    # mismatch is visible in server logs.
    settings = make_settings(default_provider="xai", xai_api_key="", groq_api_key="")

    with caplog.at_level(logging.WARNING):
        router = _default_router_factory(settings)

    assert router.current_provider == "anthropic"
    assert any("DEFAULT_PROVIDER" in r.message for r in caplog.records)


def test_default_provider_already_configured_is_used_as_is(caplog):
    settings = make_settings(default_provider="groq")

    with caplog.at_level(logging.WARNING):
        router = _default_router_factory(settings)

    assert router.current_provider == "groq"
    assert not caplog.records


def test_a_session_still_opens_with_no_provider_configured(caplog):
    settings = make_settings(anthropic_api_key="", groq_api_key="", xai_api_key="")

    with caplog.at_level(logging.WARNING):
        router = _default_router_factory(settings)

    assert router.current_provider is None
    assert any("no translation provider is configured" in r.message for r in caplog.records)


async def test_translating_with_no_provider_configured_fails_per_call_not_at_construction():
    settings = make_settings(anthropic_api_key="", groq_api_key="", xai_api_key="")
    router = _default_router_factory(settings)

    with pytest.raises(RuntimeError):
        await router.translate("hello", [])
