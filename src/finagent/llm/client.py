"""The LLM boundary. A model can only return a rationale; it never sets an outcome, amount or approval."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    citation_ids: tuple[str, ...] = ()
    number_refs: tuple[str, ...] = ()


class Rationale(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: tuple[Claim, ...] = Field(min_length=1)  # an empty rationale explains nothing


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class RationaleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    system: str
    prompt: str
    allowed_citations: tuple[str, ...] = ()
    repair_errors: tuple[str, ...] = ()


class RationaleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_text: str  # kept even when parsing fails, so a malformed reply can be audited
    parsed: Rationale | None
    model: str
    usage: Usage = Usage()
    stop_reason: str | None = None
    response_id: str | None = None  # provider request ID, kept for the audit trail (FIN-POL-010 s4)


class LLMClient(Protocol):
    def generate_rationale(self, req: RationaleRequest) -> RationaleResponse: ...
