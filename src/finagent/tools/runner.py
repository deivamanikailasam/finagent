"""Runs tools with validated arguments, deadlines, bounded retries, audit and budgets."""

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from finagent.config.clock import Clock
from finagent.domain.results import Unknown
from finagent.obs.log import log_event
from finagent.tools.block_submit import WriteRefused
from finagent.tools.declarations import ToolDeclaration, ToolsConfig
from finagent.tools.schemas import TOOL_SCHEMAS


class BudgetExceeded(RuntimeError):
    """The run has used its tool-call budget."""


class ToolHTTPError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class Executor(Protocol):
    def run(self, fn: Callable[[], Any], timeout_s: float, on_late: Callable[[Any], None]) -> Any: ...

    def sleep(self, seconds: float) -> None: ...


class ThreadExecutor:
    """Enforces a deadline by running the call on a worker thread."""

    def run(self, fn: Callable[[], Any], timeout_s: float, on_late: Callable[[Any], None]) -> Any:
        lock = threading.Lock()
        done = threading.Event()
        state: dict[str, Any] = {"abandoned": False}

        def work() -> None:
            try:
                value, error = fn(), None
            except BaseException as exc:  # handed to the caller, or dropped if the caller has gone
                value, error = None, exc
            with lock:
                late = state["abandoned"]
                state.update(value=value, error=error)
                done.set()  # inside the lock, so the caller cannot time out on a finished call
            if late and error is None:
                on_late(value)

        threading.Thread(target=work, daemon=True).start()
        if not done.wait(timeout_s):
            with lock:
                if not done.is_set():
                    state["abandoned"] = True
                    raise TimeoutError
        if state["error"] is not None:
            raise state["error"]
        return state["value"]

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True)
class RunContext:
    run_id: str
    case_id: str
    audit: Callable[[dict[str, Any]], None]
    clock: Clock


@dataclass(frozen=True)
class ToolResult:
    status: str  # ok, unknown or SUBMIT_UNKNOWN
    value: Any = None
    unknown: Unknown | None = None
    attempts: int = 0


def _fault_kind(exc: Exception) -> str | None:
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionError):
        return "connection_error"
    if isinstance(exc, ToolHTTPError):
        if exc.status == 429:
            return "http_429"
        if 500 <= exc.status < 600:
            return "http_5xx"
    return None


class ToolRunner:
    def __init__(
        self,
        tools: Mapping[str, Callable[[Any], Any]],
        declarations: ToolsConfig,
        executor: Executor,
        run_context: RunContext,
        max_tool_calls: int | None = None,
    ) -> None:
        self._tools = dict(tools)
        self._declarations = {tool.name: tool for tool in declarations.tools}
        self._backoff = declarations.backoff
        self._executor = executor
        self._context = run_context
        self._limit = declarations.budget.max_tool_calls if max_tool_calls is None else max_tool_calls
        self.tool_calls = 0

    def _audit(self, event: str, tool: str, outcome: str, started: float, **extra: Any) -> None:
        try:
            self._context.audit(
                {
                    "ts": self._context.clock.now_utc().isoformat(),
                    "run_id": self._context.run_id,
                    "event": event,
                    "tool": tool,
                    "outcome": outcome,
                    "duration_ms": (time.perf_counter() - started) * 1000,
                    **extra,
                }
            )
        except Exception:  # losing a committed write's result would be worse than losing one audit row
            log_event("audit_write_failed", outcome="error", tool=tool)

    def _outside_run_scope(self, validated: BaseModel) -> bool:
        """A write may only act for this run and this case."""
        if "run_id" not in type(validated).model_fields:
            return False
        return validated.run_id != self._context.run_id or validated.payload.case_id != self._context.case_id

    def _scoped(self, model: type[BaseModel], args: Mapping[str, Any]) -> dict[str, Any]:
        """The run context, never the caller, decides which case a tool may touch."""
        scoped = dict(args)
        if "case_id" in model.model_fields:
            scoped["case_id"] = self._context.case_id
        if "filters" in model.model_fields:
            filters = scoped.get("filters") or {}
            if isinstance(filters, BaseModel):
                filters = filters.model_dump()
            scoped["filters"] = {**filters, "case_id": self._context.case_id}
        return scoped

    @staticmethod
    def _failure(declaration: ToolDeclaration, reason: str, attempts: int) -> ToolResult:
        if declaration.access == "write":
            # The write may have committed, so only reconciliation can say what happened.
            return ToolResult("SUBMIT_UNKNOWN", attempts=attempts)
        return ToolResult(
            "unknown", unknown=Unknown(source=declaration.name, reason=reason), attempts=attempts
        )

    def call(self, name: str, args: Mapping[str, Any]) -> ToolResult:
        if name not in self._tools or name not in self._declarations or name not in TOOL_SCHEMAS:
            raise KeyError(f"tool is not available: {name}")
        if self.tool_calls >= self._limit:
            raise BudgetExceeded(f"tool-call budget of {self._limit} used")
        self.tool_calls += 1  # one invocation, however many retry attempts it takes
        declaration = self._declarations[name]
        input_model, output_model = TOOL_SCHEMAS[name]
        started = time.perf_counter()
        try:
            validated = input_model.model_validate(self._scoped(input_model, args))
        except ValidationError:
            self._audit("tool_rejected", name, "invalid_arguments", started)
            # Nothing was sent, so this is a plain unknown even for a write tool.
            return ToolResult("unknown", unknown=Unknown(source=name, reason="invalid_arguments"))
        if self._outside_run_scope(validated):
            self._audit("tool_rejected", name, "outside_run_scope", started)
            return ToolResult("unknown", unknown=Unknown(source=name, reason="outside_run_scope"))
        return self._attempts(name, declaration, output_model, validated)

    def _attempts(
        self, name: str, declaration: ToolDeclaration, output_model: type[BaseModel], validated: BaseModel
    ) -> ToolResult:
        tool = self._tools[name]
        for attempt in range(1, declaration.max_attempts + 1):
            started = time.perf_counter()

            def discard_late(_value: Any, attempt_started: float = started) -> None:
                self._audit("late_result_discarded", name, "late_result_discarded", attempt_started)

            try:
                raw = self._executor.run(lambda: tool(validated), declaration.timeout_s, discard_late)
            except WriteRefused as exc:
                self._audit("tool_attempt", name, type(exc).__name__, started, attempt=attempt)
                raise  # a definite answer, such as a duplicate, is never an unknown result
            except Exception as exc:
                kind = _fault_kind(exc)
                self._audit("tool_attempt", name, kind or "tool_error", started, attempt=attempt)
                if kind in declaration.retry_on and attempt < declaration.max_attempts:
                    self._executor.sleep(self._delay(attempt))
                    continue
                return self._failure(declaration, kind or "tool_error", attempt)
            try:
                value = output_model.model_validate(raw)
            except ValidationError:
                self._audit("tool_attempt", name, "invalid_output", started, attempt=attempt)
                return self._failure(declaration, "invalid_output", attempt)
            self._audit("tool_attempt", name, "ok", started, attempt=attempt)
            return ToolResult("ok", value=value, attempts=attempt)
        raise RuntimeError("unreachable: a tool has at least one attempt")  # pragma: no cover

    def _delay(self, attempt: int) -> float:
        delay = self._backoff.base_s * self._backoff.multiplier ** (attempt - 1)
        return min(delay, self._backoff.max_s)
