"""Final contract and adapter checks beyond the original fixed scenarios."""
from copy import deepcopy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path

import pytest

from credit_engine import evaluate
from credit_engine.errors import InputValidationError
from credit_engine.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]


def inputs(case="F01"):
    directory = ROOT / "examples" / case
    return (json.loads((directory / "snapshot.json").read_text(encoding="utf-8")),
            json.loads((directory / "checks.json").read_text(encoding="utf-8")),
            load_policy(ROOT / "policies/demo-assets-2.json"))


@pytest.mark.parametrize("value", ["1.٠٠", "1.００"])
def test_money_wire_format_is_ascii(value):
    snapshot, checks, policy = inputs()
    snapshot["requested_amount"] = value
    with pytest.raises(InputValidationError) as error:
        evaluate(snapshot, checks, policy)
    assert "requested_amount" in error.value.errors[0]["path"]


def test_unrepresentable_prior_year_has_input_error():
    snapshot, checks, policy = inputs()
    snapshot["as_of_date"] = "0001-01-01"
    with pytest.raises(InputValidationError) as error:
        evaluate(snapshot, checks, policy)
    assert error.value.errors[0]["path"] == "snapshot.as_of_date"


def test_available_offer_is_preserved_with_optional_appraisal_action():
    snapshot, checks, policy = inputs("F05")
    ip, ip_checks, _ = inputs("F03")
    ip["assets"][0]["asset_id"] = "extra-ip"
    ip_checks["asset_checks"][0]["asset_id"] = "extra-ip"
    snapshot["assets"].append(deepcopy(ip["assets"][0]))
    checks["asset_checks"].append(deepcopy(ip_checks["asset_checks"][0]))
    result = evaluate(snapshot, checks, policy)
    assert result.recommendation == "OFFERED"
    assert result.offers[0].model_dump(mode="json")["amount"] == "900000.00"
    assert not result.requested_fields
    assert any(action.code == "OPTIONAL_COLLATERAL_ASSESSMENT" for action in result.next_actions)


def test_client_adapter_does_not_forward_bank_messages_or_internal_candidates():
    spec = importlib.util.spec_from_file_location("backend_adapter", ROOT / "examples/backend_adapter.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.evaluate_saved_fixture(ROOT / "examples/F12/working_capital")
    public = module.client_view(result)
    assert "candidates" not in public
    assert "input_fingerprint" not in public
    assert all(item["audience"] == "CLIENT" for item in public["reasons"] + public["next_actions"])
    assert all("score_components" not in offer for offer in public["offers"])


def test_u01_cash_limit_keeps_product_minimum_while_score_limit_is_explained():
    snapshot, checks, policy = inputs("F12/working_capital")
    result = evaluate(snapshot, checks, policy)
    candidate = next(c for c in result.candidates if c.amount == Decimal("1760000") and c.term_months == 24)
    assert candidate.cash_limit == Decimal("2153277.68")
    detail = next(r for r in result.reasons if r.code == "LOWER_AMOUNT_IMPROVES_SCORE" and r.subject_id == candidate.candidate_key)
    assert detail.params["score_cash_limit"] == "1765299.72"
    assert Decimal(detail.params["dscr_threshold"]) == Decimal("1.50")
    assert detail.params["score_before"] == 75 and detail.params["score_after"] == 85
