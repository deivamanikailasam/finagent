"""The human approval gate (decision log V). Every state change is a compare-and-set."""

import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from finagent.config.clock import Clock
from finagent.controls.rules import rule
from finagent.persistence.db import transaction
from finagent.persistence.repos import ApprovalRecord, ApprovalRepo, AuditRepo, EscalationRecord
from finagent.tools.schemas import (
    AuthorityRecord,
    DecisionPayload,
    HistoryRecord,
    VendorRecord,
    payload_hash_of,
)

FINANCIAL_CONTROL = "Financial Control"
SEGREGATION_LIMIT = Decimal("25000.00")
DECISIONS = {"approve": "APPROVED", "reject": "REJECTED"}
PROTECTIVE_ACTIONS = frozenset({"HOLD", "ESCALATE"})


@dataclass(frozen=True)
class ApprovalRequest:
    run_id: str
    case_id: str
    action: str
    payload: DecisionPayload
    vendor_id: str
    vendor_name: str
    requester: str
    receipters: tuple[str, ...]
    exceptions: tuple[Any, ...] = ()
    citations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApprovalResponse:
    approval_id: str
    code: str
    status: str
    action: str
    approver_id: str
    decided_at: str | None = None


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason: str | None = None
    entry: AuthorityRecord | None = None


@dataclass(frozen=True)
class RevalidationResult:
    valid: bool
    status: str
    reasons: tuple[str, ...] = ()


