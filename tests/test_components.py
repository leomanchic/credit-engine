"""Focused component behavior beyond the acceptance fixture values."""

from datetime import date
from decimal import Decimal, Inexact, ROUND_DOWN, localcontext
import json
from pathlib import Path

import pytest

from credit_engine.collateral import evaluate_collateral
from credit_engine.finance import financial_summary, monthly_cashflow
from credit_engine.models import ApplicationSnapshot, NormalizedChecks
from credit_engine.policy import PolicyConfig, load_policy
from credit_engine.scoring import candidate_score, payment_points


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


def test_financial_summary_uses_lower_recent_flow_and_sorts_periods():
    data, _ = raw_case()
    for period in data["financial_periods"][-3:]:
        period["operating_receipts"] = "450000.00"
    data["financial_periods"].reverse()
    snapshot = ApplicationSnapshot.model_validate(data)
    result = financial_summary(snapshot.financial_periods, snapshot.existing_monthly_debt)
    assert result.C_12 == D("162500")
    assert result.C_3 == result.C == D("50000")
    assert result.positive_months == 12
    assert (result.period_start, result.period_end) == ("2025-10", "2026-09")


def test_negative_flow_is_retained_for_explanation():
    data, _ = raw_case()
    for period in data["financial_periods"]:
        period["operating_receipts"] = "300000.00"
    snapshot = ApplicationSnapshot.model_validate(data)
    assert monthly_cashflow(snapshot.financial_periods[0]) == D("-100000")
    result = financial_summary(snapshot.financial_periods, snapshot.existing_monthly_debt)
    assert result.C_12 == result.C_3 == result.C == D("-100000")
    assert result.positive_months == 0
    assert result.complete is True


@pytest.mark.parametrize("missing", ["period", "value"])
def test_incomplete_flow_does_not_use_zero_or_average_imputation(missing):
    data, _ = raw_case()
    if missing == "period":
        data["financial_periods"].pop()
    else:
        data["financial_periods"][-1]["tax_payments"] = None
    snapshot = ApplicationSnapshot.model_validate(data)
    result = financial_summary(snapshot.financial_periods, snapshot.existing_monthly_debt)
    assert result.C_12 is result.C_3 is result.C is None
    assert result.positive_months is None
    assert result.D == D("40000")
    assert result.complete is False


def test_unknown_debt_is_distinct_from_zero_with_known_cashflow():
    data, _ = raw_case()
    snapshot = ApplicationSnapshot.model_validate(data)
    unknown = financial_summary(snapshot.financial_periods, None)
    zero = financial_summary(snapshot.financial_periods, D("0"))
    assert unknown.D is None and unknown.complete is False
    assert zero.D == D("0") and zero.complete is True
    assert unknown.C == zero.C == D("200000")


def test_financial_summary_preserves_fractional_average_internally():
    data, _ = raw_case()
    data["financial_periods"][-1]["operating_receipts"] = "600000.01"
    snapshot = ApplicationSnapshot.model_validate(data)
    result = financial_summary(snapshot.financial_periods, snapshot.existing_monthly_debt)
    assert result.C > D("200000.00")
    assert result.C < D("200000.01")
    assert result.model_dump(mode="json")["C"] == "200000.00"


def test_component_summary_is_independent_of_caller_decimal_traps():
    data, _ = raw_case()
    data["financial_periods"][-1]["operating_receipts"] = "600000.01"
    snapshot = ApplicationSnapshot.model_validate(data)
    expected = financial_summary(snapshot.financial_periods, snapshot.existing_monthly_debt)
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = ROUND_DOWN
        ambient.traps[Inexact] = True
        actual = financial_summary(snapshot.financial_periods, snapshot.existing_monthly_debt)
        assert actual == expected


def test_output_serialization_ignores_caller_decimal_precision_and_traps():
    data, _ = raw_case()
    data["financial_periods"][-1]["operating_receipts"] = "600000.01"
    snapshot = ApplicationSnapshot.model_validate(data)
    result = financial_summary(snapshot.financial_periods, snapshot.existing_monthly_debt)
    expected = result.model_dump_json()
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = ROUND_DOWN
        ambient.traps[Inexact] = True
        assert result.model_dump_json() == expected
        assert ApplicationSnapshot.model_validate(data) == snapshot


def test_payment_bands_are_loaded_from_policy(policy):
    changed = policy.model_dump(mode="json")
    changed["scoring"]["payment_bands"][-1]["min_dscr"] = "1.70"
    alternative = PolicyConfig.model_validate(changed)
    assert payment_points(D("80000"), D("40000"), D("10000"), policy) == 40
    assert payment_points(D("80000"), D("40000"), D("10000"), alternative) == 30


def test_unknown_history_cannot_be_added_as_a_partial_score(policy):
    _, raw_checks = raw_case()
    raw_checks["credit_history"].update(state="PENDING", payload=None)
    checks = NormalizedChecks.model_validate(raw_checks)
    score = candidate_score(D("200000"), D("40000"), D("100000"), checks, 12, date(2026, 10, 21), policy)
    assert score.components.payment == 30
    assert score.components.history is None
    assert score.components.base_score is score.total is None


@pytest.mark.parametrize("name", ["F05", "F04b"])
def test_no_junior_lien_policy_excludes_known_senior_claims_even_for_manual_ip(policy, name):
    raw_snapshot, raw_checks = raw_case(name)
    raw_checks["asset_checks"][0]["payload"]["senior_claims"] = "100000.00"
    config = policy.model_dump(mode="json")
    config["collateral"]["allow_junior_lien"] = False
    alternative = PolicyConfig.model_validate(config)
    result = evaluate_collateral(
        ApplicationSnapshot.model_validate(raw_snapshot),
        NormalizedChecks.model_validate(raw_checks),
        alternative,
    )
    assert result.accepted_collateral == D("0")
    assert result.results[0].accepted_value == D("0")
    assert result.results[0].status == "INELIGIBLE"
    assert result.unresolved == ()


def test_collateral_zero_is_confirmed_while_missing_check_stays_unknown(policy):
    raw_snapshot, raw_checks = raw_case("F05")
    payload = raw_checks["asset_checks"][0]["payload"]
    payload.update(assessed_value="100000.00", liquidation_costs="65000.00", senior_claims="0.00")
    snapshot = ApplicationSnapshot.model_validate(raw_snapshot)
    zero = evaluate_collateral(snapshot, NormalizedChecks.model_validate(raw_checks), policy)
    raw_checks["asset_checks"] = []
    unknown = evaluate_collateral(snapshot, NormalizedChecks.model_validate(raw_checks), policy)
    assert zero.results[0].accepted_value == D("0")
    assert zero.results[0].status == "ACCEPTED"
    assert zero.unresolved == ()
    assert unknown.results[0].accepted_value is None
    assert unknown.unresolved[0].recommendation == "NEEDS_INFO"
