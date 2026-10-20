"""Independent review regressions: exact limits, search and canonical inputs."""

from copy import deepcopy
from decimal import Context, Decimal, ROUND_HALF_UP, localcontext
import json
from pathlib import Path

import pytest

from credit_engine import evaluate
from credit_engine.policy import PolicyConfig, load_policy


ROOT = Path(__file__).resolve().parents[1]
D = Decimal


def raw_case(name="F01"):
    directory = ROOT / "examples" / name
    return (
        json.loads((directory / "snapshot.json").read_text(encoding="utf-8")),
        json.loads((directory / "checks.json").read_text(encoding="utf-8")),
    )


@pytest.fixture
def policy():
    return load_policy(ROOT / "policies" / "demo-assets-2.json")


def test_signed_zero_money_has_the_same_canonical_input_and_result(policy):
    snapshot, checks = raw_case()
    snapshot["existing_monthly_debt"] = "0.00"
    positive_zero = evaluate(snapshot, checks, policy)
    snapshot["existing_monthly_debt"] = "-0.00"
    negative_zero = evaluate(snapshot, checks, policy)
    assert negative_zero.input_fingerprint == positive_zero.input_fingerprint
    assert negative_zero.model_dump_json() == positive_zero.model_dump_json()


@pytest.mark.parametrize("amount,status", [("2153277.68", "AUTO_ELIGIBLE"), ("2153277.69", "REJECTED")])
def test_cash_limit_decision_uses_raw_value_despite_identical_displayed_dscr(policy, amount, status):
    snapshot, checks = raw_case()
    snapshot.update(requested_amount=amount, financing_need="3000000.00")
    result = evaluate(snapshot, checks, policy)
    requested = result.requested_assessment
    assert requested.dscr == D("1.300000")
    assert requested.cash_limit == D("2153277.68")
    assert requested.status == status
    assert ("CASHFLOW_LIMIT" in requested.reason_codes) == (status == "REJECTED")


@pytest.mark.parametrize("amount,status", [("873786.40", "AUTO_ELIGIBLE"), ("873786.41", "REJECTED")])
def test_collateral_ratio_is_checked_before_money_display_rounding(policy, amount, status):
    snapshot, checks = raw_case("F05")
    snapshot["requested_amount"] = amount
    config = policy.model_dump(mode="json")
    for product in config["products"]:
        if product["product_id"] == "EQUIPMENT":
            product["coverage_ratio"] = "1.03"
    result = evaluate(snapshot, checks, PolicyConfig.model_validate(config))
    requested = result.requested_assessment
    assert requested.collateral_limit == D("873786.40")
    assert requested.status == status
    assert ("COLLATERAL_LIMIT" in requested.reason_codes) == (status == "REJECTED")


@pytest.mark.parametrize("boundary,cashflow,expected_points,expected_status", [
    ("1.30", "64999.99", 15, "REJECTED"),
    ("1.30", "65000.00", 30, "AUTO_ELIGIBLE"),
    ("1.30", "65000.01", 30, "AUTO_ELIGIBLE"),
    ("1.50", "74999.99", 30, "REVIEW"),
    ("1.50", "75000.00", 40, "AUTO_ELIGIBLE"),
    ("1.50", "75000.01", 40, "AUTO_ELIGIBLE"),
])
def test_zero_rate_exact_dscr_thresholds_and_neighboring_cents(policy, boundary, cashflow, expected_points, expected_status):
    snapshot, checks = raw_case()
    snapshot.update(requested_amount="120000.00", requested_term_months=12)
    for period in snapshot["financial_periods"]:
        period["operating_receipts"] = format(D(cashflow) + D("400000"), ".2f")
    if boundary == "1.50":
        checks["credit_history"]["payload"]["max_closed_overdue_days"] = 1
    config = policy.model_dump(mode="json")
    for product in config["products"]:
        product["annual_rate"] = "0"
    requested = evaluate(snapshot, checks, PolicyConfig.model_validate(config)).requested_assessment
    assert requested.dscr == D(boundary)
    assert requested.score_components.payment == expected_points
    assert requested.status == expected_status


def test_available_review_candidate_is_not_hidden_by_optional_missing_asset(policy):
    # Base score35 + best payment40 =75. Existing vehicle permits a valid review.
    snapshot, checks = raw_case("F05")
    checks["credit_history"]["payload"]["max_closed_overdue_days"] = 31
    original = evaluate(snapshot, checks, policy)
    extra = deepcopy(snapshot["assets"][0])
    extra.update(asset_id="unassessed-extra", object_key="VEHICLE:VIN:unassessed-extra")
    snapshot["assets"].append(extra)
    augmented = evaluate(snapshot, checks, policy)
    assert original.recommendation == augmented.recommendation == "MANUAL_REVIEW"
    assert any(candidate.status == "REVIEW" for candidate in augmented.candidates)
    assert augmented.requested_fields == ()
    assert not augmented.offers


