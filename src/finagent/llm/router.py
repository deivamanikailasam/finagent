"""Primary model, one repair, then the deterministic template. No path can change the outcome."""

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from finagent.llm.anthropic_adapter import ModelConfig
from finagent.llm.client import Claim, LLMClient, Rationale, RationaleRequest, RationaleResponse
from finagent.llm.prompt import (
    SYSTEM,
    PromptChunk,
    RationaleInput,
    allowed_citations,
    build_prompt,
    prompt_hash,
)

MAX_MODEL_CALLS = 2  # the first call and one repair


@dataclass(frozen=True)
class RouterResult:
    source: str  # LLM or TEMPLATE
    rationale: Rationale
    prompt_hash: str
    model: str | None
    calls: int
    stop_reasons: dict[str, int]


def template_rationale(evaluation: RationaleInput) -> Rationale:
    """A deterministic explanation that mentions every exception and unknown."""
    claims = [Claim(text=f"Recommended {evaluation.outcome}. {evaluation.next_action}.")]
    claims += [
        Claim(
            text=f"Exception {item.category} from rule {item.failed_rule}, owner {item.owner}.",
            citation_ids=item.citations,
        )
        for item in evaluation.exceptions
    ]
    claims += [Claim(text=f"Unknown: {item.source} ({item.reason}).") for item in evaluation.unknowns]
    return Rationale(claims=tuple(claims))


def _problem(response: RationaleResponse) -> str | None:
    if response.stop_reason != "end_turn":
        return f"stop:{response.stop_reason}"
    return None if response.parsed is not None else "malformed_json"


class Router:
    def __init__(
        self,
        primary: LLMClient,
        rationale_source: str,
        config: ModelConfig | None = None,
        audit: Callable[..., None] | None = None,
    ) -> None:
        self._primary = primary
        self._source = rationale_source
        self._config = config
        self._audit = audit or (lambda *_args, **_kwargs: None)

    def _record(self, hop: int, digest: str, response: RationaleResponse, outcome: str) -> None:
        cost = self._config.cost(response.model, response.usage) if self._config else None
        self._audit(
            outcome,
            hop=hop,
            provider=getattr(self._primary, "provider", "fake"),
            model=response.model,
            response_id=response.response_id,
            region=response.region,
            prompt_hash=digest,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=str(cost) if cost is not None else "unpriced",
        )

    def rationale(self, evaluation: RationaleInput, chunks: Sequence[PromptChunk] = ()) -> RouterResult:
        prompt = build_prompt(evaluation, chunks)
        digest = prompt_hash(prompt)
        stops: Counter[str] = Counter()
        model: str | None = None
        calls = 0
        errors: tuple[str, ...] = ()
        if self._source != "template":
            allowed = allowed_citations(evaluation, chunks)
            for hop in range(1, MAX_MODEL_CALLS + 1):
                request = RationaleRequest(
                    system=SYSTEM, prompt=prompt, allowed_citations=allowed, repair_errors=errors
                )
                calls += 1
                try:
                    response = self._primary.generate_rationale(request)
                except Exception as exc:  # a provider failure cannot be repaired by asking again
                    self._audit("error", hop=hop, prompt_hash=digest, error=type(exc).__name__)
                    break
                model = response.model
                stops[str(response.stop_reason)] += 1
                problem = _problem(response)
                self._record(hop, digest, response, problem or "ok")
                if problem is None:
                    return RouterResult("LLM", response.parsed, digest, model, calls, dict(stops))
                errors = (problem,)
        return RouterResult("TEMPLATE", template_rationale(evaluation), digest, model, calls, dict(stops))
