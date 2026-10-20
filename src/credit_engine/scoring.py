"""Candidate-specific points read from the versioned credit policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from .finance import meets_dscr

if TYPE_CHECKING:
    from .models import NormalizedChecks, ScoreComponents
    from .policy import PolicyConfig


@dataclass(frozen=True)
class ScoreAssessment:
    components: ScoreComponents
    total: int | None


def full_months(start: date, end: date) -> int:
    """Completed monthly anniversaries, including the registration day."""
    if start > end:
        raise ValueError("registration date must not be in the future")
    return (end.year - start.year) * 12 + end.month - start.month - (end.day < start.day)


def payment_points(
    cashflow: Decimal,
    debt: Decimal,
    payment: Decimal,
    policy: PolicyConfig,
) -> int:
    bands = policy.scoring.payment_bands
    points = bands[0].points
    for band in bands:
        if band.min_dscr is None or meets_dscr(
            cashflow, debt, payment, band.min_dscr, precision=policy.rounding.precision
        ):
            points = band.points
    return points


def candidate_score(
    cashflow: Decimal | None,
    debt: Decimal | None,
    payment: Decimal | None,
    checks: NormalizedChecks,
    positive_months: int | None,
    as_of_date: date,
    policy: PolicyConfig,
) -> ScoreAssessment:
    """Unknown components stay null and never become a partial total."""
    from .models import ScoreComponents

    payment_score = None
    if cashflow is not None and debt is not None and payment is not None:
        payment_score = payment_points(cashflow, debt, payment, policy)

    history_score = None
    history = checks.credit_history
    if (
        history.state == "COMPLETE"
        and history.payload is not None
        and history.checked_on is not None
        and history.valid_until is not None
        and history.checked_on <= as_of_date <= history.valid_until
    ):
        payload = history.payload
        if payload.active_overdue_days == 0:
            if not payload.has_history:
                history_score = policy.scoring.history_no_history_points
            else:
                for band in policy.scoring.history_bands:
                    if band.max_days is None or payload.max_closed_overdue_days <= band.max_days:
                        history_score = band.points
                        break

    stability_score = None
    if positive_months is not None:
        for band in policy.scoring.stability_bands:
            if positive_months >= band.min_months:
                stability_score = band.points

    tenure_score = None
    company = checks.company
    if (
        company.state == "COMPLETE"
        and company.payload is not None
        and company.checked_on is not None
        and company.valid_until is not None
        and company.checked_on <= as_of_date <= company.valid_until
    ):
        months = full_months(company.payload.registration_date, as_of_date)
        if months >= policy.scoring.minimum_history_months:
            for band in policy.scoring.tenure_bands:
                if months >= band.min_months:
                    tenure_score = band.points

    base_values = (history_score, stability_score, tenure_score)
    base = sum(base_values) if all(value is not None for value in base_values) else None
    total = base + payment_score if base is not None and payment_score is not None else None
    return ScoreAssessment(
        components=ScoreComponents(
            payment=payment_score,
            history=history_score,
            stability=stability_score,
            tenure=tenure_score,
            base_score=base,
        ),
        total=total,
    )
