"""WP 5.2 vendor and authority tests, written before the implementation."""

from datetime import date

import pytest

from finagent.config.clock import FixedClock
from finagent.controls.vendor_authority import check, default_authority_source
from finagent.domain.results import Unknown
from fixtures.builder import build_case

PASS_STATES = {"PASS", "UNKNOWN"}
SAME_PERSON = {
    "po.requester": "req-01",
    "po.receipts[0].receipter": "req-01",
    "po.receipts[1].receipter": "req-01",
}


def gst_free(price, excl):
    return {
        "po.lines[1].qty": 1,
        "po.lines[1].unit_price": price,
        "po.lines[1].line_value": price,
        "invoice.lines[1].qty": 1,
        "invoice.lines[1].unit_price": price,
        "invoice.lines[1].tax_code": "FRE",
        "po.receipts[1].qty": 1,
        "evidence.goods_receipt_note.lines[1].qty": 1,
        "po.totals.excl_tax": excl,
    }


def at(day):
    return f"{day}T09:00:00+10:00"


def run(case, source=default_authority_source):
    return check(case, FixedClock(case.as_of), source)


def failures(case, source=default_authority_source):
    return [item for item in run(case, source).findings if item.outcome not in PASS_STATES]


def rules(case):
    return {item.rule_id for item in failures(case)}


def categories(case):
    return {item.category for item in failures(case)}


def test_fin_001_is_clean_at_cost_centre_manager_tier():
    case = build_case("FIN-001")
    assert failures(case) == []
    assert run(case).dfa_role == "Cost Centre Manager"


@pytest.mark.parametrize(
    ("overrides", "role"),
    [
        ({}, "Cost Centre Manager"),
        (gst_free("3400.00", "9400.00"), "Cost Centre Manager"),
        (gst_free("3400.01", "9400.01"), "Department Director"),
    ],
)
def test_dfa_role_boundaries(overrides, role):
    result = run(build_case("FIN-001", overrides))
    assert result.dfa_role == role
    assert not [
        item for item in result.findings if item.rule_id == "DFA-TIER" and item.outcome not in PASS_STATES
    ]


def test_commitment_is_the_higher_of_gross_and_po_total():
    case = build_case("FIN-001", {"po.totals.incl_tax": "10000.01"})
    assert run(case).dfa_role == "Department Director"


def test_no_posting_role_covers_the_commitment():
    case = build_case("FIN-001", gst_free("43400.01", "49400.01"))
    assert rules(case) == {"DFA-TIER"}
    assert categories(case) == {"AUTHORITY_GAP"}
    assert run(case).dfa_role is None


def test_financial_control_is_not_a_posting_role():
    overrides = {**gst_free("18400.00", "24400.00"), "authority": []}
    fc_only = build_case("FIN-001").authority[2].model_dump(mode="json")
    overrides["authority[0]"] = fc_only
    case = build_case("FIN-001", overrides)
    assert "DFA-TIER" in rules(case)
    assert run(case).dfa_role is None


def test_a_register_limit_above_the_matrix_does_not_raise_the_role_limit():
    overrides = {"authority[0].limit": "20000.00", **gst_free("8400.00", "14400.00")}
    assert run(build_case("FIN-001", overrides)).dfa_role == "Department Director"


def test_authority_tool_failure_is_unknown():
    case = build_case("FIN-001").model_copy(
        update={"unknowns": (Unknown(source="get_authority_record", reason="timeout"),), "authority": ()}
    )
    tier = next(item for item in run(case).findings if item.rule_id == "DFA-TIER")
    assert tier.outcome == "UNKNOWN"


