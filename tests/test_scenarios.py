"""Acceptance fixtures contain independent expectations, never evaluate snapshots."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from credit_engine import evaluate
from credit_engine.errors import InputValidationError
from credit_engine.models import ApplicationSnapshot, NormalizedChecks
from credit_engine.policy import load_policy


ROOT = Path(__file__).resolve().parents[1]
CASES = sorted((ROOT / "examples").rglob("expected.json"))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_case(directory):
    snapshot = read_json(directory / "snapshot.json")
    checks = read_json(directory / "checks.json")
    reference = read_json(directory / "policy_ref.json")
    policy = load_policy(directory / reference["path"])
    assert policy.policy_version == reference["policy_version"]
    return snapshot, checks, policy


def calculate(directory):
    snapshot, checks, policy = load_case(directory)
    return evaluate(ApplicationSnapshot.model_validate(snapshot), NormalizedChecks.model_validate(checks), policy)


def assert_subset(actual, expected, path="result"):
    if isinstance(expected, dict):
        for key, value in expected.items():
            assert key in actual, f"{path}.{key} is missing"
            assert_subset(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, list):
        assert actual == expected, path
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


def reason_codes(result):
    codes = {reason["code"] for reason in result["reasons"]}
    for candidate in [result["requested_assessment"], *result["candidates"]]:
        codes.update(candidate.get("reason_codes", []))
    return codes


@pytest.mark.parametrize("expected_path", CASES, ids=lambda path: path.parent.relative_to(ROOT / "examples").as_posix())
def test_acceptance_fixture(expected_path):
    directory = expected_path.parent
    expected = read_json(expected_path)
    assert expected["basis"], "Every expectation must explain its independent basis"
    if "error" in expected:
        with pytest.raises(InputValidationError) as caught:
            evaluate(*load_case(directory))
        error_text = str(caught.value)
        assert expected["error_path_contains"] in error_text
        return

    result = calculate(directory).model_dump(mode="json")
    assert result["recommendation"] == expected["recommendation"]
    assert result["schema_version"] == "2.0"
    assert result["calculation_version"] == "2.0.0"
    assert len(result["policy_hash"]) == len(result["input_fingerprint"]) == 64
    assert len(result["offers"]) <= 3
    assert len({(item["amount"], item["term_months"]) for item in result["candidates"]}) == len(result["candidates"])
    for offer in result["offers"]:
        assert offer["status"] == "AUTO_ELIGIBLE"
        assert offer in result["candidates"]
        assert offer["score_total"] >= 80
        amount = Decimal(offer["amount"])
        assert amount <= Decimal(offer["cash_limit"])
        if offer["collateral_limit"] is not None:
            assert amount <= Decimal(offer["collateral_limit"])
        assert Decimal(offer["monthly_payment"]) * (offer["term_months"] - 1) + Decimal(offer["last_payment"]) == Decimal(offer["total_payment"])
    snapshot_data = read_json(directory / "snapshot.json")
    def ranking_key(item):
        exact_request = item["amount"] == snapshot_data["requested_amount"] and item["term_months"] == snapshot_data["requested_term_months"]
        return (not exact_request, Decimal(item["funding_gap"]), Decimal(item["total_payment"]), item["term_months"], Decimal(item["amount"]), item["candidate_key"])
    assert result["offers"] == sorted(result["offers"], key=ranking_key)
    for section in ("financial_summary", "requested_assessment"):
        if section in expected:
            assert_subset(result[section], expected[section], section)
    if "first_offer" in expected:
        assert result["offers"], "Expected an available offer"
        assert_subset(result["offers"][0], expected["first_offer"], "offers[0]")
    for key, section in (("candidates_contains", "candidates"), ("asset_results_contains", "asset_results")):
        for subset in expected.get(key, []):
            identity = ("amount", "term_months") if section == "candidates" else ("asset_id",)
            matches = [item for item in result[section] if all(item[field] == subset[field] for field in identity)]
            assert len(matches) == 1, f"Expected exactly one {section} item matching {subset}"
            assert_subset(matches[0], subset, section)
    if "offers_count" in expected:
        assert len(result["offers"]) == expected["offers_count"]
    if "max_offer_amount" in expected:
        assert all(Decimal(offer["amount"]) <= Decimal(expected["max_offer_amount"]) for offer in result["offers"])
    assert set(expected.get("reason_codes", [])) <= reason_codes(result)
    if expected.get("requested_fields_empty"):
        assert result["requested_fields"] == []
    for text in expected.get("requested_fields_contain_text", []):
        assert text in json.dumps(result["requested_fields"], ensure_ascii=False)
    if expected.get("metamorphic") == "repeatable_immutable":
        snapshot_data, checks_data, policy = load_case(directory)
        snapshot = ApplicationSnapshot.model_validate(snapshot_data)
        checks = NormalizedChecks.model_validate(checks_data)
        before = deepcopy((snapshot.model_dump(mode="json"), checks.model_dump(mode="json"), policy.model_dump(mode="json")))
        assert evaluate(snapshot, checks, policy).model_dump(mode="json") == result
        assert evaluate(snapshot, checks, policy).model_dump(mode="json") == result
        assert (snapshot.model_dump(mode="json"), checks.model_dump(mode="json"), policy.model_dump(mode="json")) == before
    if "same_canonical_result_as" in expected:
        assert result == calculate(directory / expected["same_canonical_result_as"]).model_dump(mode="json")
    if "different_policy_hash_from" in expected:
        other = calculate(directory / expected["different_policy_hash_from"]).model_dump(mode="json")
        assert result["policy_hash"] != other["policy_hash"]
        assert result["input_fingerprint"] == other["input_fingerprint"]


def test_required_scenario_families_are_present():
    families = {path.relative_to(ROOT / "examples").parts[0] for path in CASES}
    assert families == {f"F{number:02}" for number in range(1, 18)} | {"F04b"}
    assert len(CASES) >= 40, "Multi-case scenarios need separate fixtures"
