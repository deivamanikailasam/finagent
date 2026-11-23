"""The Anthropic implementation of LLMClient. It returns text for our code to validate."""

from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import anthropic
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from finagent.config.settings import load_settings
from finagent.llm.client import Rationale, RationaleRequest, RationaleResponse, Usage

RATIONALE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "citation_ids", "number_refs"],
                "properties": {
                    "text": {"type": "string"},
                    "citation_ids": {"type": "array", "items": {"type": "string"}},
                    "number_refs": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}
_MILLION = Decimal(1_000_000)


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input: Decimal = Field(ge=0)
    output: Decimal = Field(ge=0)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    primary: str = Field(min_length=1)
    effort: Literal["low", "medium", "high", "xhigh", "max"]
    thinking: Literal["adaptive"]
    max_tokens: int = Field(gt=0)
    timeout_s: float = Field(gt=0, allow_inf_nan=False)
    rationale_source: Literal["llm", "template"] = "llm"
    prices_per_mtok: dict[str, Price | None] = Field(default_factory=dict)

    def cost(self, model: str, usage: Usage) -> Decimal | None:
        """The call's cost in USD, or None while the model has no checked price."""
        price = self.prices_per_mtok.get(model)
        if price is None:
            return None
        return (usage.input_tokens * price.input + usage.output_tokens * price.output) / _MILLION


def load_model_config(path: Path) -> ModelConfig:
    return ModelConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def build_request(config: ModelConfig, req: RationaleRequest) -> dict[str, Any]:
    """The whole request body. It never holds sampling settings, tool_choice or an assistant message."""
    content = req.prompt
    if req.repair_errors:
        content += (
            "\n\nYour previous reply was rejected for: "
            + ", ".join(req.repair_errors)
            + ". Return corrected JSON that follows every rule."
        )
    return {
        "model": config.primary,
        "max_tokens": config.max_tokens,
        "system": req.system,
        "messages": [{"role": "user", "content": content}],
        "thinking": {"type": config.thinking},
        "output_config": {
            "effort": config.effort,
            "format": {"type": "json_schema", "schema": RATIONALE_SCHEMA},
        },
    }


def _parse(text: str, stop_reason: str | None) -> Rationale | None:
    if stop_reason != "end_turn":  # a truncated or refused reply is malformed, whatever it contains
        return None
    try:
        return Rationale.model_validate_json(text)
    except ValidationError:
        return None


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, config: ModelConfig, client: Any = None) -> None:
        self._config = config
        if client is None:
            key = load_settings().anthropic_api_key
            client = anthropic.Anthropic(
                api_key=key.get_secret_value() if key else None, timeout=config.timeout_s
            )
        self._client = client

    def generate_rationale(self, req: RationaleRequest) -> RationaleResponse:
        message = self._client.messages.create(**build_request(self._config, req))
        text = "".join(block.text for block in message.content if block.type == "text")
        return RationaleResponse(
            raw_text=text,
            parsed=_parse(text, message.stop_reason),
            model=message.model,
            usage=Usage(input_tokens=message.usage.input_tokens, output_tokens=message.usage.output_tokens),
            stop_reason=message.stop_reason,
            response_id=getattr(message, "_request_id", None) or message.id,
            region=getattr(message.usage, "inference_geo", None),
        )
