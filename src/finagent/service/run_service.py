"""The application boundary for runs. Approve and reject only decide, then resume the same run."""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from finagent.config.clock import FixedClock
from finagent.config.settings import load_settings
from finagent.domain.requests import ProcessingRequest
from finagent.domain.results import FinalResult
from finagent.llm.client import LLMClient
from finagent.llm.fake import FakeLLM
from finagent.orchestration.checkpoint import restore
from finagent.orchestration.checkpoint import resume as resume_run
from finagent.orchestration.runner import ROOT, Runner, RunView, build_deps
from finagent.persistence.db import connect
from finagent.persistence.repos import AuditRepo, CheckpointRepo, RunRepo

CASES_DIR = ROOT / "fixtures" / "cases"
APPLIED_CODES = frozenset({"APPROVED", "REJECTED"})


class RunServiceError(Exception):
    """A request the service refused. The message is safe to show to the caller."""


class _Dto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StartCommand(_Dto):
    case_dir: Path | None = None
    request_path: Path | None = None
    as_of: AwareDatetime | None = None


class DecisionCommand(_Dto):
    run_id: str = Field(min_length=1)
    approver: str = Field(min_length=1)
    reason: str | None = None
    approval_id: str | None = None


class RunSummary(_Dto):
    run_id: str
    status: str
    current_node: str
    outcome: str | None
    primary_category: str | None
    pending_approval_id: str | None
    result: FinalResult | None


class RunDetail(_Dto):
    run_id: str
    status: str
    current_node: str
    result: FinalResult | None
    audit_events: list[dict[str, Any]] | None = None


class DecisionResponse(_Dto):
    run_id: str
    code: str
    applied: bool
    approval_id: str
    status: str
    current_node: str
    outcome: str | None
    pending_approval_id: str | None
    result: FinalResult | None


def _summary(view: RunView) -> RunSummary:
    return RunSummary(
        run_id=view.run_id,
        status=view.status,
        current_node=view.current_node,
        outcome=view.outcome,
        primary_category=view.primary_category,
        pending_approval_id=view.pending_approval_id,
        result=view.result,
    )


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    return "; ".join(f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in errors)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunServiceError(f"cannot read {path.name}") from exc


def _require_reviewed_request(command: DecisionCommand, state: Any, status: str) -> None:
    """A repeated command must never decide a replacement request the approver has not seen."""
    if command.approval_id is not None and command.approval_id != state.approval_id:
        raise RunServiceError(f"the current approval request is {state.approval_id}")
    requests = sum(item["action"] == "approval_request" for item in state.actions)
    if status == "PENDING" and requests > 1 and command.approval_id is None:
        raise RunServiceError(
            f"the earlier request was replaced; review {state.approval_id} and pass it as --approval-id"
        )


class RunService:
    def __init__(self, home: Path | None = None, llm_factory: Callable[[], LLMClient] = FakeLLM) -> None:
        self._home = Path(home) if home is not None else load_settings().home
        self._llm_factory = llm_factory  # the fake model is the default everywhere (decision log I4)

    @property
    def _app_db(self) -> Path:
        return self._home / "finagent.db"

    def _resolve_start(self, command: StartCommand) -> tuple[Path, dict[str, Any] | None]:
        if (command.case_dir is None) == (command.request_path is None):
            raise RunServiceError("give exactly one of --case or --request")
        if command.case_dir is not None:
            case_dir = command.case_dir.resolve()
            data, override = _read_json(case_dir / "request.json"), None
        else:
            data = _read_json(command.request_path)
            override = data
        try:
            request = ProcessingRequest.model_validate(data)
        except ValidationError as exc:
            raise RunServiceError(_validation_message(exc)) from None
        if override is not None:
            case_dir = CASES_DIR / request.case_id  # the validated pattern cannot leave this directory
            override = request.model_dump(mode="json")
        if not (case_dir / "meta.json").is_file():
            raise RunServiceError(f"no case records for {request.case_id}")
        if case_dir.name != request.case_id:  # tools are scoped by this ID, so the two must agree
            raise RunServiceError("the request's case_id does not match the case directory")
        return case_dir, override

    def start(self, command: StartCommand) -> RunSummary:
        case_dir, override = self._resolve_start(command)
        as_of = command.as_of or datetime.fromisoformat(_read_json(case_dir / "meta.json")["as_of"])
        deps = build_deps(case_dir, self._home, FixedClock(as_of), self._llm_factory(), request=override)
        try:
            return _summary(Runner(deps).start())
        finally:
            deps.conn.close()

    def _existing(self, run_id: str) -> tuple[Any, Any]:
        if not self._app_db.is_file():
            raise RunServiceError(f"unknown run: {run_id}")
        conn = connect(self._app_db)
        try:
            row = RunRepo(conn).get(run_id)
            if row is None:
                raise RunServiceError(f"unknown run: {run_id}")
            return row, restore(CheckpointRepo(conn), run_id)
        finally:
            conn.close()

    def _deps_for(self, run_id: str) -> tuple[Any, Any]:
        """Rebuild dependencies for a run, at the date the run was decided against."""
        row, state = self._existing(run_id)
        clock = FixedClock(row.as_of)
        deps = build_deps(Path(state.case_dir), self._home, clock, self._llm_factory(), request=state.request)
        return deps, state

    def get(self, run_id: str, include_audit: bool = True) -> RunDetail:
        row, _ = self._existing(run_id)
        conn = connect(self._app_db)
        try:
            events = AuditRepo(conn).events(run_id) if include_audit else None
        finally:
            conn.close()
        result = FinalResult.model_validate_json(row.result_json) if row.result_json else None
        return RunDetail(
            run_id=row.run_id,
            status=row.status,
            current_node=row.current_node,
            result=result,
            audit_events=events,
        )

    def _decide(self, command: DecisionCommand, decision: str) -> DecisionResponse:
        deps, state = self._deps_for(command.run_id)
        try:
            if state.approval_id is None:
                raise RunServiceError("this run has no approval request")
            record = deps.approvals.get(state.approval_id)
            if record is None or record.run_id != command.run_id:
                # The run that created the request owns the decision and is the one that continues.
                owner = record.run_id if record is not None else "unknown"
                raise RunServiceError(f"this approval belongs to run {owner}; decide on that run")
            _require_reviewed_request(command, state, record.status)
            response = deps.approvals.decide(state.approval_id, command.approver, decision, command.reason)
            view = Runner(deps).resume(command.run_id)
        finally:
            deps.conn.close()
        return DecisionResponse(
            run_id=view.run_id,
            code=response.code,
            applied=response.code in APPLIED_CODES,
            approval_id=response.approval_id,
            status=view.status,
            current_node=view.current_node,
            outcome=view.outcome,
            pending_approval_id=view.pending_approval_id,
            result=view.result,
        )

    def approve(self, command: DecisionCommand) -> DecisionResponse:
        return self._decide(command, "approve")

    def reject(self, command: DecisionCommand) -> DecisionResponse:
        if not (command.reason or "").strip():
            raise RunServiceError("a rejection needs a reason")
        return self._decide(command, "reject")

    def resume(self, run_id: str) -> RunSummary:
        """Crash recovery only. A paused or finished run comes back unchanged."""
        deps, _ = self._deps_for(run_id)
        try:
            return _summary(resume_run(run_id, deps))
        finally:
            deps.conn.close()