@pytest.mark.parametrize("case", ["F17/score_below_minimum", "F17/cash_below_minimum"])
def test_impossible_financial_candidate_does_not_request_collateral_work(policy, case):
    snapshot, checks = raw_case(case)
    result = evaluate(snapshot, checks, policy)
    assert result.recommendation == "DECLINED"
    assert result.requested_fields == ()
    assert result.next_actions == ()


def test_rejected_and_review_candidates_do_not_create_bank_actions_when_auto_offer_exists(policy):
    snapshot, checks = raw_case()
    checks["credit_history"]["payload"]["max_closed_overdue_days"] = 1
    result = evaluate(snapshot, checks, policy)
    assert result.recommendation == "OFFERED"
    assert any(candidate.status == "REVIEW" for candidate in result.candidates)
    assert "BANK_REVIEW" not in {action.code for action in result.next_actions}


@pytest.mark.parametrize("cashflow,need", [("60000.00", "300000.00"), ("200000.00", "150000.00")])
def test_step_limited_amounts_are_not_misreported_as_bad_scores(policy, cashflow, need):
    snapshot, checks = raw_case()
    snapshot.update(requested_amount="300000.00", financing_need=need)
    for period in snapshot["financial_periods"]:
        period["operating_receipts"] = format(D(cashflow) + D("400000"), ".2f")
    config = policy.model_dump(mode="json")
    for product in config["products"]:
        if product["product_id"] == "WORKING_CAPITAL":
            product["amount_step"] = "200000.00"
    result = evaluate(snapshot, checks, PolicyConfig.model_validate(config))
    assert result.recommendation == "DECLINED"
    assert all(candidate.score_total >= 50 for candidate in result.candidates)
    assert "SCORE_BELOW_MIN" not in {item.code for item in result.reasons if item.scope == "APPLICATION"}


@pytest.mark.parametrize("cashflow,amount,need,overdue", [
    ("80000", "2000000.00", "2000000.00", 0),
    ("200000", "2000000.00", "1234567.89", 0),
    ("200000", "1234567.89", "2000000.00", 0),
    ("200000", "2000000.00", "2000000.00", 30),
    ("200000", "2000000.00", "2000000.00", 31),
    ("65000", "300000.01", "2000000.00", 30),
    ("200000", "3000000.01", "2999999.99", 0),
    ("200000", "100000.01", "100000.00", 0),
    ("200000", "100000.00", "99999.99", 0),
])
def test_finite_search_finds_best_offer_from_independent_grid(policy, cashflow, amount, need, overdue):
    snapshot, checks = raw_case()
    snapshot.update(requested_amount=amount, financing_need=need)
    for period in snapshot["financial_periods"]:
        period["operating_receipts"] = format(D(cashflow) + D("400000"), ".2f")
    checks["credit_history"]["payload"]["max_closed_overdue_days"] = overdue
    actual = evaluate(snapshot, checks, policy)

    # Independent exhaustive grid oracle, with fixed demo rules from sections7/10.
    # It imports no implementation finance/scoring/search functions.
    history_points = 25 if overdue == 0 else 10 if overdue <= 30 else 0
    expected = []
    with localcontext(Context(prec=60, rounding=ROUND_HALF_UP)):
        requested, financing_need, c = D(amount), D(need), D(cashflow)
        amounts = {requested}
        amounts.update(D(value) for value in range(100000, min(int(requested), 3000000) + 1, 10000) if D(value) < requested)
        for months in (6, 12, 18, 24):
            for candidate in amounts:
                if not D("100000") <= candidate <= min(requested, financing_need, D("3000000")):
                    continue
                growth = D("1.02") ** months
                payment = candidate * D("0.02") * growth / (growth - 1)
                if c < D("1.30") * (D("40000") + payment):
                    continue
                payment_points = 40 if c >= D("1.50") * (D("40000") + payment) else 30
                if history_points + 25 + 10 + payment_points < 80:
                    continue
                total = (payment * months).quantize(D(".01"))
                expected.append((not (candidate == requested and months == 24), financing_need - candidate, total, months, candidate))
    assert bool(actual.offers) == bool(expected)
    if expected:
        best = min(expected)
        assert (actual.offers[0].amount, actual.offers[0].term_months) == (best[4], best[3])
