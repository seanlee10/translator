from pathlib import Path

from app.config import ENV_FILES, REPO_ROOT, Settings

KEY_VARS = ("DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY")


def make(**overrides) -> Settings:
    base = dict(deepgram_api_key="d", anthropic_api_key="a", groq_api_key="g")
    base.update(overrides)
    # _env_file=None keeps the suite hermetic: Settings now resolves a real
    # repo-root .env, and a developer's local one must not change defaults.
    return Settings(_env_file=None, **base)


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


# ---- .env file resolution ----
#
# Every other test in the suite builds Settings from literal keyword
# arguments, so none of them ever touch the env-file machinery. That is
# exactly how a CWD-relative env_file shipped: README documents `.env` at
# the repo root and `cd backend && uvicorn ...`, so a bare ".env" resolved
# to backend/.env and no key was ever found. These tests exercise the
# resolution path itself.


def test_env_files_resolve_against_the_source_tree_not_the_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # a CWD with no .env anywhere near it

    repo_root = Path(__file__).resolve().parents[2]

    # Sanity-check the anchor: these files only exist at the real repo root,
    # so if REPO_ROOT ever drifts, this fails rather than silently passing.
    assert (repo_root / "README.md").is_file()
    assert (repo_root / "backend").is_dir()

    assert REPO_ROOT == repo_root
    assert ENV_FILES[0] == repo_root / ".env"
    assert ENV_FILES[1] == repo_root / "backend" / ".env"
    assert all(path.is_absolute() for path in ENV_FILES)


def test_settings_load_their_values_from_an_env_file(tmp_path, monkeypatch):
    for var in (*KEY_VARS, "DEBOUNCE_MS"):
        monkeypatch.delenv(var, raising=False)

    env = tmp_path / ".env"
    env.write_text(
        "DEEPGRAM_API_KEY=dg-from-file\n"
        "ANTHROPIC_API_KEY=an-from-file\n"
        "GROQ_API_KEY=gq-from-file\n"
        "DEBOUNCE_MS=123\n",
        encoding="utf-8",
    )

    s = Settings(_env_file=env)

    assert s.deepgram_api_key == "dg-from-file"
    assert s.anthropic_api_key == "an-from-file"
    assert s.groq_api_key == "gq-from-file"
    assert s.debounce_ms == 123


def test_a_backend_env_file_wins_over_the_repo_root_one(tmp_path, monkeypatch):
    # Pins the "later entries win" ordering ENV_FILES relies on, so both
    # documented layouts work and the backend copy is the override.
    for var in (*KEY_VARS, "DEBOUNCE_MS"):
        monkeypatch.delenv(var, raising=False)

    root_env = tmp_path / ".env"
    root_env.write_text(
        "DEEPGRAM_API_KEY=dg-root\n"
        "ANTHROPIC_API_KEY=an-root\n"
        "GROQ_API_KEY=gq-root\n"
        "DEBOUNCE_MS=111\n",
        encoding="utf-8",
    )
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    backend_env = backend_dir / ".env"
    backend_env.write_text("DEBOUNCE_MS=222\n", encoding="utf-8")

    s = Settings(_env_file=(root_env, backend_env))

    assert s.debounce_ms == 222  # backend/.env overrides
    assert s.deepgram_api_key == "dg-root"  # keys still come from the root file


def test_a_missing_env_file_is_not_an_error_when_the_environment_supplies_keys(
    tmp_path, monkeypatch
):
    # The normal shape of a deployment that exports real environment
    # variables instead of keeping a file. Neither configured path exists.
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-env")
    monkeypatch.setenv("GROQ_API_KEY", "gq-env")

    s = Settings(_env_file=(tmp_path / ".env", tmp_path / "backend" / ".env"))

    assert s.deepgram_api_key == "dg-env"
