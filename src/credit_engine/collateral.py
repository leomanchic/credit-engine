"""Confirmed collateral contributions and unresolved per-asset routes.

This module never makes an application-level decision: an optional asset issue
may only matter after the offer search establishes that collateral is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from .finance import ONE, ZERO, calculation_context

if TYPE_CHECKING:
    from .models import ApplicationSnapshot, AssetResult, NormalizedChecks
    from .policy import PolicyConfig


@dataclass(frozen=True)
class AssetIssue:
    asset_id: str
    recommendation: str
    code: str
    path: str
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CollateralSummary:
    results: tuple[AssetResult, ...]
    accepted_collateral: Decimal
    collateral_limit: Decimal | None
    applicable: bool
    unresolved: tuple[AssetIssue, ...]


def market_recovery(
    assessed_value: Decimal,
    haircut: Decimal,
    liquidation_costs: Decimal,
    senior_claims: Decimal,
    *,
    precision: int = 34,
) -> Decimal:
    """Net contribution from an accepted valuation, never a market appraisal."""
    for value in (assessed_value, haircut, liquidation_costs, senior_claims):
        if not isinstance(value, Decimal) or not value.is_finite() or value < ZERO:
            raise ValueError("collateral inputs must be finite nonnegative Decimals")
    if haircut > ONE:
        raise ValueError("haircut cannot exceed one")
    with calculation_context(precision):
        return max(ZERO, assessed_value * (ONE - haircut) - liquidation_costs - senior_claims)


def evaluate_collateral(
    snapshot: ApplicationSnapshot,
    checks: NormalizedChecks,
    policy: PolicyConfig,
) -> CollateralSummary:
    """Assess each unique asset; structural duplicate checks belong to validation."""
    from .models import AssetResult

    product = policy.product(snapshot.product_id)
    by_asset = {check.asset_id: check for check in checks.asset_checks}
    results = []
    unresolved = []
    accepted = ZERO
    for asset in sorted(snapshot.assets, key=lambda item: item.asset_id):
        check = by_asset.get(asset.asset_id)
        payload = check.payload if check is not None else None
        refs = tuple(sorted(set(asset.source_refs + (check.evidence_refs if check is not None else ()))))
        contribution = None
        eligible = None
        method = payload.valuation_method if payload is not None else None
        haircut = None
        decision_ref = payload.manual_decision_ref if payload is not None else None
        code = None
        route = None
        status = "UNKNOWN"
        path = f"checks.asset_checks[{asset.asset_id}]"
        if check is None or check.state == "MISSING":
            route, code = "NEEDS_INFO", "ASSET_DOCUMENTS_REQUIRED"
        elif check.state in ("PENDING", "ERROR"):
            route = "WAITING_CHECKS"
            code = "CHECK_PROVIDER_UNAVAILABLE" if check.state == "ERROR" else "ASSET_CHECK_PENDING"
        elif check.valid_until < snapshot.as_of_date:
            route, code = "WAITING_CHECKS", "CHECK_EXPIRED"
        elif check.state == "UNRESOLVED":
            route, code = "MANUAL_REVIEW", "ASSET_REVIEW_REQUIRED"
        elif payload.pledge_eligible is False:
            contribution, eligible = ZERO, False
            status, code = "INELIGIBLE", "ASSET_INELIGIBLE"
        elif (
            not policy.collateral.allow_junior_lien
            and payload.senior_claims is not None
            and payload.senior_claims > ZERO
        ):
            contribution, eligible = ZERO, False
            status, code = "INELIGIBLE", "ASSET_INELIGIBLE"
        elif (
            asset.asset_type == "OTHER"
            or asset.owner_id != snapshot.company_id
            or payload.ownership_confirmed is not True
            or payload.pledge_eligible is not True
            or payload.exclusive_allocation is not True
        ):
            route, code = "MANUAL_REVIEW", "ASSET_REVIEW_REQUIRED"
        elif asset.asset_type == "IP_RIGHT" and payload.valuation_method != "MANUAL_RECOVERY":
            route, code = "MANUAL_REVIEW", "ASSET_REVIEW_REQUIRED"
        elif payload.valuation_date is None:
            route, code = "NEEDS_INFO", "ASSET_DOCUMENTS_REQUIRED"
        elif (snapshot.as_of_date - payload.valuation_date).days > policy.collateral.max_valuation_age_days:
            route, code = "WAITING_CHECKS", "VALUATION_EXPIRED"
        elif payload.valuation_method == "MANUAL_RECOVERY":
            if (
                asset.asset_type not in policy.collateral.manual_recovery_types
                or check.source_type != "BANK_OPERATOR"
                or payload.manual_recovery_value is None
                or payload.manual_decision_ref is None
                or payload.approval_date is None
            ):
                route, code = "MANUAL_REVIEW", "ASSET_REVIEW_REQUIRED"
            else:
                # Already net of all discounts, costs and senior claims.
                contribution = payload.manual_recovery_value
                eligible, status = True, "ACCEPTED"
        elif payload.valuation_method == "MARKET_VALUE":
            haircut = policy.collateral.haircut(asset.asset_type)
            if haircut is None:
                route, code = "MANUAL_REVIEW", "ASSET_REVIEW_REQUIRED"
            elif any(value is None for value in (payload.assessed_value, payload.liquidation_costs, payload.senior_claims)):
                route, code = "NEEDS_INFO", "ASSET_DOCUMENTS_REQUIRED"
            else:
                contribution = market_recovery(
                    payload.assessed_value,
                    haircut,
                    payload.liquidation_costs,
                    payload.senior_claims,
                    precision=policy.rounding.precision,
                )
                eligible, status = True, "ACCEPTED"
        else:
            route, code = "NEEDS_INFO", "ASSET_DOCUMENTS_REQUIRED"

        if route is not None:
            unresolved.append(AssetIssue(asset.asset_id, route, code, path, refs))
            if route == "MANUAL_REVIEW":
                status = "REVIEW"
        if contribution is not None:
            with calculation_context(policy.rounding.precision):
                accepted += contribution
        results.append(
            AssetResult(
                asset_id=asset.asset_id,
                object_key=asset.object_key,
                asset_type=asset.asset_type,
                accepted_value=contribution,
                eligible=eligible,
                status=status,
                valuation_method=method,
                haircut=haircut,
                manual_decision_ref=decision_ref,
                source_type=check.source_type if check is not None else None,
                source_refs=refs,
                reason_codes=(code,) if code is not None else (),
                required_route=route,
                collateral_applicable=product.collateral_required,
            )
        )
    with calculation_context(policy.rounding.precision):
        limit = accepted / product.coverage_ratio if product.collateral_required else None
    return CollateralSummary(tuple(results), accepted, limit, product.collateral_required, tuple(unresolved))