class ApprovalService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Clock,
        authority: Callable[[], Sequence[AuthorityRecord]],
        vendor: Callable[[], VendorRecord],
        history: Callable[[], Sequence[HistoryRecord]],
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._authority = authority
        self._vendor = vendor
        self._history = history
        self._repo = ApprovalRepo(conn)
        self._audit_repo = AuditRepo(conn)

    def _audit(self, run_id: str, event: str, outcome: str, started: float, **detail: Any) -> None:
        self._audit_repo.append(
            {
                "ts": self._clock.now_utc().isoformat(),
                "run_id": run_id,
                "event": event,
                "outcome": outcome,
                "duration_ms": (time.perf_counter() - started) * 1000,
                **detail,
            }
        )

    def _snapshot(self) -> dict[str, Any] | None:
        """Time-sensitive facts at request time. None means they could not be read."""
        try:
            vendor = self._vendor()
            history = self._history()
        except Exception:
            return None
        return {
            "vendor_status": str(vendor.status),
            "fingerprint": vendor.fingerprint,
            "history": sorted(item.record_id for item in history),
        }

    def create_request(self, request: ApprovalRequest) -> ApprovalRecord:
        """Create the invoice's one pending request, or return the one that already exists."""
        started = time.perf_counter()
        payload = request.payload
        snapshot = self._snapshot()
        now = self._clock.now_utc().isoformat()
        approval_id = str(uuid.uuid4())
        values = {
            "approval_id": approval_id,
            "run_id": request.run_id,
            "case_id": request.case_id,
            "action": request.action,
            "vendor_id": request.vendor_id,
            "vendor_name": request.vendor_name,
            "invoice_no_norm": payload.invoice_no_norm,
            "amount": str(payload.amount),
            "currency": payload.currency,
            "payload_json": payload.model_dump_json(),
            "payload_hash": payload_hash_of(payload),
            "requester": request.requester,
            "receipters_json": json.dumps(list(request.receipters)),
            "exceptions_json": json.dumps(list(request.exceptions), default=str),
            "citations_json": json.dumps(list(request.citations)),
            "snapshot_json": json.dumps(snapshot) if snapshot is not None else None,
            "status": "PENDING",
            "requested_at": now,
        }
        with transaction(self._conn):
            existing = self._repo.pending_for(request.vendor_id, payload.invoice_no_norm)
            if existing is None:
                self._repo.insert(values)
                self._audit(request.run_id, "approval_requested", "pending", started, approval_id=approval_id)
                created = self._repo.get(approval_id)
            else:
                self._audit(
                    request.run_id,
                    "DUPLICATE_APPROVAL_REQUEST",
                    "existing_request",
                    started,
                    approval_id=existing.approval_id,
                )
                created = existing
        return created

    def get(self, approval_id: str) -> ApprovalRecord | None:
        return self._repo.get(approval_id)

    def escalations(self) -> list[EscalationRecord]:
        return self._repo.escalations()

    def check_eligibility(self, approval_id: str, approver_id: str) -> Eligibility:
        record = self._repo.get(approval_id)
        if record is None:
            return Eligibility(False, "approval_not_found")
        return self._eligibility(record, approver_id)

    def _eligibility(self, record: ApprovalRecord, approver_id: str) -> Eligibility:
        try:
            entries = self._authority()
        except Exception:
            return Eligibility(False, "authority_unavailable")  # an unreadable register approves nobody
        entry = next((item for item in entries if item.approver_id == approver_id), None)
        if entry is None:
            return Eligibility(False, "not_registered")
        if record.action in PROTECTIVE_ACTIONS:
            return Eligibility(True, entry=entry)
        if not self._delegation_active(entry, self._clock.as_of()):
            return Eligibility(False, "delegation_expired", entry)
        try:
            creator = self._vendor().created_by
        except Exception:
            return Eligibility(False, "vendor_unavailable", entry)
        if approver_id == creator:
            return Eligibility(False, "vendor_creator", entry)
        if approver_id == record.requester:
            return Eligibility(False, "approver_is_requester", entry)
        if record.amount > SEGREGATION_LIMIT and approver_id in record.receipters:
            return Eligibility(False, "receipter_above_25k", entry)
        reason = self._limit_problem(record, entry)
        return Eligibility(reason is None, reason, entry)

    @staticmethod
    def _delegation_active(entry: AuthorityRecord, day: date) -> bool:
        own = [item for item in entry.delegations if item.delegate == entry.approver_id]
        return not own or any(item.start <= day <= item.end for item in own)  # the end date is inclusive

    @staticmethod
    def _limit_problem(record: ApprovalRecord, entry: AuthorityRecord) -> str | None:
        matrix = {name: Decimal(limit) for name, limit in rule("DFA-TIER").threshold.items()}
        if entry.role in matrix:
            limit = min(entry.limit, matrix[entry.role])
        elif record.action == "REJECT" and entry.role == FINANCIAL_CONTROL:
            limit = entry.limit  # counts as at least Cost Centre Manager for REJECT, never for POST
        else:
            return "role_not_permitted"
        return None if limit >= record.amount else "limit_too_low"

    def decide(
        self, approval_id: str, approver_id: str, decision: str, reason: str | None = None
    ) -> ApprovalResponse:
        if decision not in DECISIONS:
            raise ValueError(f"unknown decision: {decision}")
        started = time.perf_counter()
        record = self._repo.get(approval_id)
        if record is None:
            return ApprovalResponse(approval_id, "APPROVAL_NOT_FOUND", "NOT_FOUND", "", approver_id)
        if record.status != "PENDING":
            return self._after_decision(record, approver_id, decision)
        eligibility = self._eligibility(record, approver_id)
        if not eligibility.eligible:
            return self._refuse(record, approver_id, eligibility.reason or "not_eligible", started)
        entry = eligibility.entry
        status = DECISIONS[decision]
        decided_at = self._clock.now_utc().isoformat()
        response = ApprovalResponse(approval_id, status, status, record.action, approver_id, decided_at)
        fields = {
            "decided_at": decided_at,
            "approver_id": approver_id,
            "role": entry.role,
            "limit_amount": str(entry.limit),
            "register_version": entry.register_version,
            "decision": decision,
            "reason": reason,
            "response_json": json.dumps(asdict(response)),
        }
        with transaction(self._conn):
            won = self._repo.decide(approval_id, status, fields)
            if won:
                self._audit(
                    record.run_id,
                    "approval_decided",
                    status,
                    started,
                    approval_id=approval_id,
                    approver_id=approver_id,
                )
        if not won:  # another callback decided first
            return self._after_decision(self._repo.get(approval_id), approver_id, decision)
        return response

    def _after_decision(self, record: ApprovalRecord, approver_id: str, decision: str) -> ApprovalResponse:
        same = record.approver_id == approver_id and record.decision == decision
        if same and record.status in DECISIONS.values() and record.response_json:
            return ApprovalResponse(**json.loads(record.response_json))  # replay-safe stored response
        return ApprovalResponse(
            record.approval_id, "ALREADY_DECIDED", record.status, record.action, approver_id
        )

    def _refuse(
        self, record: ApprovalRecord, approver_id: str, reason: str, started: float
    ) -> ApprovalResponse:
        now = self._clock.now_utc()
        with transaction(self._conn):
            self._audit(
                record.run_id,
                "security_event",
                "APPROVER_NOT_ELIGIBLE",
                started,
                approval_id=record.approval_id,
                approver_id=approver_id,
                reason_code=reason,
            )
            self._repo.add_escalation(
                record.approval_id, record.run_id, approver_id, FINANCIAL_CONTROL, reason, now
            )
        return ApprovalResponse(
            record.approval_id, "APPROVER_NOT_ELIGIBLE", "PENDING", record.action, approver_id
        )

    def _revalidation_problems(self, record: ApprovalRecord) -> list[str]:
        if record.snapshot is None:
            return ["snapshot_unavailable"]
        try:
            vendor = self._vendor()
            history = self._history()
            entries = self._authority()
        except Exception:
            return ["read_failure"]
        problems = []
        if str(vendor.status) != record.snapshot["vendor_status"]:
            problems.append("vendor_status_changed")
        if vendor.fingerprint != record.snapshot["fingerprint"]:
            problems.append("bank_details_changed")
        if sorted(item.record_id for item in history) != record.snapshot["history"]:
            problems.append("history_changed")
        entry = next((item for item in entries if item.approver_id == record.approver_id), None)
        if entry is None or not self._delegation_active(entry, self._clock.as_of()):
            problems.append("delegation_invalid")
        return problems

    def invalidate(self, approval_id: str) -> bool:
        """Retire an approved request whose decision can no longer be carried out."""
        started = time.perf_counter()
        record = self._repo.get(approval_id)
        if record is None:
            return False
        with transaction(self._conn):
            moved = self._repo.mark_revalidated(approval_id, False, self._clock.now_utc())
            self._audit(record.run_id, "approval_invalidated", str(moved), started, approval_id=approval_id)
        return moved

    def revalidate(self, approval_id: str) -> RevalidationResult:
        """Recheck time-sensitive facts at Clock now. Any change or read failure invalidates."""
        started = time.perf_counter()
        record = self._repo.get(approval_id)
        if record is None:
            return RevalidationResult(False, "NOT_FOUND", ("approval_not_found",))
        if record.status != "APPROVED":
            return RevalidationResult(False, record.status, ("not_approved",))
        problems = self._revalidation_problems(record)
        valid = not problems
        with transaction(self._conn):
            moved = self._repo.mark_revalidated(approval_id, valid, self._clock.now_utc())
            self._audit(
                record.run_id,
                "approval_revalidated",
                "valid" if valid and moved else "invalidated",
                started,
                approval_id=approval_id,
                reasons=problems,
            )
        current = self._repo.get(approval_id)
        return RevalidationResult(valid and moved, current.status, tuple(problems))
