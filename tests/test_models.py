"""Boundary validation uses typed values and precise safe locations."""
from datetime import datetime
from decimal import Decimal, Inexact, localcontext
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from credit_engine.errors import InputValidationError
from credit_engine.models import ApplicationSnapshot, FinancialSummary, NormalizedChecks
from credit_engine.policy import load_policy, PolicyConfig

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def snapshot():
    return json.loads((ROOT / "examples/F01/snapshot.json").read_text(encoding="utf-8"))


@pytest.fixture
def checks():
    return json.loads((ROOT / "examples/F01/checks.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("value", [2000000.0, 2000000, True, "2000000", "2000000.0", "2e6", "NaN", "Infinity", "1.001", "10000000000000000.00", "-1.00", "0.00"])
def test_requested_money_cannot_be_coerced_or_outside_range(snapshot, value):
    snapshot["requested_amount"] = value
    with pytest.raises(InputValidationError) as caught:
        ApplicationSnapshot.model_validate(snapshot)
    assert caught.value.errors[0]["path"] == "requested_amount"
    assert caught.value.errors[0]["code"]


@pytest.mark.parametrize("constructor", [lambda data: ApplicationSnapshot(**data), ApplicationSnapshot.model_validate, lambda data: ApplicationSnapshot.model_validate_json(json.dumps(data))])
def test_nested_errors_preserve_exact_path_for_every_public_constructor(snapshot, constructor):
    snapshot["financial_periods"][3]["tax_payments"] = "not-money"
    with pytest.raises(InputValidationError) as caught:
        constructor(snapshot)
    assert caught.value.errors[0]["path"] == "financial_periods.3.tax_payments"
    assert "not-money" not in caught.value.errors[0]["message_ru"]


@pytest.mark.parametrize("field,value", [("revision", True), ("revision", "1"), ("requested_term_months", 24.0), ("as_of_date", "2026-02-30"), ("as_of_date", "2026-10-21T12:00:00"), ("as_of_date", datetime(2026, 10, 21)), ("purpose", "OTHER"), ("schema_version", "1.0"), ("unknown_field", "secret")])
def test_strict_scalar_contract(snapshot, field, value):
    snapshot[field] = value
    with pytest.raises(InputValidationError) as caught:
        ApplicationSnapshot.model_validate(snapshot)
    assert caught.value.errors[0]["path"] == field


def test_numeric18_boundary_and_serialization_ignore_global_context(snapshot):
    snapshot["financing_need"] = "9999999999999999.99"
    snapshot["existing_monthly_debt"] = Decimal("-0.00")
    with localcontext() as context:
        context.prec = 6
        context.traps[Inexact] = True
        result = ApplicationSnapshot.model_validate(snapshot)
        assert result.financing_need == Decimal("9999999999999999.99")
        payload = result.model_dump(mode="json")
        assert payload["financing_need"] == "9999999999999999.99"
        assert payload["existing_monthly_debt"] == "0.00"


def test_unknown_financial_values_remain_none(snapshot):
    snapshot["existing_monthly_debt"] = None
    snapshot["financial_periods"][0]["tax_payments"] = None
    result = ApplicationSnapshot.model_validate(snapshot)
    assert result.existing_monthly_debt is None
    assert result.financial_periods[0].tax_payments is None


def test_complete_requires_typed_payload(checks):
    checks["company"]["payload"] = None
    with pytest.raises(InputValidationError) as caught:
        NormalizedChecks.model_validate(checks)
    assert caught.value.errors[0]["path"] == "company.payload"


@pytest.mark.parametrize("field", ["checked_on", "valid_until"])
@pytest.mark.parametrize("state", ["COMPLETE", "UNRESOLVED"])
def test_resolved_check_states_require_dates(checks, field, state):
    checks["company"]["state"] = state
    checks["company"][field] = None
    with pytest.raises(InputValidationError) as caught:
        NormalizedChecks.model_validate(checks)
    assert caught.value.errors[0]["path"] == f"company.{field}"


def test_pending_check_can_have_unknown_payload_and_dates(checks):
    checks["credit_history"].update(state="PENDING", payload=None, checked_on=None, valid_until=None)
    result = NormalizedChecks.model_validate(checks)
    assert result.credit_history.payload is None


def test_no_history_cannot_contain_overdue_fact(checks):
    checks["credit_history"]["payload"].update(has_history=False, active_overdue_days=1)
    with pytest.raises(InputValidationError) as caught:
        NormalizedChecks.model_validate(checks)
    assert caught.value.errors[0]["path"] == "credit_history.payload.active_overdue_days"


def test_boolean_confirmation_cannot_be_truthy_string(checks):
    checks["company"]["payload"]["is_active"] = "false"
    with pytest.raises(InputValidationError) as caught:
        NormalizedChecks.model_validate(checks)
    assert caught.value.errors[0]["path"] == "company.payload.is_active"


def test_duplicate_period_is_rejected_at_duplicate_index(snapshot):
    snapshot["financial_periods"][1]["month"] = snapshot["financial_periods"][0]["month"]
    with pytest.raises(InputValidationError) as caught:
        ApplicationSnapshot.model_validate(snapshot)
    assert caught.value.errors[0]["path"] == "financial_periods.1.month"


def test_mock_requires_fixture_version(checks):
    checks["company"]["fixture_version"] = None
    with pytest.raises(InputValidationError) as caught:
        NormalizedChecks.model_validate(checks)
    assert caught.value.errors[0]["path"] == "company.fixture_version"


def test_inputs_are_deeply_immutable_and_not_aliased(snapshot):
    result = ApplicationSnapshot.model_validate(snapshot)
    with pytest.raises(ValidationError):
        result.financial_periods[0].tax_payments = Decimal("0.00")
    with pytest.raises(TypeError):
        result.financial_periods[0] = result.financial_periods[1]
    snapshot["financial_periods"][0]["source_refs"].append("later-source")
    assert "later-source" not in result.financial_periods[0].source_refs


def test_output_money_can_exceed_input_range_and_preserves_raw_average():
    value = Decimal("10000000000000000.123456789")
    summary = FinancialSummary(C=value)
    assert summary.C == value
    assert summary.model_dump(mode="json")["C"] == "10000000000000000.12"


def test_coefficient_redundant_zeroes_do_not_reduce_supported_domain():
    data = load_policy(ROOT / "policies/demo-assets-2.json").model_dump(mode="json")
    data["products"][0]["annual_rate"] = "0.2400000000000000000000000000"
    value = PolicyConfig.model_validate(data)
    assert value.products[0].annual_rate == Decimal("0.24")
