"""The signed-off end-to-end cases, run with `make scenarios`."""

import pytest

from finagent.eval.scenarios import load_expectations, run_case

pytestmark = pytest.mark.scenarios
EXPECTATIONS = load_expectations()


@pytest.mark.parametrize("case_id", sorted(EXPECTATIONS))
def test_case_meets_every_expectation(case_id):
    result = run_case(case_id, EXPECTATIONS[case_id])
    failed = [item for item in result.assertions if not item.passed]
    assert result.passed, [(item.key, item.expected, item.actual) for item in failed]