@pytest.mark.parametrize(
    ("amount", "escalates"),
    [(("18400.00", "24400.00"), False), (("18400.01", "24400.01"), True)],
)
def test_segregation_boundary(amount, escalates):
    case = build_case("FIN-001", {**gst_free(*amount), **SAME_PERSON})
    found = [item for item in failures(case) if item.rule_id == "SOD-ABOVE-25K"]
    assert bool(found) is escalates
    if escalates:
        assert (found[0].outcome, found[0].category) == ("ESCALATE_CONTROL_REVIEW", "AUTHORITY_GAP")


def test_segregation_needs_the_same_person():
    assert "SOD-ABOVE-25K" not in rules(build_case("FIN-001", gst_free("18400.01", "24400.01")))


@pytest.mark.parametrize(("created", "new"), [("2026-08-16", False), ("2026-08-17", True)])
def test_new_vendor_boundary(created, new):
    case = build_case("FIN-001", {"vendor.created_at": at(created), "vendor.last_paid_at": None})
    assert ("HIGH-RISK" in rules(case)) is new


@pytest.mark.parametrize("flag", ["overseas_account", "manual_payment", "fraud_flag"])
def test_risk_flags_are_authority_gap(flag):
    case = build_case("FIN-001", {"vendor.risk_flags": [flag]})
    assert rules(case) == {"HIGH-RISK"}
    assert categories(case) == {"AUTHORITY_GAP"}


def test_overseas_remit_country_is_high_risk():
    assert "HIGH-RISK" in rules(build_case("FIN-001", {"invoice.remit_to.country": "NZ"}))


def test_recent_bank_change_is_bank_change():
    case = build_case("FIN-001", {"vendor.bank_changed_at": at("2026-09-14")})
    assert "BANK-CHANGE-HOLD" in rules(case)
    assert "BANK_CHANGE" in categories(case)


@pytest.mark.parametrize(("changed", "held"), [("2026-09-10", True), ("2026-09-09", False)])
def test_bank_change_margin_pair(changed, held):
    case = build_case("FIN-001", {"vendor.bank_changed_at": at(changed)})
    assert ("BANK-CHANGE-HOLD" in rules(case)) is held
    assert "FIRST-PAYMENT-AFTER-BANK-CHANGE" in rules(case)


@pytest.mark.parametrize("status", ["BLOCKED", "DORMANT", "SANCTIONS_REVIEW", "PENDING_VERIFICATION"])
def test_held_vendor_statuses(status):
    case = build_case("FIN-001", {"vendor.status": status})
    assert rules(case) == {"VENDOR-STATUS"}
    assert categories(case) == {"VENDOR_BLOCK"}


def test_first_payment_after_a_bank_change():
    case = build_case("FIN-001", {"vendor.bank_changed_at": at("2026-09-01")})
    assert rules(case) == {"FIRST-PAYMENT-AFTER-BANK-CHANGE", "HIGH-RISK"}
    assert categories(case) == {"AUTHORITY_GAP"}


def test_first_payment_when_the_vendor_was_never_paid():
    case = build_case("FIN-001", {"vendor.bank_changed_at": at("2026-09-01"), "vendor.last_paid_at": None})
    assert "FIRST-PAYMENT-AFTER-BANK-CHANGE" in rules(case)


def test_payment_after_the_bank_change_clears_first_payment():
    case = build_case(
        "FIN-001", {"vendor.bank_changed_at": at("2026-08-01"), "vendor.last_paid_at": at("2026-08-03")}
    )
    assert rules(case) == set()


def test_dfa_source_uses_as_of_and_never_last_paid():
    before = build_case("FIN-001", {"as_of": at("2026-07-31"), "vendor.last_paid_at": at("2026-07-20")})
    assert rules(before) == {"DFA-AUTHORITATIVE-SOURCE"}
    assert run(before).dfa_role is None
    after = build_case("FIN-001", {"vendor.last_paid_at": at("2026-07-20")})
    assert rules(after) == set()
    assert run(after).dfa_role == "Cost Centre Manager"


def test_as_of_before_the_dfa_is_authority_gap():
    case = build_case("FIN-001", {"as_of": at("2026-07-31")})
    assert categories(case) == {"AUTHORITY_GAP"}


