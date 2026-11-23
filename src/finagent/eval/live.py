"""The live model run (plan 2.1): FIN-001 and FIN-003, once per configuration. Costs real money."""

import statistics
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from finagent.eval.report import store_payload
from finagent.eval.scenarios import AS_OF
from finagent.llm.anthropic_adapter import ModelConfig, load_model_config
from finagent.llm.client import LLMClient, RationaleRequest, RationaleResponse
from finagent.llm.fake import FakeLLM
from finagent.llm.prompt import PromptChunk, RationaleInput
from finagent.llm.router import Router
from finagent.llm.validator import has_forbidden_claim, store_raw
from finagent.orchestration.graph import RunState
from finagent.orchestration.nodes import rationale_chunks, rationale_input
from finagent.orchestration.runner import ROOT
from finagent.persistence.db import connect
from finagent.persistence.repos import CheckpointRepo
from finagent.service.run_service import CASES_DIR, RunService, StartCommand

LIVE_CASES = ("FIN-001", "FIN-003")
CONFIGS = frozenset({"template", "haiku"})
LIVE_LABEL = "live model"
RESULTS_PATH = ROOT / "docs" / "EVAL_RESULTS.md"
_BAD_STOPS = frozenset({"max_tokens", "refusal"})

Prepared = tuple[RationaleInput, Sequence[PromptChunk]]


class RecordingLLM:
    """Keeps every raw reply and its latency, before any validation."""

    provider = "anthropic"

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.replies: list[RationaleResponse] = []
        self.latencies_ms: list[float] = []

    def generate_rationale(self, req: RationaleRequest) -> RationaleResponse:
        started = time.perf_counter()
        response = self._inner.generate_rationale(req)
        self.latencies_ms.append((time.perf_counter() - started) * 1000)
        self.replies.append(response)
        return response


def prepare_inputs(case_id: str) -> Prepared:
    """The decided case, reached offline, so every configuration explains the same decision."""
    with tempfile.TemporaryDirectory(prefix="finagent-live-") as directory:
        home = Path(directory)
        run = RunService(home, FakeLLM).start(StartCommand(case_dir=CASES_DIR / case_id, as_of=AS_OF))
        conn = connect(home / "finagent.db")
        try:
            state = RunState.from_json(CheckpointRepo(conn).latest(run.run_id)[2])
        finally:
            conn.close()
    return rationale_input(state), rationale_chunks(state)


def exception_coverage(evaluation: RationaleInput, text: str) -> tuple[int, int]:
    """How many exceptions the rationale mentions, by rule ID or by category in words."""
    lowered = text.casefold()
    covered = sum(
        1
        for item in evaluation.exceptions
        if item.failed_rule.casefold() in lowered
        or str(item.category).replace("_", " ").casefold() in lowered
        or str(item.category).casefold() in lowered
    )
    return covered, len(evaluation.exceptions)


def _usage(recorder: RecordingLLM | None, config: ModelConfig) -> dict[str, Any]:
    replies = recorder.replies if recorder else []
    costs = [config.cost(reply.model, reply.usage) for reply in replies]
    return {
        "input_tokens": sum(reply.usage.input_tokens for reply in replies),
        "output_tokens": sum(reply.usage.output_tokens for reply in replies),
        "cost_usd": "unpriced" if None in costs else str(sum(costs, Decimal(0))),
    }


