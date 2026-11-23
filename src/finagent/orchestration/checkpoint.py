"""Restores a run from its last checkpoint and records what the run was decided against."""

import hashlib
from pathlib import Path
from typing import Any

from finagent.orchestration.graph import PAUSED, TERMINAL, RunState
from finagent.persistence.repos import CheckpointRepo

ROOT = Path(__file__).resolve().parents[3]
_CONFIG_FILES = (
    "config/fraud_indicators.yaml",
    "config/retrieval.yaml",
    "config/tools.yaml",
    "spec/graph.yaml",
    "spec/submit_permissions.yaml",
)


def _digest(paths: list[Path]) -> str:
    combined = hashlib.sha256()
    for path in paths:
        combined.update(path.relative_to(ROOT).as_posix().encode())
        combined.update(hashlib.sha256(path.read_bytes()).digest())
    return combined.hexdigest()


def fingerprints() -> tuple[str, str]:
    """The combined corpus, registry and rules hash, and the config hash. Recorded, never compared."""
    corpus = [
        path
        for path in sorted((ROOT / "corpus").rglob("*"))
        if path.is_file() and not path.name.startswith(".")  # OS metadata files are not corpus
    ]
    sources = [*corpus, ROOT / "config/registry.yaml", ROOT / "spec/policy_rules.yaml"]
    return _digest(sources), _digest([ROOT / name for name in _CONFIG_FILES])


def restore(checkpoints: CheckpointRepo, run_id: str) -> RunState:
    """The last saved state. Results recorded before it are reused, so those nodes never run again."""
    saved = checkpoints.latest(run_id)
    if saved is None:
        raise KeyError(f"unknown run: {run_id}")
    state = RunState.from_json(saved[2])
    interrupted = state.node not in TERMINAL and state.signal != PAUSED
    if interrupted and state.visited and state.visited[-1] == state.node:
        state.visited.pop()  # saved just before a block write; the node runs again and is counted once
    return state


def resume(run_id: str, deps: Any) -> Any:
    """Continue a run after a restart. REVALIDATE, PLACE_BLOCK and SUBMIT always run live."""
    from finagent.orchestration.runner import Runner  # the runner imports this module

    return Runner(deps).resume(run_id)
