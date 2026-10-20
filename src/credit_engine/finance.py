"""Pure Decimal cash-flow calculations; no IO or ambient context dependency."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Context, Decimal, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from typing import TYPE_CHECKING, Iterator, Sequence

if TYPE_CHECKING:
    from .models import FinancialPeriod, FinancialSummary

ZERO = Decimal("0")
ONE = Decimal("1")
TWELVE = Decimal("12")


@contextmanager
def calculation_context(precision: int = 34) -> Iterator[Context]:
    """Use an independent context, including rounding, traps and exponent limits."""
    if isinstance(precision, bool) or not isinstance(precision, int) or precision < 34:
        raise ValueError("Decimal precision must be an integer of at least 34")
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_UP)) as context:
        yield context


def _decimal(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")


def _loan_inputs(amount: Decimal, months: int, annual_rate: Decimal) -> None:
    _decimal(amount, "amount")
    _decimal(annual_rate, "annual_rate")
    if amount < ZERO or annual_rate < ZERO:
        raise ValueError("amount and annual_rate must be nonnegative")
    if isinstance(months, bool) or not isinstance(months, int) or months <= 0:
        raise ValueError("months must be a positive integer")


def round_money(value: Decimal, *, precision: int = 34) -> Decimal:
    _decimal(value, "value")
    with calculation_context(precision):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def floor_money(value: Decimal, *, precision: int = 34) -> Decimal:
    _decimal(value, "value")
    with calculation_context(precision):
        return value.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)


def floor_step(value: Decimal, step: Decimal, *, precision: int = 34) -> Decimal:
    _decimal(value, "value")
    _decimal(step, "step")
    if step <= ZERO:
        raise ValueError("step must be positive")
    with calculation_context(precision):
        return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def display_dscr(value: Decimal, places: int = 6, *, precision: int = 34) -> Decimal:
    _decimal(value, "value")
    if isinstance(places, bool) or not isinstance(places, int) or places < 0:
        raise ValueError("places must be a nonnegative integer")
    with calculation_context(precision):
        return value.quantize(ONE.scaleb(-places), rounding=ROUND_HALF_UP)


def monthly_cashflow(period: FinancialPeriod, *, precision: int = 34) -> Decimal | None:
    """Asset income is already part of receipts and is never added separately."""
    fields = (
        period.operating_receipts,
        period.operating_payments,
        period.tax_payments,
        period.maintenance_capex,
    )
    if any(value is None for value in fields):
        return None
    with calculation_context(precision):
        return fields[0] - fields[1] - fields[2] - fields[3]  # type: ignore[operator]


def financial_summary(
    periods: Sequence[FinancialPeriod],
    debt: Decimal | None,
    *,
    precision: int = 34,
) -> FinancialSummary:
    """Summarize a validated 12-month window without inventing missing months."""
    from .models import FinancialSummary

    ordered = sorted(periods, key=lambda item: item.month)
    values = tuple(monthly_cashflow(period, precision=precision) for period in ordered)
    complete = len(values) == 12 and all(value is not None for value in values)
    c12 = c3 = c = None
    positive = None
    if complete:
        with calculation_context(precision):
            c12 = sum(values, ZERO) / TWELVE
            c3 = sum(values[-3:], ZERO) / Decimal("3")
            c = min(c12, c3)
            positive = sum(value > ZERO for value in values)
    return FinancialSummary(
        C_12=c12,
        C_3=c3,
        C=c,
        D=debt,
        positive_months=positive,
        period_start=ordered[0].month if ordered else None,
        period_end=ordered[-1].month if ordered else None,
        complete=complete and debt is not None,
    )


def annuity(
    amount: Decimal, months: int, annual_rate: Decimal, *, precision: int = 34
) -> Decimal:
    """Exact calculation value; round only when constructing display fields."""
    _loan_inputs(amount, months, annual_rate)
    with calculation_context(precision):
        monthly_rate = annual_rate / TWELVE
        if monthly_rate == ZERO:
            return amount / Decimal(months)
        return amount * monthly_rate / (ONE - (ONE + monthly_rate) ** (-months))


def dscr(
    cashflow: Decimal, debt: Decimal, payment: Decimal, *, precision: int = 34
) -> Decimal:
    for name, value in (("cashflow", cashflow), ("debt", debt), ("payment", payment)):
        _decimal(value, name)
    if debt < ZERO or payment <= ZERO:
        raise ValueError("debt must be nonnegative and payment must be positive")
    with calculation_context(precision):
        return cashflow / (debt + payment)


def meets_dscr(
    cashflow: Decimal,
    debt: Decimal,
    payment: Decimal,
    threshold: Decimal,
    *,
    precision: int = 34,
) -> bool:
    """Compare the unrounded inequality, never the displayed DSCR."""
    for name, value in (
        ("cashflow", cashflow), ("debt", debt), ("payment", payment), ("threshold", threshold)
    ):
        _decimal(value, name)
    if debt < ZERO or payment <= ZERO or threshold <= ZERO:
        raise ValueError("invalid debt, payment or threshold")
    with calculation_context(precision):
        return cashflow >= threshold * (debt + payment)


def cash_limit(
    cashflow: Decimal,
    debt: Decimal,
    months: int,
    annual_rate: Decimal,
    threshold: Decimal,
    *,
    precision: int = 34,
) -> Decimal:
    _loan_inputs(ZERO, months, annual_rate)
    for name, value in (("cashflow", cashflow), ("debt", debt), ("threshold", threshold)):
        _decimal(value, name)
    if debt < ZERO or threshold <= ZERO:
        raise ValueError("debt must be nonnegative and threshold must be positive")
    with calculation_context(precision):
        capacity = max(ZERO, cashflow / threshold - debt)
        monthly_rate = annual_rate / TWELVE
        if monthly_rate == ZERO:
            return capacity * Decimal(months)
        return capacity * (ONE - (ONE + monthly_rate) ** (-months)) / monthly_rate


def display_schedule(
    amount: Decimal,
    months: int,
    annual_rate: Decimal,
    financing_need: Decimal,
    *,
    precision: int = 34,
) -> dict[str, Decimal]:
    """The agreed demo schedule, not a calendar amortization or APR schedule."""
    _decimal(financing_need, "financing_need")
    with calculation_context(precision):
        payment = annuity(amount, months, annual_rate, precision=precision)
        monthly = round_money(payment, precision=precision)
        total = round_money(payment * Decimal(months), precision=precision)
        return {
            "monthly_payment": monthly,
            "last_payment": total - monthly * Decimal(months - 1),
            "total_payment": total,
            "interest": total - amount,
            "funding_gap": max(ZERO, financing_need - amount),
        }
