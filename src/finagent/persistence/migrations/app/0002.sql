-- Approval evidence cannot be rewritten: only legal status moves, and decided fields are frozen.
CREATE TRIGGER IF NOT EXISTS approvals_no_delete BEFORE DELETE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;

CREATE TRIGGER IF NOT EXISTS approvals_legal_update BEFORE UPDATE ON approvals
WHEN NOT (
        (OLD.status = 'PENDING' AND NEW.status IN ('APPROVED', 'REJECTED', 'INVALIDATED'))
        OR (OLD.status = 'APPROVED' AND NEW.status IN ('APPROVED', 'INVALIDATED'))
    )
    OR NEW.payload_json IS NOT OLD.payload_json
    OR NEW.payload_hash IS NOT OLD.payload_hash
    OR NEW.run_id IS NOT OLD.run_id
    OR NEW.action IS NOT OLD.action
    OR NEW.amount IS NOT OLD.amount
    OR (OLD.status <> 'PENDING' AND (
        NEW.approver_id IS NOT OLD.approver_id
        OR NEW.role IS NOT OLD.role
        OR NEW.limit_amount IS NOT OLD.limit_amount
        OR NEW.register_version IS NOT OLD.register_version
        OR NEW.decision IS NOT OLD.decision
        OR NEW.reason IS NOT OLD.reason
        OR NEW.decided_at IS NOT OLD.decided_at
        OR NEW.response_json IS NOT OLD.response_json
    ))
BEGIN SELECT RAISE(ABORT, 'illegal approval update'); END;
