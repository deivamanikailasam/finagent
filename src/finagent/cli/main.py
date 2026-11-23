"""The finagent command line. Commands print JSON on stdout; logs go to stderr."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel

from finagent.config.settings import SettingsError, load_settings
from finagent.eval.retrieval_eval import render_report, run_retrieval_eval
from finagent.obs.log import configure_logging
from finagent.orchestration.runner import ROOT
from finagent.rag.loader import IndexIntegrityError, ingest, persist_manifest, trust_report
from finagent.service.run_service import (
    CASES_DIR,
    DecisionCommand,
    RunService,
    RunServiceError,
    StartCommand,
)

app = typer.Typer(no_args_is_help=True, add_completion=False)
run_app = typer.Typer(no_args_is_help=True)
eval_app = typer.Typer()
app.add_typer(run_app, name="run", help="Start, inspect and decide runs.")
app.add_typer(eval_app, name="eval", help="Run evaluations.")

JsonFlag = Annotated[bool, typer.Option("--json", help="Compact JSON on one line.")]
ApprovalIdOption = Annotated[
    str | None, typer.Option("--approval-id", help="Required when an earlier request was replaced.")
]


@app.callback()
def _startup() -> None:
    configure_logging()


def _emit(value: Any, compact: bool) -> None:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    typer.echo(json.dumps(data, sort_keys=True, indent=None if compact else 2))


def _fail(message: str) -> typer.Exit:
    typer.echo(json.dumps({"error": message}), err=True)
    return typer.Exit(code=2)


def _service() -> RunService:
    try:
        return RunService()
    except SettingsError as exc:
        raise _fail(str(exc)) from None


@app.command("ingest")
def ingest_command() -> None:
    """Verify the corpus and record its index manifest."""
    result = ingest(ROOT / "corpus", ROOT / "config/registry.yaml")
    typer.echo(trust_report(result))
    try:
        home = load_settings().home
        persist_manifest(result, Path(home) / "index_manifest.json")
    except (IndexIntegrityError, SettingsError) as exc:
        raise _fail(str(exc)) from None


@run_app.command("start")
def run_start(
    case: Annotated[Path | None, typer.Option("--case", help="A case directory.")] = None,
    request: Annotated[Path | None, typer.Option("--request", help="A request JSON file.")] = None,
    as_of: Annotated[str | None, typer.Option("--as-of", help="ISO time with a UTC offset.")] = None,
    compact: JsonFlag = False,
) -> None:
    """Run a case to its approval pause or a terminal state."""
    try:
        when = datetime.fromisoformat(as_of) if as_of else None
        if when is not None and when.utcoffset() is None:
            raise ValueError
    except ValueError:
        raise _fail("--as-of must be an ISO time with a UTC offset") from None
    try:
        command = StartCommand(case_dir=case, request_path=request, as_of=when)
        _emit(_service().start(command), compact)
    except RunServiceError as exc:
        raise _fail(str(exc)) from None


@run_app.command("get")
def run_get(
    run_id: str,
    audit: Annotated[bool, typer.Option("--audit/--no-audit", help="Include audit events.")] = True,
    compact: JsonFlag = False,
) -> None:
    """Show a run's status, current node, result and audit events."""
    try:
        detail = _service().get(run_id, include_audit=audit)
    except RunServiceError as exc:
        raise _fail(str(exc)) from None
    data = detail.model_dump(mode="json")
    if not audit:
        data.pop("audit_events")
    _emit(data, compact)


def _finish_decision(response: Any, compact: bool) -> None:
    _emit(response, compact)
    if not response.applied:  # for example an ineligible approver: the run stays paused
        raise typer.Exit(code=1)


@run_app.command("approve")
def run_approve(
    run_id: str,
    approver: Annotated[str, typer.Option("--approver", help="The approver's ID.")],
    approval_id: ApprovalIdOption = None,
    compact: JsonFlag = False,
) -> None:
    """Approve the pending request and continue the same run."""
    try:
        command = DecisionCommand(run_id=run_id, approver=approver, approval_id=approval_id)
        response = _service().approve(command)
    except RunServiceError as exc:
        raise _fail(str(exc)) from None
    _finish_decision(response, compact)


@run_app.command("reject")
def run_reject(
    run_id: str,
    approver: Annotated[str, typer.Option("--approver", help="The approver's ID.")],
    reason: Annotated[str, typer.Option("--reason", help="Why it is rejected.")],
    approval_id: ApprovalIdOption = None,
    compact: JsonFlag = False,
) -> None:
    """Reject the pending request and continue the same run."""
    try:
        command = DecisionCommand(run_id=run_id, approver=approver, reason=reason, approval_id=approval_id)
        response = _service().reject(command)
    except RunServiceError as exc:
        raise _fail(str(exc)) from None
    _finish_decision(response, compact)


@run_app.command("resume")
def run_resume(run_id: str, compact: JsonFlag = False) -> None:
    """Crash recovery only. A paused or finished run is returned unchanged."""
    try:
        _emit(_service().resume(run_id), compact)
    except RunServiceError as exc:
        raise _fail(str(exc)) from None


@eval_app.callback(invoke_without_command=True)
def eval_command(
    context: typer.Context,
    retrieval: Annotated[bool, typer.Option("--retrieval", help="Run the retrieval evaluation.")] = False,
    live: Annotated[bool, typer.Option("--live", help="Use the real model (WP 8.2).")] = False,
    configs: Annotated[str | None, typer.Option("--configs", help="Live configurations (WP 8.2).")] = None,
) -> None:
    """Run an evaluation."""
    if context.invoked_subcommand is not None:
        return
    if live or configs:
        raise _fail("the live evaluation is not available until WP 8.2")
    if not retrieval:
        raise _fail("the scenario evaluation is not available until WP 8.2; use --retrieval")
    os.chdir(ROOT)  # the retrieval evaluation reads and writes project-relative files
    typer.echo(render_report(run_retrieval_eval(ROOT)))


@eval_app.command("list")
def eval_list(compact: JsonFlag = False) -> None:
    """List the evaluations and cases."""
    cases = sorted(path.name for path in CASES_DIR.iterdir() if (path / "meta.json").is_file())
    evals = [
        {"name": "retrieval", "available": True},
        {"name": "scenarios", "available": False},
        {"name": "live", "available": False},
    ]
    _emit({"evals": evals, "cases": cases}, compact)
