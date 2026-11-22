"""Runtime settings, read from environment variables only."""

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HMAC_KEY = re.compile(r"[0-9a-fA-F]{64,}")


class SettingsError(RuntimeError):
    """Configuration is invalid. The message never contains a configured value."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINAGENT_",
        extra="forbid",
        env_ignore_empty=True,  # a blank line copied from .env.example counts as unset
        hide_input_in_errors=True,  # a rejected key must not appear in a traceback
    )

    home: Path = Path("./var")
    hmac_key: SecretStr
    anthropic_api_key: SecretStr | None = Field(None, validation_alias="ANTHROPIC_API_KEY")
    model_config_path: Path = Path("config/model.yaml")
    tools_config_path: Path = Path("config/tools.yaml")
    retrieval_config_path: Path = Path("config/retrieval.yaml")
    timezone: Literal["Australia/Sydney"] = "Australia/Sydney"  # the control dates are defined on this zone

    @field_validator("hmac_key")
    @classmethod
    def _hmac_key_is_long_hex(cls, value: SecretStr) -> SecretStr:
        if not _HMAC_KEY.fullmatch(value.get_secret_value()):
            raise ValueError("must be at least 64 hex characters")
        return value


def _env_name(location: tuple[int | str, ...]) -> str:
    name = str(location[0]) if location else "settings"
    return name if name == "ANTHROPIC_API_KEY" else f"FINAGENT_{name.upper()}"


def load_settings() -> Settings:
    """Load settings or fail fast with a readable message that holds no values."""
    try:
        return Settings()
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        problems = "; ".join(f"{_env_name(error['loc'])}: {error['msg']}" for error in errors)
        raise SettingsError(f"Invalid configuration: {problems}") from None