def _run_one(
    case_id: str,
    name: str,
    prepared: Prepared,
    config: ModelConfig,
    llm_factory: Callable[[], LLMClient],
    raw_dir: Path,
) -> dict[str, Any]:
    evaluation, chunks = prepared
    recorder = RecordingLLM(llm_factory()) if name == "haiku" else None
    source = "llm" if recorder else "template"
    router = Router(recorder or FakeLLM("good"), source, config)
    result = router.rationale(evaluation, chunks)
    replies = recorder.replies if recorder else []
    for hop, reply in enumerate(replies, 1):
        store_raw(raw_dir / f"{case_id}-{name}-{hop}.txt", reply.raw_text)
    shown = " ".join(claim.text for claim in result.rationale.claims)
    raw_texts = [reply.raw_text for reply in replies] or [shown]
    covered, total = exception_coverage(evaluation, shown)
    latencies = recorder.latencies_ms if recorder else []
    return {
        "case_id": case_id,
        "config": name,
        "source": result.source,
        "model_calls": result.calls,
        "first_try_valid": result.source == "LLM" and result.calls == 1,
        "raw_forbidden_claim": any(has_forbidden_claim(text) for text in raw_texts),  # before validation
        "truncated_or_refused": any(reply.stop_reason in _BAD_STOPS for reply in replies),
        "stop_reasons": result.stop_reasons,
        "exceptions_covered": covered,
        "exceptions_total": total,
        **_usage(recorder, config),
        "latency_ms": latencies,
        "manual_read": "",
    }


def render_model_section(rows: Sequence[dict[str, Any]], generated_at: datetime) -> str:
    latencies = [value for row in rows for value in row["latency_ms"]]
    timing = (
        f"p50 {statistics.median(latencies):.0f} ms, max {max(latencies):.0f} ms (indicative, small sample)"
        if latencies
        else "no model calls"
    )
    lines = [
        "## Model",
        "",
        f"Live run at {generated_at.isoformat()}.",
        "One run per input and configuration; no confidence intervals.",
        "Gate inputs are computed on the raw output, before validation.",
        "",
        "| Case | Config | Shown | First-try valid | Raw forbidden claim | Truncated or refused "
        "| Exceptions covered | Input tokens | Output tokens | Cost (USD) | Manual read |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['case_id']} | {row['config']} | {row['source']} | {row['first_try_valid']} "
            f"| {row['raw_forbidden_claim']} | {row['truncated_or_refused']} "
            f"| {row['exceptions_covered']}/{row['exceptions_total']} | {row['input_tokens']} "
            f"| {row['output_tokens']} | {row['cost_usd']} | {row['manual_read']} |"
        )
    return "\n".join([*lines, "", f"Latency: {timing}.", ""])


def write_model_section(path: Path, section: str) -> None:
    """Replace only the Model section. Every other section is left byte for byte."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Evaluation results\n\n{section}", encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    start = 0 if text.startswith("## Model") else text.find("\n## Model")
    if start == -1:
        path.write_text(f"{text.rstrip()}\n\n{section}", encoding="utf-8")
        return
    begin = start if text.startswith("## Model") else start + 1
    following = text.find("\n## ", begin + 1)
    tail = text[following + 1 :] if following != -1 else ""
    separator = "\n" if tail and not section.endswith("\n\n") else ""
    path.write_text(f"{text[:begin]}{section}{separator}{tail}", encoding="utf-8")


def run_live(
    configs: Sequence[str],
    llm_factory: Callable[[], LLMClient],
    home: Path,
    generated_at: datetime,
    prepare: Callable[[str], Prepared] = prepare_inputs,
    results_path: Path = RESULTS_PATH,
    config_path: Path = ROOT / "config/model.yaml",
) -> tuple[dict[str, Any], Path]:
    unknown = set(configs) - CONFIGS
    if unknown or not configs:
        raise ValueError(f"configs must be chosen from {sorted(CONFIGS)}")
    config = load_model_config(config_path)
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    raw_dir = Path(home) / "eval" / f"live-raw-{stamp}"
    rows = [
        _run_one(case_id, name, prepare(case_id), config, llm_factory, raw_dir)
        for name in configs
        for case_id in LIVE_CASES
    ]
    payload = {
        "kind": "live",
        "label": LIVE_LABEL,
        "generated_at": generated_at.isoformat(),
        "model": config.primary,
        "runs": rows,
    }
    path = store_payload(payload, home, generated_at, prefix="live-")
    write_model_section(results_path, render_model_section(rows, generated_at))
    return payload, path
