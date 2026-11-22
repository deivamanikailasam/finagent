import pytest

from finagent.config.settings import Settings, SettingsError, load_settings


def test_missing_hmac_key_fails_fast(monkeypatch):
    monkeypatch.delenv("FINAGENT_HMAC_KEY")
    with pytest.raises(SettingsError, match="FINAGENT_HMAC_KEY"):
        load_settings()


def test_blank_hmac_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv("FINAGENT_HMAC_KEY", "")
    with pytest.raises(SettingsError, match="FINAGENT_HMAC_KEY"):
        load_settings()


@pytest.mark.parametrize("bad_key", ["ab" * 31 + "a", "z" * 64])
def test_short_or_non_hex_key_fails_without_echoing_it(monkeypatch, bad_key):
    monkeypatch.setenv("FINAGENT_HMAC_KEY", bad_key)
    with pytest.raises(SettingsError) as caught:
        load_settings()
    assert "64 hex" in str(caught.value)
    assert bad_key not in str(caught.value)


def test_valid_key_is_hidden_in_repr():
    settings = load_settings()
    assert settings.hmac_key.get_secret_value() == "0" * 64
    assert "0" * 64 not in repr(settings)


def test_no_env_file_is_configured_or_read(monkeypatch, tmp_path):
    monkeypatch.delenv("FINAGENT_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FINAGENT_HOME=/from-env-file\nANTHROPIC_API_KEY=from-env-file\n")
    settings = load_settings()
    assert Settings.model_config.get("env_file") is None
    assert str(settings.home) == "var"
    assert settings.anthropic_api_key is None


def test_anthropic_key_is_read_without_prefix(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-value")
    assert load_settings().anthropic_api_key.get_secret_value() == "placeholder-value"


def test_prefixed_anthropic_key_is_ignored(monkeypatch):
    monkeypatch.setenv("FINAGENT_ANTHROPIC_API_KEY", "placeholder-value")
    assert load_settings().anthropic_api_key is None


def test_other_timezone_is_rejected(monkeypatch):
    monkeypatch.setenv("FINAGENT_TIMEZONE", "UTC")
    with pytest.raises(SettingsError, match="FINAGENT_TIMEZONE"):
        load_settings()
