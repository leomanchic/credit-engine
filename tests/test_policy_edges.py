"""Reject policy configurations unsupported by the finite-boundary algorithm."""

from pathlib import Path

import pytest

from credit_engine.errors import PolicyValidationError
from credit_engine.policy import PolicyConfig, load_policy


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return load_policy(ROOT / "policies" / "demo-assets-2.json").model_dump(mode="json")


@pytest.mark.parametrize(
    "field,value",
    [
        ("min_amount", "3000001.00"),
        ("amount_step", "0.00"),
        ("terms_months", []),
        ("terms_months", [12, 12]),
        ("annual_rate", "-0.01"),
        ("annual_rate", "1.01"),
        ("annual_rate", 0.24),
        ("annual_rate", "0.0000000000001"),
        ("min_dscr", "0"),
        ("max_offers", 0),
    ],
)
def test_invalid_product_configurations(config, field, value):
    config["products"][0][field] = value
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


def test_required_collateral_cannot_have_zero_coverage_ratio(config):
    secured = next(product for product in config["products"] if product["collateral_required"])
    secured["coverage_ratio"] = "0"
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


@pytest.mark.parametrize("value", [33, 0, True])
def test_precision_cannot_weaken_numeric_contract(config, value):
    config["rounding"]["precision"] = value
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


def test_payment_thresholds_must_be_strictly_ordered(config):
    config["scoring"]["payment_bands"][-1]["min_dscr"] = "1.20"
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


def test_payment_bands_must_cover_values_below_first_threshold(config):
    config["scoring"]["payment_bands"].pop(0)
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


def test_history_bands_must_cover_arbitrarily_long_overdue(config):
    config["scoring"]["history_bands"].pop()
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


def test_stability_bands_must_include_zero_positive_months(config):
    config["scoring"]["stability_bands"].pop(0)
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


def test_manual_recovery_cannot_be_enabled_for_material_assets(config):
    config["collateral"]["manual_recovery_types"] = ["VEHICLE"]
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


@pytest.mark.parametrize("haircut", ["-0.01", "1.01", 0.35])
def test_haircut_must_be_a_valid_decimal_ratio(config, haircut):
    config["collateral"]["market_haircuts"][0]["haircut"] = haircut
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


def test_unknown_policy_fields_are_rejected(config):
    config["hidden_industry_multiplier"] = "0.8"
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


@pytest.mark.parametrize("field", ["payment_bands", "stability_bands", "tenure_bands"])
def test_scores_cannot_decrease_when_the_indicator_improves(config, field):
    config["scoring"][field][-1]["points"] = 0
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


def test_more_overdue_days_cannot_increase_history_score(config):
    config["scoring"]["history_bands"][-1]["points"] = 20
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


def test_every_material_asset_type_requires_exactly_one_haircut(config):
    config["collateral"]["market_haircuts"].pop()
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)


def test_review_and_automatic_score_ranges_must_not_overlap(config):
    config["products"][0]["auto_score_min"] = config["scoring"]["review_score_min"]
    with pytest.raises(PolicyValidationError):
        PolicyConfig.model_validate(config)
