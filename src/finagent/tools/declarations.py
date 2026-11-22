"""Typed view of config/tools.yaml."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    purpose: str
    access: Literal["read", "write"]
    timeout_s: float = Field(gt=0)
    max_attempts: int = Field(ge=1)
    retry_on: tuple[str, ...] = ()
    impl: Literal["real", "mock", "simulated"]

    @model_validator(mode="after")
    def _write_tools_are_never_retried(self) -> "ToolDeclaration":
        # A blind retry of a write could commit twice; recovery goes through idempotency reconciliation.
        if self.access == "write" and (self.max_attempts != 1 or self.retry_on):
            raise ValueError(f"write tool {self.name} must have max_attempts 1 and no retry_on")
        return self


class Backoff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_s: float = Field(gt=0)
    multiplier: float = Field(ge=1)
    max_s: float = Field(gt=0)
    jitter: bool


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    unit: Literal["tool_invocation"]
    retry_attempts_counted: Literal[False]


class ToolsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tools: tuple[ToolDeclaration, ...]
    backoff: Backoff
    budget: Budget


def load_tool_declarations(path: Path) -> ToolsConfig:
    with path.open(encoding="utf-8") as handle:
        return ToolsConfig.model_validate(yaml.safe_load(handle))