def test_authority_source_is_called_with_the_clock_instant():
    calls = []

    def source(doc_id, instant):
        calls.append((doc_id, instant))
        return True

    case = build_case("FIN-001")
    run(case, source)
    assert calls == [("FIN-POL-003", case.as_of)]


def test_source_that_says_no_fails_safe():
    case = build_case("FIN-001")
    assert {item.rule_id for item in failures(case, lambda doc, day: False)} == {"DFA-AUTHORITATIVE-SOURCE"}


def test_remit_mismatch_is_bank_change():
    case = build_case("FIN-001", {"invoice.remit_to.fingerprint": "f" * 64})
    assert rules(case) == {"REMIT-MATCHES-MASTER"}
    assert categories(case) == {"BANK_CHANGE"}


def test_missing_remit_to_fails():
    assert "REMIT-MATCHES-MASTER" in rules(build_case("FIN-001", {"invoice.remit_to": None}))


def test_usd_is_held_by_treasury_with_no_dfa_finding():
    case = build_case("FIN-001", {"invoice.currency": "USD", "po.currency": "USD", "request.currency": "USD"})
    result = run(case)
    found = [item for item in result.findings if item.outcome not in PASS_STATES]
    assert [(item.rule_id, item.owner) for item in found] == [("FX-HOLD", "Treasury")]
    assert not [item for item in result.findings if item.rule_id.startswith("DFA-")]
    assert result.dfa_role is None


def test_invoice_and_po_currency_mismatch_is_held():
    case = build_case("FIN-001", {"po.currency": "USD"})
    assert rules(case) == {"FX-HOLD"}
    assert run(case).dfa_role is None


def test_failed_vendor_read_is_unknown():
    case = build_case("FIN-001").model_copy(update={"vendor": None})
    result = run(case)
    assert not [item for item in result.findings if item.outcome not in PASS_STATES]
    status = next(item for item in result.findings if item.rule_id == "VENDOR-STATUS")
    assert status.outcome == "UNKNOWN"


def test_failed_invoice_read_is_unknown():
    case = build_case("FIN-001").model_copy(update={"invoice": None})
    result = run(case)
    outcomes = {item.rule_id: item.outcome for item in result.findings}
    assert outcomes["VENDOR-STATUS"] == "PASS"
    for rule_id in ("REMIT-MATCHES-MASTER", "HIGH-RISK", "DFA-TIER", "SOD-ABOVE-25K", "FX-HOLD"):
        assert outcomes[rule_id] == "UNKNOWN", rule_id
    assert result.dfa_role is None


def test_a_known_risk_still_fails_when_the_invoice_is_unavailable():
    case = build_case("FIN-001", {"vendor.risk_flags": ["manual_payment"]}).model_copy(
        update={"invoice": None}
    )
    assert "HIGH-RISK" in rules(case)


def test_fin_004_uses_the_invoice_gross_when_the_po_is_unavailable():
    case = build_case("FIN-004")
    result = run(case)
    assert result.dfa_role == "Cost Centre Manager"
    segregation = next(item for item in result.findings if item.rule_id == "SOD-ABOVE-25K")
    assert segregation.outcome == "PASS"


def test_unavailable_po_above_25k_makes_segregation_unknown():
    case = build_case("FIN-004", {"invoice.lines[1].unit_price": "300.00"})
    segregation = next(item for item in run(case).findings if item.rule_id == "SOD-ABOVE-25K")
    assert segregation.outcome == "UNKNOWN"


def test_clock_date_is_the_sydney_date():
    case = build_case("FIN-001", {"as_of": "2026-07-31T15:00:00+00:00"})
    assert FixedClock(case.as_of).as_of() == date(2026, 8, 1)
    assert "DFA-AUTHORITATIVE-SOURCE" not in rules(case)
