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

from finagent.config.clock import Clock, add_business_days, local_date
from finagent.controls.decision import OUTCOME_PRECEDENCE
from finagent.controls.rules import rule
from finagent.domain.enums import Outcome
from finagent.obs.redaction import redact
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
RECENT_MODIFIER_BUSINESS_DAYS = 6  # FIN-POL-001 s4: five business days, plus the one-day safety margin


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


def _is_stricter(new: Outcome | str, current: str) -> bool:
    return OUTCOME_PRECEDENCE.index(Outcome(new)) < OUTCOME_PRECEDENCE.index(Outcome(current))


class ApprovalService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Clock,
        authority: Callable[[], Sequence[AuthorityRecord]],
        vendor: Callable[[], VendorRecord],
        history: Callable[[], Sequence[HistoryRecord]],
        documents: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._authority = authority
        self._vendor = vendor
        self._history = history
        self._documents = documents or (lambda: {})
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
            documents = self._documents()
        except Exception:
            return None
        return {
            "vendor_status": str(vendor.status),
            "fingerprint": vendor.fingerprint,
            "history": sorted(item.record_id for item in history),
            "risk_flags": sorted(str(item) for item in vendor.risk_flags),
            "bank_changed_at": vendor.bank_changed_at.isoformat() if vendor.bank_changed_at else None,
            "documents": documents,
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
            if existing is not None and _is_stricter(payload.outcome, existing.outcome):
                # A stricter recommendation must reach an approver, so it replaces the undecided request.
                self._repo.retire_pending(existing.approval_id, self._clock.now_utc())
                self._audit(
                    existing.run_id,
                    "approval_replaced",
                    str(payload.outcome),
                    started,
                    approval_id=existing.approval_id,
                    replaced_by=approval_id,
                )
                existing = None
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

    def check_eligibility(self, approval_id: str, approver_id: str, decision: str = "approve") -> Eligibility:
        record = self._repo.get(approval_id)
        if record is None:
            return Eligibility(False, "approval_not_found")
        return self._eligibility(record, approver_id, decision)

    def _conflict(self, record: ApprovalRecord, approver_id: str) -> str | None:
        try:
            vendor = self._vendor()
        except Exception:
            return "vendor_unavailable"
        if approver_id == vendor.created_by:
            return "vendor_creator"
        today = self._clock.as_of()
        for change in vendor.modification_history:
            changed = local_date(change.at)
            if change.by == approver_id and today <= add_business_days(
                changed, RECENT_MODIFIER_BUSINESS_DAYS
            ):
                return "recent_vendor_modifier"
        if approver_id == record.requester:
            return "approver_is_requester"
        if record.commitment > SEGREGATION_LIMIT and approver_id in record.receipters:
            return "receipter_above_25k"
        return None

    def _eligibility(
        self, record: ApprovalRecord, approver_id: str, decision: str = "approve"
    ) -> Eligibility:
        try:
            entries = self._authority()
        except Exception:
            return Eligibility(False, "authority_unavailable")  # an unreadable register approves nobody
        entry = next((item for item in entries if item.approver_id == approver_id), None)
        if entry is None:
            return Eligibility(False, "not_registered")
        if record.action in PROTECTIVE_ACTIONS:
            if decision == "approve":  # recording a protective action needs no authority limit
                return Eligibility(True, entry=entry)
            conflict = self._conflict(record, approver_id)  # dismissing one must not be open to a conflict
            return Eligibility(conflict is None, conflict, entry)
        if not self._delegation_active(entry, self._clock.as_of()):
            return Eligibility(False, "delegation_expired", entry)
        conflict = self._conflict(record, approver_id)
        if conflict is not None:
            return Eligibility(False, conflict, entry)
        reason = self._limit_problem(record, entry)
        return Eligibility(reason is None, reason, entry)

    @staticmethod
    def _delegation_active(entry: AuthorityRecord, day: date) -> bool:
        own = [item for item in entry.delegations if item.delegate == entry.approver_id]
        return not own or any(item.start <= day <= item.end for item in own)  # the end date is inclusive

    @staticmethod
    def _applied_limit(record: ApprovalRecord, entry: AuthorityRecord) -> Decimal | None:
        matrix = {name: Decimal(limit) for name, limit in rule("DFA-TIER").threshold.items()}
        if entry.role in matrix:
            return min(entry.limit, matrix[entry.role])
        if record.action in PROTECTIVE_ACTIONS or (
            record.action == "REJECT" and entry.role == FINANCIAL_CONTROL
        ):
            return entry.limit  # Financial Control counts for REJECT and protective actions, never POST
        return None

    @classmethod
    def _limit_problem(cls, record: ApprovalRecord, entry: AuthorityRecord) -> str | None:
        limit = cls._applied_limit(record, entry)
        if limit is None:
            return "role_not_permitted"
        return None if limit >= record.commitment else "limit_too_low"

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
        eligibility = self._eligibility(record, approver_id, decision)
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
            "limit_amount": str(self._applied_limit(record, entry) or entry.limit),
            "register_version": entry.register_version,
            "decision": decision,
            "reason": redact(reason),
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
                    case_id=record.case_id,
                    decision=decision,
                    role=entry.role,
                    limit=fields["limit_amount"],
                    register_version=entry.register_version,
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
            documents = self._documents()
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
        if sorted(str(item) for item in vendor.risk_flags) != record.snapshot.get("risk_flags", []):
            problems.append("risk_flags_changed")
        bank_changed = vendor.bank_changed_at.isoformat() if vendor.bank_changed_at else None
        if bank_changed != record.snapshot.get("bank_changed_at"):
            problems.append("bank_change_date_changed")
        if documents != record.snapshot.get("documents", {}):
            problems.append("invoice_or_po_changed")
        if record.approver_id and not self._eligibility(record, record.approver_id).eligible:
            problems.append("approver_no_longer_eligible")
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
