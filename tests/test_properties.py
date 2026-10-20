"""Behavioral variations outside the fixed acceptance scenarios."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext
import builtins
import json
from pathlib import Path
import random
import socket
import uuid

import pytest

from credit_engine import evaluate
from credit_engine.errors import InputValidationError
from credit_engine.models import ApplicationSnapshot, NormalizedChecks
from credit_engine.policy import PolicyConfig, load_policy
from credit_engine.scoring import candidate_score, full_months, payment_points


ROOT = Path(__file__).resolve().parents[1]


def raw_case(name="F01"):
    directory = ROOT / "examples" / name
    return (
        json.loads((directory / "snapshot.json").read_text(encoding="utf-8")),
        json.loads((directory / "checks.json").read_text(encoding="utf-8")),
    )


@pytest.fixture
def policy():
    return load_policy(ROOT / "policies" / "demo-assets-2.json")


def run(snapshot, checks, policy):
    return evaluate(snapshot, checks, policy).model_dump(mode="json")


def codes(result):
    return {item["code"] for item in result["reasons"]}


def independent_payment(amount, months, annual_rate):
    with localcontext() as context:
        context.prec = 60
        rate = Decimal(annual_rate) / Decimal(12)
        return Decimal(amount) / Decimal(months) if rate == 0 else Decimal(amount) * rate / (1 - (1 + rate) ** (-months))


@pytest.mark.parametrize("bad", [2000000.0, 2000000, "2000000", "2000000.0", "2e6", "NaN", "Infinity", "-1.00", "10000000000000000.00", True])
def test_money_boundary_rejects_noncontract_values(policy, bad):
    snapshot, checks = raw_case()
    snapshot["requested_amount"] = bad
    with pytest.raises(InputValidationError):
        run(snapshot, checks, policy)


@pytest.mark.parametrize("target", ["snapshot", "check", "payload", "asset"])
def test_unknown_fields_are_not_trusted(policy, target):
    snapshot, checks = raw_case("F03")
    objects = {"snapshot": snapshot, "check": checks["company"], "payload": checks["company"]["payload"], "asset": snapshot["assets"][0]}
    objects[target]["client_approved"] = True
    with pytest.raises(InputValidationError):
        run(snapshot, checks, policy)


@pytest.mark.parametrize("field", ["consolidated_accounts_confirmed", "expense_classification_confirmed"])
def test_complete_negative_financial_fact_needs_correction(policy, field):
    snapshot, checks = raw_case()
    checks["financials"]["payload"][field] = False
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "NEEDS_INFO"
    assert not result["offers"]
    assert result["requested_fields"]


@pytest.mark.parametrize("state", ["PENDING", "ERROR"])
def test_provider_failure_is_waiting_not_bad_history(policy, state):
    snapshot, checks = raw_case()
    checks["credit_history"].update(state=state, payload=None)
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "WAITING_CHECKS"
    assert result["requested_assessment"]["score_total"] is None
    assert not result["offers"]
    if state == "ERROR":
        assert "CHECK_PROVIDER_UNAVAILABLE" in codes(result)


@pytest.mark.parametrize("check_name", ["company", "credit_history", "financials", "debt", "business_profile"])
def test_expired_required_check_waits(policy, check_name):
    snapshot, checks = raw_case()
    checks[check_name]["checked_on"] = "2026-09-01"
    checks[check_name]["valid_until"] = "2026-10-20"
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "WAITING_CHECKS"
    assert "CHECK_EXPIRED" in codes(result)


def test_freshness_end_date_is_inclusive(policy):
    snapshot, checks = raw_case()
    for name in ("company", "credit_history", "financials", "debt", "business_profile"):
        checks[name]["valid_until"] = snapshot["as_of_date"]
    assert run(snapshot, checks, policy)["recommendation"] == "OFFERED"


def test_expired_inactive_company_fact_does_not_prove_decline(policy):
    snapshot, checks = raw_case()
    checks["company"]["payload"]["is_active"] = False
    checks["company"].update(checked_on="2026-09-01", valid_until="2026-10-20")
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "WAITING_CHECKS"
    assert "CHECK_EXPIRED" in codes(result)


@pytest.mark.parametrize("field", ["checked_on", "registration_date", "valuation_date", "approval_date"])
def test_future_dates_are_input_errors(policy, field):
    snapshot, checks = raw_case("F04b" if field in ("valuation_date", "approval_date") else "F01")
    if field == "checked_on":
        checks["company"][field] = "2026-10-22"
    elif field == "registration_date":
        checks["company"]["payload"][field] = "2026-10-22"
    else:
        checks["asset_checks"][0]["payload"][field] = "2026-10-22"
    with pytest.raises(InputValidationError):
        run(snapshot, checks, policy)


@pytest.mark.parametrize("field,value", [("window_start", "2025-10-22"), ("window_end", "2026-10-19")])
def test_history_window_must_cover_required_year(policy, field, value):
    snapshot, checks = raw_case()
    checks["credit_history"]["payload"][field] = value
    original = deepcopy((snapshot, checks))
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "WAITING_CHECKS"
    assert "HISTORY_UNAVAILABLE" in codes(result)
    reason = next(item for item in result["reasons"] if item["code"] == "HISTORY_UNAVAILABLE")
    expected_windows = {
        "required_window_start": "2025-10-21",
        "required_window_end": "2026-10-20",
        "actual_window_start": checks["credit_history"]["payload"]["window_start"],
        "actual_window_end": checks["credit_history"]["payload"]["window_end"],
    }
    assert expected_windows.items() <= reason["params"].items()
    assert reason["next_action"] == "REQUEST_FULL_HISTORY"
    assert "REQUEST_FULL_HISTORY" in {action["code"] for action in result["next_actions"]}
    assert result["requested_assessment"]["score_total"] is None
    assert not result["offers"]
    assert (snapshot, checks) == original


def test_full_history_coverage_does_not_require_twelve_months_of_actual_loans(policy):
    """window_* describes checked coverage, not first/last loan activity.

    A backend may have observed actual loans only in the last six months. There
    is intentionally no loan-age field in the DTO and no invented input feature.
    Complete coverage plus has_history=True is the full relevant contract.
    """
    snapshot, checks = raw_case()
    checks["credit_history"]["evidence_refs"] = ["synthetic-full-year-coverage-six-month-loan-activity"]
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "OFFERED"
    assert result["requested_assessment"]["score_components"]["history"] == 25


def test_full_window_confirmed_no_history_keeps_no_history_category(policy):
    snapshot, checks = raw_case()
    checks["credit_history"]["payload"]["has_history"] = False
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "OFFERED"
    assert result["requested_assessment"]["score_components"]["history"] == 15
    assert "HISTORY_UNAVAILABLE" not in codes(result)


def test_reversed_history_window_is_structural_error(policy):
    snapshot, checks = raw_case()
    checks["credit_history"]["payload"].update(window_start="2026-10-21", window_end="2026-10-20")
    with pytest.raises(InputValidationError) as caught:
        run(snapshot, checks, policy)
    assert "window" in str(caught.value)


def test_inactive_company_overrides_incomplete_history_coverage(policy):
    snapshot, checks = raw_case()
    checks["company"]["payload"]["is_active"] = False
    checks["credit_history"]["payload"]["window_start"] = "2026-04-21"
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "DECLINED"
    assert "COMPANY_INACTIVE" in codes(result)
    assert "REQUEST_FULL_HISTORY" not in {action["code"] for action in result["next_actions"]}


def test_young_company_retains_short_history_route_without_full_year_request(policy):
    snapshot, checks = raw_case("F07")
    checks["credit_history"]["payload"]["window_start"] = "2026-01-15"
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "MANUAL_REVIEW"
    assert "SHORT_OPERATING_HISTORY" in codes(result)
    assert "REQUEST_FULL_HISTORY" not in {action["code"] for action in result["next_actions"]}
    assert not result["requested_fields"]


@pytest.mark.parametrize("start,expected", [("2023-02-28", "OFFERED"), ("2023-03-01", "WAITING_CHECKS")])
def test_history_calendar_year_leap_anniversary_clamps_to_february28(policy, start, expected):
    snapshot, checks = raw_case()
    snapshot["as_of_date"] = "2024-02-29"
    months = [f"2023-{month:02}" for month in range(2, 13)] + ["2024-01"]
    for period, month in zip(snapshot["financial_periods"], months, strict=True):
        period["month"] = month
    for name in ("company", "credit_history", "financials", "debt", "business_profile"):
        checks[name].update(checked_on="2024-02-29", valid_until="2024-03-30")
    checks["credit_history"]["payload"].update(window_start=start, window_end="2024-02-28")
    assert run(snapshot, checks, policy)["recommendation"] == expected


@pytest.mark.parametrize("month", ["2026-10", "2025-09"])
def test_financial_month_outside_window_is_error(policy, month):
    snapshot, checks = raw_case()
    snapshot["financial_periods"][0]["month"] = month
    with pytest.raises(InputValidationError):
        run(snapshot, checks, policy)


@pytest.mark.parametrize("field", ["active_overdue_days", "max_closed_overdue_days"])
def test_no_history_with_overdue_is_contradictory(policy, field):
    snapshot, checks = raw_case()
    checks["credit_history"]["payload"].update(has_history=False)
    checks["credit_history"]["payload"][field] = 1
    with pytest.raises(InputValidationError):
        run(snapshot, checks, policy)


def test_multiple_issues_preserve_precedence_and_all_causes(policy):
    snapshot, checks = raw_case()
    snapshot["financial_periods"][0]["tax_payments"] = None
    checks["credit_history"].update(state="ERROR", payload=None)
    checks["business_profile"]["payload"]["cashflow_pattern"] = "SEASONAL"
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "NEEDS_INFO"
    assert {"FINANCIAL_VALUE_MISSING", "CHECK_PROVIDER_UNAVAILABLE", "SEASONAL_CASHFLOW"} <= codes(result)


def test_no_external_io_or_randomness_inside_evaluate(policy, monkeypatch):
    raw_snapshot, raw_checks = raw_case()
    snapshot = ApplicationSnapshot.model_validate(raw_snapshot)
    checks = NormalizedChecks.model_validate(raw_checks)
    baseline = evaluate(snapshot, checks, policy).model_dump(mode="json")

    def forbidden(*args, **kwargs):
        raise AssertionError("Pure evaluation must not access external state")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(random, "random", forbidden)
        patch.setattr(uuid, "uuid4", forbidden)
        assert evaluate(snapshot, checks, policy).model_dump(mode="json") == baseline


def test_caller_decimal_precision_does_not_change_result(policy):
    snapshot, checks = raw_case()
    expected = run(snapshot, checks, policy)
    with localcontext() as context:
        context.prec = 6
        actual = run(snapshot, checks, policy)
    assert actual == expected


def test_order_independent_lists_have_identical_canonical_results(policy):
    snapshot, checks = raw_case("F05")
    second = deepcopy(snapshot["assets"][0])
    second.update(asset_id="synthetic-asset-002", object_key="VEHICLE:SYNTHETIC:002")
    snapshot["assets"].append(second)
    second_check = deepcopy(checks["asset_checks"][0])
    second_check["asset_id"] = second["asset_id"]
    checks["asset_checks"].append(second_check)
    for period in snapshot["financial_periods"]:
        period["source_refs"] += ["synthetic-ref-b", "synthetic-ref-a"]
    for item in checks["asset_checks"]:
        item["evidence_refs"] += ["synthetic-evidence-b", "synthetic-evidence-a"]
    first = run(snapshot, checks, policy)
    snapshot["financial_periods"].reverse()
    snapshot["assets"].reverse()
    checks["asset_checks"].reverse()
    for period in snapshot["financial_periods"]:
        period["source_refs"].reverse()
    for item in checks["asset_checks"]:
        item["evidence_refs"].reverse()
    assert run(snapshot, checks, policy) == first


def test_equivalent_decimal_rate_spellings_have_same_policy_hash(policy):
    snapshot, checks = raw_case()
    before = run(snapshot, checks, policy)
    alternative_data = policy.model_dump(mode="json")
    products = alternative_data["products"]
    for product in products.values() if isinstance(products, dict) else products:
        product["annual_rate"] = "0.240000"
    equivalent = PolicyConfig.model_validate(alternative_data)
    assert run(snapshot, checks, equivalent) == before


def test_unverified_ip_does_not_change_unsecured_economics(policy):
    snapshot, checks = raw_case()
    bare = run(snapshot, checks, policy)
    with_ip_snapshot, with_ip_checks = raw_case("F03")
    with_ip = run(with_ip_snapshot, with_ip_checks, policy)
    assert with_ip["financial_summary"] == bare["financial_summary"]
    for first, second in zip(bare["offers"], with_ip["offers"], strict=True):
        for key in ("amount", "term_months", "monthly_payment", "score_total", "cash_limit", "funding_gap"):
            assert first[key] == second[key]


def test_offers_remain_inside_selected_product(policy):
    snapshot, checks = raw_case("F04b")
    result = run(snapshot, checks, policy)
    assert result["offers"]
    assert {item["product_id"] for item in result["candidates"]} == {"EQUIPMENT"}
    assert {item["term_months"] for item in result["candidates"]} == {12, 24, 36}


def test_extra_collateral_cannot_change_cash_limit_or_score(policy):
    snapshot, checks = raw_case("F05")
    small = run(snapshot, checks, policy)
    checks["asset_checks"][0]["payload"]["assessed_value"] = "10000000.00"
    big = run(snapshot, checks, policy)
    for key in ("cash_limit", "score_total", "score_components", "dscr"):
        assert big["requested_assessment"][key] == small["requested_assessment"][key]
    assert big["financial_summary"] == small["financial_summary"]


@pytest.mark.parametrize("debt", ["0.00", "10000.00", "39999.99"])
def test_reducing_debt_never_reduces_cash_limit(policy, debt):
    snapshot, checks = raw_case()
    before = run(snapshot, checks, policy)
    snapshot["existing_monthly_debt"] = debt
    after = run(snapshot, checks, policy)
    assert Decimal(after["requested_assessment"]["cash_limit"]) >= Decimal(before["requested_assessment"]["cash_limit"])


@pytest.mark.parametrize("amount,need", [("2000000.01", "2000000.01"), ("2000500.00", "1900555.55"), ("4200000.00", "4200000.00"), ("100001.23", "2000000.00")])
def test_offers_obey_limits_preserve_exact_requests_and_step_reductions(policy, amount, need):
    snapshot, checks = raw_case()
    snapshot.update(requested_amount=amount, financing_need=need)
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "OFFERED"
    if Decimal(amount) <= min(Decimal(need), Decimal("2000000.01")):
        assert result["offers"][0]["amount"] == amount
        assert result["offers"][0]["term_months"] == 24
    for offer in result["offers"]:
        offered = Decimal(offer["amount"])
        assert Decimal("100000.00") <= offered <= min(Decimal(amount), Decimal(need), Decimal("3000000.00"))
        assert offered <= Decimal(offer["cash_limit"])
        if offered < Decimal(amount):
            assert offered % Decimal("10000") == 0
        assert Decimal(offer["monthly_payment"]) * (offer["term_months"] - 1) + Decimal(offer["last_payment"]) == Decimal(offer["total_payment"])
        assert Decimal(offer["total_payment"]) - offered == Decimal(offer["interest"])


def test_manual_candidates_cannot_override_cashflow_capacity(policy):
    snapshot, checks = raw_case()
    checks["credit_history"]["payload"]["max_closed_overdue_days"] = 31
    for period in snapshot["financial_periods"]:
        period["operating_receipts"] = "480000.00"
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "MANUAL_REVIEW"
    assert not result["offers"]
    assert result["requested_assessment"]["status"] == "REJECTED"
    reviews = [item for item in result["candidates"] if item["status"] == "REVIEW"]
    assert reviews
    for item in reviews:
        payment = independent_payment(item["amount"], item["term_months"], item["annual_rate"])
        assert Decimal("80000") >= Decimal("1.30") * (Decimal("40000") + payment)
        assert 50 <= item["score_total"] <= 79


def test_known_collateral_offer_has_priority_over_missing_additional_asset(policy):
    snapshot, checks = raw_case("F05")
    ip_snapshot, _ = raw_case("F03")
    extra = deepcopy(ip_snapshot["assets"][0])
    extra["asset_id"] = "synthetic-extra-ip"
    snapshot["assets"].append(extra)
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "OFFERED"
    assert result["offers"][0]["amount"] == "900000.00"


def test_absent_collateral_requires_addition_when_financially_feasible(policy):
    snapshot, checks = raw_case()
    snapshot.update(product_id="EQUIPMENT", purpose="EQUIPMENT_PURCHASE")
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "NEEDS_INFO"
    assert "ADD_COLLATERAL" in json.dumps(result["next_actions"])


@pytest.mark.parametrize("state,expected", [("MISSING", "NEEDS_INFO"), ("PENDING", "WAITING_CHECKS"), ("ERROR", "WAITING_CHECKS"), ("UNRESOLVED", "MANUAL_REVIEW")])
def test_needed_material_asset_routes_to_its_actual_check_state(policy, state, expected):
    snapshot, checks = raw_case("F05")
    checks["asset_checks"][0].update(state=state, payload=None)
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == expected
    assert result["asset_results"][0]["accepted_value"] is None


@pytest.mark.parametrize("age,expected", [(180, "OFFERED"), (181, "WAITING_CHECKS")])
def test_valuation_max_age_boundary(policy, age, expected):
    snapshot, checks = raw_case("F05")
    checks["asset_checks"][0]["payload"]["valuation_date"] = (date(2026, 10, 21) - timedelta(days=age)).isoformat()
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == expected
    if age == 181:
        assert "VALUATION_EXPIRED" in codes(result)
        assert result["asset_results"][0]["accepted_value"] is None


def test_ineligible_asset_does_not_require_useless_valuation(policy):
    snapshot, checks = raw_case("F05")
    checks["asset_checks"][0]["payload"] = {"pledge_eligible": False}
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "DECLINED"
    assert "COLLATERAL_INSUFFICIENT" in codes(result)


def test_foreign_owner_is_manual_when_needed(policy):
    snapshot, checks = raw_case("F05")
    snapshot["assets"][0]["owner_id"] = "synthetic-other-owner"
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "MANUAL_REVIEW"


def test_uncertain_asset_can_prevent_final_collateral_decline(policy):
    snapshot, checks = raw_case("F14/zero_recovery")
    pending_snapshot, pending_checks = raw_case("F03")
    extra = deepcopy(pending_snapshot["assets"][0])
    extra["asset_id"] = "synthetic-second-ip"
    snapshot["assets"].append(extra)
    extra_check = deepcopy(pending_checks["asset_checks"][0])
    extra_check.update(asset_id=extra["asset_id"], state="UNRESOLVED")
    checks["asset_checks"].append(extra_check)
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == "MANUAL_REVIEW"
    assert not result["offers"]


def test_declared_asset_price_cannot_change_accepted_value(policy):
    snapshot, checks = raw_case("F05")
    before = run(snapshot, checks, policy)
    snapshot["assets"][0]["declared_value"] = "9999999999999999.99"
    after = run(snapshot, checks, policy)
    assert after["asset_results"][0]["accepted_value"] == before["asset_results"][0]["accepted_value"] == "900000.00"
    assert after["offers"][0]["amount"] == before["offers"][0]["amount"]


def test_asset_check_cannot_reference_an_absent_asset(policy):
    snapshot, checks = raw_case("F05")
    checks["asset_checks"][0]["asset_id"] = "not-in-snapshot"
    with pytest.raises(InputValidationError):
        run(snapshot, checks, policy)


@pytest.mark.parametrize("amount,expected", [("99999.99", "DECLINED"), ("100000.00", "OFFERED"), ("100000.01", "OFFERED")])
def test_product_minimum_amount_boundary_is_credit_logic(policy, amount, expected):
    snapshot, checks = raw_case()
    snapshot["requested_amount"] = amount
    result = run(snapshot, checks, policy)
    assert result["recommendation"] == expected
    if expected == "OFFERED":
        assert result["offers"][0]["amount"] == amount


def test_manual_ip_never_applies_market_deductions_twice(policy):
    snapshot, checks = raw_case("F04b")
    checks["asset_checks"][0]["payload"].update(assessed_value="1.00", liquidation_costs="9000000.00", senior_claims="9000000.00")
    result = run(snapshot, checks, policy)
    assert result["asset_results"][0]["accepted_value"] == "2400000.00"
    assert result["recommendation"] == "OFFERED"


@pytest.mark.parametrize("change", ["mock_source", "wrong_asset_type", "missing_decision", "missing_approval"])
def test_manual_recovery_requirements_are_not_client_flags(policy, change):
    snapshot, checks = raw_case("F04b")
    item = checks["asset_checks"][0]
    if change == "mock_source":
        item.update(source_type="MOCK", fixture_version="synthetic-untrusted-claim")
    elif change == "wrong_asset_type":
        snapshot["assets"][0]["asset_type"] = "VEHICLE"
    elif change == "missing_decision":
        item["payload"]["manual_decision_ref"] = None
    else:
        item["payload"]["approval_date"] = None
    with pytest.raises(InputValidationError):
        run(snapshot, checks, policy)


@pytest.mark.parametrize("overdue,points", [(0, 25), (1, 10), (30, 10), (31, 0)])
def test_history_points_at_bucket_boundaries(policy, overdue, points):
    snapshot, checks = raw_case()
    checks["credit_history"]["payload"]["max_closed_overdue_days"] = overdue
    result = run(snapshot, checks, policy)
    assert result["requested_assessment"]["score_components"]["history"] == points


@pytest.mark.parametrize("positive,points", [(0, 0), (6, 0), (7, 15), (9, 15), (10, 25), (12, 25)])
def test_stability_points_at_bucket_boundaries(policy, positive, points):
    _, checks = raw_case()
    assessment = candidate_score(Decimal("200000"), Decimal("40000"), Decimal("100000"), NormalizedChecks.model_validate(checks), positive, date(2026, 10, 21), policy)
    assert assessment.components.stability == points


@pytest.mark.parametrize("ratio,points", [("1.0999999", 0), ("1.10", 15), ("1.1000001", 15), ("1.2999999", 15), ("1.30", 30), ("1.3000001", 30), ("1.4999999", 30), ("1.50", 40), ("1.5000001", 40)])
def test_payment_points_compare_unrounded_boundaries(policy, ratio, points):
    assert payment_points(Decimal(ratio) * 100, Decimal(0), Decimal(100), policy) == points


@pytest.mark.parametrize("registration,months,points", [("2025-10-21", 12, 5), ("2024-10-22", 23, 5), ("2024-10-21", 24, 10)])
def test_tenure_boundaries_include_day_of_month(policy, registration, months, points):
    _, checks = raw_case()
    checks["company"]["payload"]["registration_date"] = registration
    assert full_months(date.fromisoformat(registration), date(2026, 10, 21)) == months
    assessment = candidate_score(Decimal("200000"), Decimal("40000"), Decimal("100000"), NormalizedChecks.model_validate(checks), 12, date(2026, 10, 21), policy)
    assert assessment.components.tenure == points
