-- Application database: runs, checkpoints, append-only audit and approvals (decision log V).
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    status TEXT NOT NULL,
    current_node TEXT NOT NULL,
    as_of TEXT NOT NULL,
    corpus_hash TEXT,
    prompt_hash TEXT,
    config_hash TEXT,
    model_id TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs (run_id),
    seq INTEGER NOT NULL,
    node TEXT NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, seq)
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    run_id TEXT,
    event TEXT NOT NULL,
    outcome TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    detail_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_by_run ON audit (run_id, id);

CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    action TEXT NOT NULL,
    vendor_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    invoice_no_norm TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    requester TEXT NOT NULL,
    receipters_json TEXT NOT NULL,
    exceptions_json TEXT NOT NULL,
    citations_json TEXT NOT NULL,
    snapshot_json TEXT,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'INVALIDATED')),
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    revalidated_at TEXT,
    approver_id TEXT,
    role TEXT,
    limit_amount TEXT,
    register_version TEXT,
    decision TEXT,
    reason TEXT,
    response_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS approvals_one_pending_per_invoice
    ON approvals (vendor_id, invoice_no_norm) WHERE status = 'PENDING';

CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY,
    approval_id TEXT NOT NULL REFERENCES approvals (approval_id),
    run_id TEXT NOT NULL,
    approver_id TEXT NOT NULL,
    routed_to TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (approval_id, approver_id, reason)
);
