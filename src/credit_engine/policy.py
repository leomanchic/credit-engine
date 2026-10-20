"""Versioned, validated policy; file loading belongs outside evaluate."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from .errors import InputValidationError, PolicyValidationError, from_pydantic
from .models import (
    AssetType, DecimalValue, FrozenModel, Identifier, PositiveMoney,
    ProductId, Purpose, StrictBool, StrictInt, _raise_issues, validation_issue,
)


Points = Annotated[StrictInt, Field(ge=0, le=100)]


class PaymentBand(FrozenModel):
    # None is the unbounded first interval; every later lower bound is inclusive.
    min_dscr: DecimalValue | None
    points: Points


class HistoryBand(FrozenModel):
    # Upper bounds are inclusive; None is the final unbounded interval.
    max_days: Annotated[StrictInt, Field(ge=0)] | None
    points: Points


class MonthBand(FrozenModel):
    min_months: Annotated[StrictInt, Field(ge=0)]
    points: Points


class ScoringPolicy(FrozenModel):
    payment_bands: Annotated[tuple[PaymentBand, ...], Field(min_length=2)]
    history_no_history_points: Points
    history_bands: Annotated[tuple[HistoryBand, ...], Field(min_length=1)]
    stability_bands: Annotated[tuple[MonthBand, ...], Field(min_length=1)]
    tenure_bands: Annotated[tuple[MonthBand, ...], Field(min_length=1)]
    review_score_min: Points
    minimum_history_months: Literal[12]

    @model_validator(mode="after")
    def coherent_bands(self):
        issues = []
        payments = [band.min_dscr for band in self.payment_bands]
        if payments[0] is not None or any(v is None for v in payments[1:]):
            issues.append(validation_issue(("payment_bands",), "payment_coverage", "Первая корзина должна начинаться с неограниченного нижнего интервала"))
        elif any(v <= 0 for v in payments[1:]) or any(a >= b for a, b in zip(payments[1:], payments[2:])):
            issues.append(validation_issue(("payment_bands",), "threshold_order", "Границы DSCR должны быть положительными и строго возрастать"))
        for index, value in enumerate(payments):
            if value is not None and (value > Decimal("100") or value.as_tuple().exponent < -12):
                issues.append(validation_issue(("payment_bands", index, "min_dscr"), "unsupported_decimal_range", "Поддерживается DSCR до 100 с точностью не более 12 десятичных знаков"))
        history = [band.max_days for band in self.history_bands]
        if history[-1] is not None or any(v is None for v in history[:-1]):
            issues.append(validation_issue(("history_bands",), "history_coverage", "Последняя корзина истории должна покрывать неограниченный диапазон"))
        elif any(a >= b for a, b in zip(history[:-1], history[1:-1])):
            issues.append(validation_issue(("history_bands",), "threshold_order", "Верхние границы истории должны строго возрастать"))
        for field, first in (("stability_bands", 0), ("tenure_bands", self.minimum_history_months)):
            bands = getattr(self, field)
            values = [band.min_months for band in bands]
            if values[0] != first or any(a >= b for a, b in zip(values, values[1:])):
                issues.append(validation_issue((field,), "month_coverage", "Границы месяцев должны строго возрастать и покрывать допустимый диапазон"))
            if field == "stability_bands" and values[-1] > self.minimum_history_months:
                issues.append(validation_issue((field,), "month_range", "Устойчивость оценивается только в двенадцати месяцах"))
        for field in ("payment_bands", "stability_bands", "tenure_bands"):
            points = [band.points for band in getattr(self, field)]
            if any(a > b for a, b in zip(points, points[1:])):
                issues.append(validation_issue((field,), "nonmonotone_points", "Баллы должны не убывать при улучшении показателя"))
        points = [band.points for band in self.history_bands]
        if any(a < b for a, b in zip(points, points[1:])):
            issues.append(validation_issue(("history_bands",), "nonmonotone_points", "Баллы не должны расти при увеличении просрочки"))
        maximum = max(b.points for b in self.payment_bands) + max(self.history_no_history_points, *(b.points for b in self.history_bands)) + max(b.points for b in self.stability_bands) + max(b.points for b in self.tenure_bands)
        if maximum > 100 or self.review_score_min > maximum:
            issues.append(validation_issue(("review_score_min",), "score_range", "Диапазон баллов должен укладываться в 0–100 и достигать порога рассмотрения"))
        _raise_issues(type(self).__name__, issues)
        return self


class ProductPolicy(FrozenModel):
    product_id: ProductId
    purpose: Purpose
    min_amount: PositiveMoney
    max_amount: PositiveMoney
    terms_months: Annotated[tuple[Annotated[StrictInt, Field(ge=1, le=120)], ...], Field(min_length=1)]
    annual_rate: Annotated[DecimalValue, Field(ge=Decimal("0"), le=Decimal("1"))]
    min_dscr: Annotated[DecimalValue, Field(gt=Decimal("0"), le=Decimal("100"))]
    auto_score_min: Points
    collateral_required: StrictBool
    coverage_ratio: Annotated[DecimalValue, Field(gt=Decimal("0"), le=Decimal("100"))]
    amount_step: PositiveMoney
    max_offers: Annotated[StrictInt, Field(ge=1, le=3)]

    @field_validator("annual_rate", "min_dscr", "coverage_ratio")
    @classmethod
    def supported_decimal(cls, value):
        if value.as_tuple().exponent < -12:
            from pydantic_core import PydanticCustomError
            raise PydanticCustomError("unsupported_decimal_scale", "Поддерживается не более 12 знаков после десятичной точки")
        return value

    @model_validator(mode="after")
    def coherent(self):
        issues = []
        expected_purpose = "WORKING_CAPITAL" if self.product_id == "WORKING_CAPITAL" else "EQUIPMENT_PURCHASE"
        if self.purpose != expected_purpose:
            issues.append(validation_issue(("purpose",), "product_purpose_mismatch", "Цель несовместима с продуктом"))
        if self.min_amount > self.max_amount:
            issues.append(validation_issue(("min_amount",), "amount_range", "Минимальная сумма превышает максимальную"))
        if len(set(self.terms_months)) != len(self.terms_months):
            issues.append(validation_issue(("terms_months",), "duplicate_term", "Сроки должны быть уникальными"))
        if self.amount_step > self.max_amount:
            issues.append(validation_issue(("amount_step",), "amount_step_range", "Шаг альтернативы превышает максимальную сумму"))
        if self.collateral_required != (self.product_id == "EQUIPMENT"):
            issues.append(validation_issue(("collateral_required",), "unsupported_product_collateral", "В этой версии алгоритма обеспечение требуется только для EQUIPMENT"))
        _raise_issues(type(self).__name__, issues)
        return self


class MarketHaircut(FrozenModel):
    asset_type: Literal["VEHICLE", "EQUIPMENT", "REAL_ESTATE"]
    haircut: Annotated[DecimalValue, Field(ge=Decimal("0"), le=Decimal("1"))]

    @field_validator("haircut")
    @classmethod
    def scale(cls, value):
        if value.as_tuple().exponent < -12:
            from pydantic_core import PydanticCustomError
            raise PydanticCustomError("unsupported_decimal_scale", "Поддерживается не более 12 знаков после десятичной точки")
        return value


class CollateralPolicy(FrozenModel):
    market_haircuts: tuple[MarketHaircut, ...]
    manual_recovery_types: tuple[Literal["IP_RIGHT"], ...]
    max_valuation_age_days: Annotated[StrictInt, Field(ge=0)]
    allow_junior_lien: StrictBool

    @model_validator(mode="after")
    def supported_assets(self):
        issues = []
        types = [item.asset_type for item in self.market_haircuts]
        if set(types) != {"VEHICLE", "EQUIPMENT", "REAL_ESTATE"} or len(types) != 3:
            issues.append(validation_issue(("market_haircuts",), "asset_coverage", "Нужен один дисконт для каждого из трёх материальных типов активов"))
        if len(set(self.manual_recovery_types)) != len(self.manual_recovery_types):
            issues.append(validation_issue(("manual_recovery_types",), "duplicate_asset_type", "Типы ручной оценки должны быть уникальными"))
        _raise_issues(type(self).__name__, issues)
        return self

    def haircut(self, asset_type: str | AssetType) -> Decimal | None:
        for item in self.market_haircuts:
            if item.asset_type == asset_type:
                return item.haircut
        return None


class RoundingPolicy(FrozenModel):
    precision: Annotated[StrictInt, Field(ge=34, le=200)]
    money_places: Literal[2]
    dscr_places: Annotated[StrictInt, Field(ge=1, le=12)]
    payment_mode: Literal["ROUND_HALF_UP"]
    limit_mode: Literal["ROUND_FLOOR"]


class PolicyConfig(FrozenModel):
    schema_version: Literal["2.0"]
    policy_version: Identifier
    calculation_version: Literal["2.0.0"]
    currency: Literal["RUB"]
    products: Annotated[tuple[ProductPolicy, ...], Field(min_length=1, max_length=2)]
    scoring: ScoringPolicy
    collateral: CollateralPolicy
    rounding: RoundingPolicy

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise PolicyValidationError(from_pydantic(exc).errors) from exc

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any):
        try:
            return super().model_validate(obj, **kwargs)
        except ValidationError as exc:
            raise PolicyValidationError(from_pydantic(exc).errors) from exc

    @classmethod
    def model_validate_json(cls, json_data: str | bytes | bytearray, **kwargs: Any):
        try:
            return super().model_validate_json(json_data, **kwargs)
        except ValidationError as exc:
            raise PolicyValidationError(from_pydantic(exc).errors) from exc

    @model_validator(mode="after")
    def coherent_policy(self):
        issues = []
        ids = [p.product_id for p in self.products]
        if len(set(ids)) != len(ids):
            issues.append(validation_issue(("products",), "duplicate_product", "Идентификаторы продуктов должны быть уникальными"))
        for index, product in enumerate(self.products):
            if product.auto_score_min <= self.scoring.review_score_min:
                issues.append(validation_issue(("products", index, "auto_score_min"), "score_threshold_order", "Автоматический порог должен быть выше порога рассмотрения"))
        _raise_issues(type(self).__name__, issues)
        return self

    def product(self, product_id: str) -> ProductPolicy:
        for product in self.products:
            if product.product_id == product_id:
                return product
        raise InputValidationError([{"path": "product_id", "code": "UNSUPPORTED_PRODUCT", "message_ru": "Продукт отсутствует в переданной политике"}])


def load_policy(path: str | Path) -> PolicyConfig:
    """Read policy outside the deterministic calculation core."""
    return PolicyConfig.model_validate_json(Path(path).read_text(encoding="utf-8"))
