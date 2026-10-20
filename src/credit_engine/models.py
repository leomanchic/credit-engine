"""Immutable DTOs and explicit decimal/date contracts for schema version 2.0."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Context, Decimal, ROUND_HALF_UP, localcontext
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer,
    ValidationError, WithJsonSchema, field_validator, model_validator,
)
from pydantic_core import PydanticCustomError

from .errors import InputValidationError, from_pydantic


def _money(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, str) and re.fullmatch(r"-?(?:0|[1-9][0-9]*)\.[0-9]{2}", value):
        amount = Decimal(value)
    else:
        raise PydanticCustomError("money_format", "Деньги передаются строкой с двумя десятичными знаками")
    if not amount.is_finite():
        raise PydanticCustomError("money_nonfinite", "NaN и бесконечность запрещены")
    if amount.as_tuple().exponent < -2 or amount.copy_abs() > Decimal("9999999999999999.99"):
        raise PydanticCustomError("money_precision", "Денежное значение должно соответствовать NUMERIC(18,2)")
    return Decimal("0.00") if amount == 0 else amount


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        result = Decimal(value)
    else:
        raise PydanticCustomError("decimal_format", "Коэффициент передаётся десятичной строкой")
    if not result.is_finite():
        raise PydanticCustomError("decimal_nonfinite", "NaN и бесконечность запрещены")
    # Strip only redundant zeroes without Decimal.normalize(), whose rounding
    # and traps depend on the caller's ambient decimal context.
    return Decimal(decimal_text(result))


def _output_decimal(value: Any) -> Decimal:
    if not isinstance(value, (Decimal, str)):
        raise PydanticCustomError("decimal_type", "Требуется Decimal или десятичная строка")
    return _decimal(value)


def money_text(value: Decimal) -> str:
    if value == 0:
        return "0.00"
    with localcontext(Context(prec=max(50, len(value.as_tuple().digits) + abs(value.as_tuple().exponent) + 3), rounding=ROUND_HALF_UP)):
        return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _iso_date(value: Any) -> date:
    if isinstance(value, datetime):
        raise PydanticCustomError("date_format", "Требуется дата YYYY-MM-DD без времени")
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise PydanticCustomError("date_format", "Требуется корректная дата YYYY-MM-DD")


Money = Annotated[Decimal, BeforeValidator(_money), PlainSerializer(money_text, return_type=str), WithJsonSchema({"type": "string", "pattern": r"^-?(0|[1-9][0-9]{0,15})\.[0-9]{2}$"})]
NonnegativeMoney = Annotated[Money, Field(ge=Decimal("0")), WithJsonSchema({"type": "string", "pattern": r"^(?:-0\.00|(0|[1-9][0-9]{0,15})\.[0-9]{2})$"})]
PositiveMoney = Annotated[Money, Field(gt=Decimal("0")), WithJsonSchema({"type": "string", "pattern": r"^(?!0\.00$)(0|[1-9][0-9]{0,15})\.[0-9]{2}$"})]
DecimalValue = Annotated[Decimal, BeforeValidator(_decimal), PlainSerializer(decimal_text, return_type=str), WithJsonSchema({"type": "string", "pattern": r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$"})]
OutputMoney = Annotated[Decimal, BeforeValidator(_output_decimal), PlainSerializer(money_text, return_type=str), WithJsonSchema({"type": "string", "pattern": r"^-?(0|[1-9][0-9]*)\.[0-9]{2}$"})]
OutputDecimal = Annotated[Decimal, BeforeValidator(_output_decimal), PlainSerializer(decimal_text, return_type=str)]
ISODate = Annotated[date, BeforeValidator(_iso_date)]
Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=128)]
Month = Annotated[str, Field(strict=True, pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
StrictInt = Annotated[int, Field(strict=True)]
StrictBool = Annotated[bool, Field(strict=True)]
ProductId = Literal["WORKING_CAPITAL", "EQUIPMENT"]
Purpose = Literal["WORKING_CAPITAL", "EQUIPMENT_PURCHASE"]


class CheckState(str, Enum):
    COMPLETE = "COMPLETE"
    MISSING = "MISSING"
    PENDING = "PENDING"
    ERROR = "ERROR"
    UNRESOLVED = "UNRESOLVED"


class SourceType(str, Enum):
    MOCK = "MOCK"
    BANK_OPERATOR = "BANK_OPERATOR"
    NORMALIZER = "NORMALIZER"


class AssetType(str, Enum):
    VEHICLE = "VEHICLE"
    EQUIPMENT = "EQUIPMENT"
    REAL_ESTATE = "REAL_ESTATE"
    IP_RIGHT = "IP_RIGHT"
    OTHER = "OTHER"


class ValuationMethod(str, Enum):
    MARKET_VALUE = "MARKET_VALUE"
    MANUAL_RECOVERY = "MANUAL_RECOVERY"


class Recommendation(str, Enum):
    OFFERED = "OFFERED"
    NEEDS_INFO = "NEEDS_INFO"
    WAITING_CHECKS = "WAITING_CHECKS"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    DECLINED = "DECLINED"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, allow_inf_nan=False)


class InputModel(FrozenModel):
    """Root input objects translate Pydantic diagnostics into the public error."""

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise from_pydantic(exc) from exc

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any):
        try:
            return super().model_validate(obj, **kwargs)
        except ValidationError as exc:
            raise from_pydantic(exc) from exc

    @classmethod
    def model_validate_json(cls, json_data: str | bytes | bytearray, **kwargs: Any):
        try:
            return super().model_validate_json(json_data, **kwargs)
        except ValidationError as exc:
            raise from_pydantic(exc) from exc


def validation_issue(path: tuple[str | int, ...], code: str, message: str, value: Any = None) -> dict[str, Any]:
    return {"type": PydanticCustomError(code, message), "loc": path, "input": value}


def _raise_issues(name: str, issues: list[dict[str, Any]]) -> None:
    if issues:
        raise ValidationError.from_exception_data(name, issues)


class FinancialPeriod(FrozenModel):
    month: Month
    operating_receipts: NonnegativeMoney | None
    operating_payments: NonnegativeMoney | None
    tax_payments: NonnegativeMoney | None
    maintenance_capex: NonnegativeMoney | None
    source_refs: tuple[Identifier, ...]


class DeclaredAsset(FrozenModel):
    asset_id: Identifier
    object_key: Identifier
    asset_type: AssetType
    owner_id: Identifier
    declared_value: NonnegativeMoney | None
    source_refs: tuple[Identifier, ...]


class ApplicationSnapshot(InputModel):
    schema_version: Literal["2.0"]
    application_id: Identifier
    company_id: Identifier
    revision: Annotated[StrictInt, Field(ge=1)]
    as_of_date: ISODate
    product_id: ProductId
    purpose: Purpose
    financing_need: PositiveMoney
    requested_amount: PositiveMoney
    requested_term_months: Annotated[StrictInt, Field(gt=0)]
    industry_code: Identifier | None = None
    existing_monthly_debt: NonnegativeMoney | None
    financial_periods: Annotated[tuple[FinancialPeriod, ...], Field(max_length=12)]
    assets: tuple[DeclaredAsset, ...]

    @model_validator(mode="after")
    def no_duplicates(self):
        issues = []
        for field, key in (("financial_periods", "month"), ("assets", "asset_id"), ("assets", "object_key")):
            seen = set()
            for index, row in enumerate(getattr(self, field)):
                value = getattr(row, key)
                if value in seen:
                    issues.append(validation_issue((field, index, key), "duplicate_value", "Повторное значение", value))
                seen.add(value)
        _raise_issues(type(self).__name__, issues)
        return self


class CompanyPayload(FrozenModel):
    is_active: StrictBool
    registration_date: ISODate


class CreditHistoryPayload(FrozenModel):
    has_history: StrictBool
    max_closed_overdue_days: Annotated[StrictInt, Field(ge=0)]
    active_overdue_days: Annotated[StrictInt, Field(ge=0)]
    window_start: ISODate
    window_end: ISODate

    @model_validator(mode="after")
    def consistent(self):
        issues = []
        if not self.has_history:
            for field in ("max_closed_overdue_days", "active_overdue_days"):
                if getattr(self, field) != 0:
                    issues.append(validation_issue((field,), "history_contradiction", "При отсутствии истории просрочка должна быть нулевой"))
        if self.window_start > self.window_end:
            issues.append(validation_issue(("window_end",), "invalid_date_range", "Конец периода раньше начала"))
        _raise_issues(type(self).__name__, issues)
        return self


class FinancialsPayload(FrozenModel):
    consolidated_accounts_confirmed: StrictBool
    expense_classification_confirmed: StrictBool


class DebtPayload(FrozenModel):
    obligations_complete: StrictBool
    service_pattern: Literal["FLAT_OR_CONSERVATIVE_MAX", "IRREGULAR"]


class BusinessProfilePayload(FrozenModel):
    cashflow_pattern: Literal["REGULAR", "SEASONAL"]


class AssetPayload(FrozenModel):
    ownership_confirmed: StrictBool | None = None
    pledge_eligible: StrictBool | None = None
    exclusive_allocation: StrictBool | None = None
    valuation_method: ValuationMethod | None = None
    valuation_date: ISODate | None = None
    assessed_value: NonnegativeMoney | None = None
    liquidation_costs: NonnegativeMoney | None = None
    senior_claims: NonnegativeMoney | None = None
    manual_recovery_value: NonnegativeMoney | None = None
    manual_decision_ref: Identifier | None = None
    approval_date: ISODate | None = None


class CheckEnvelope(FrozenModel):
    state: CheckState
    checked_on: ISODate | None = None
    valid_until: ISODate | None = None
    source_type: SourceType
    evidence_refs: tuple[Identifier, ...]
    fixture_version: Identifier | None = None

    @model_validator(mode="after")
    def state_contract(self):
        issues = []
        if self.state in (CheckState.COMPLETE, CheckState.UNRESOLVED):
            for field in ("checked_on", "valid_until"):
                if getattr(self, field) is None:
                    issues.append(validation_issue((field,), "check_date_required", "Для этого состояния обязательна дата проверки"))
        if self.state == CheckState.COMPLETE and self.payload is None:
            issues.append(validation_issue(("payload",), "complete_payload_required", "Завершённая проверка требует payload"))
        if self.checked_on and self.valid_until and self.valid_until < self.checked_on:
            issues.append(validation_issue(("valid_until",), "invalid_date_range", "Срок действия раньше даты проверки"))
        if self.source_type == SourceType.MOCK and not self.fixture_version:
            issues.append(validation_issue(("fixture_version",), "fixture_version_required", "Симулятор должен указывать версию данных"))
        _raise_issues(type(self).__name__, issues)
        return self


class CompanyCheck(CheckEnvelope):
    payload: CompanyPayload | None = None


class CreditHistoryCheck(CheckEnvelope):
    payload: CreditHistoryPayload | None = None


class FinancialsCheck(CheckEnvelope):
    payload: FinancialsPayload | None = None


class DebtCheck(CheckEnvelope):
    payload: DebtPayload | None = None


class BusinessProfileCheck(CheckEnvelope):
    payload: BusinessProfilePayload | None = None


class AssetCheck(CheckEnvelope):
    asset_id: Identifier
    payload: AssetPayload | None = None

    @model_validator(mode="after")
    def accepted_valuation(self):
        payload = self.payload
        if self.state != CheckState.COMPLETE or payload is None:
            return self
        issues = []
        accepted = payload.ownership_confirmed is True and payload.pledge_eligible is True and payload.exclusive_allocation is True
        if accepted:
            required = ["valuation_method", "valuation_date"]
            if payload.valuation_method == ValuationMethod.MARKET_VALUE:
                required += ["assessed_value", "liquidation_costs", "senior_claims"]
            elif payload.valuation_method == ValuationMethod.MANUAL_RECOVERY:
                required += ["manual_recovery_value", "manual_decision_ref", "approval_date"]
                if self.source_type != SourceType.BANK_OPERATOR:
                    issues.append(validation_issue(("source_type",), "operator_required", "Ручную оценку подтверждает банковский сотрудник"))
            for field in required:
                if getattr(payload, field) is None:
                    issues.append(validation_issue(("payload", field), "valuation_required", "Для принятого актива необходимо значение оценки"))
        if payload.approval_date and self.checked_on and payload.approval_date > self.checked_on:
            issues.append(validation_issue(("payload", "approval_date"), "approval_after_check", "Одобрение не может быть позже проверки"))
        _raise_issues(type(self).__name__, issues)
        return self


class NormalizedChecks(InputModel):
    application_id: Identifier
    revision: Annotated[StrictInt, Field(ge=1)]
    company: CompanyCheck
    credit_history: CreditHistoryCheck
    financials: FinancialsCheck
    debt: DebtCheck
    business_profile: BusinessProfileCheck
    asset_checks: tuple[AssetCheck, ...]

    @model_validator(mode="after")
    def no_duplicates(self):
        seen = set()
        issues = []
        for index, check in enumerate(self.asset_checks):
            if check.asset_id in seen:
                issues.append(validation_issue(("asset_checks", index, "asset_id"), "duplicate_asset_check", "Повторная проверка актива"))
            seen.add(check.asset_id)
        _raise_issues(type(self).__name__, issues)
        return self


class FinancialSummary(FrozenModel):
    C_12: OutputMoney | None = None
    C_3: OutputMoney | None = None
    C: OutputMoney | None = None
    D: OutputMoney | None = None
    positive_months: int | None = None
    period_start: Month | None = None
    period_end: Month | None = None
    complete: bool = False


class ScoreComponents(FrozenModel):
    payment: int | None = None
    history: int | None = None
    stability: int | None = None
    tenure: int | None = None
    base_score: int | None = None


class Candidate(FrozenModel):
    candidate_key: Identifier
    product_id: ProductId
    amount: OutputMoney
    term_months: int
    annual_rate: OutputDecimal
    status: Literal["AUTO_ELIGIBLE", "REVIEW", "REJECTED", "NOT_EVALUATED"]
    monthly_payment: OutputMoney | None = None
    last_payment: OutputMoney | None = None
    total_payment: OutputMoney | None = None
    interest: OutputMoney | None = None
    funding_gap: OutputMoney | None = None
    dscr: OutputDecimal | None = None
    score_total: int | None = None
    score_components: ScoreComponents = Field(default_factory=ScoreComponents)
    cash_limit: OutputMoney | None = None
    collateral_limit: OutputMoney | None = None
    collateral_applicable: bool = False
    limiting_factors: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


class AssetResult(FrozenModel):
    asset_id: Identifier
    object_key: Identifier
    asset_type: AssetType
    status: Literal["ACCEPTED", "INELIGIBLE", "UNKNOWN", "REVIEW"]
    eligible: bool | None = None
    accepted_value: OutputMoney | None = None
    valuation_method: ValuationMethod | None = None
    haircut: OutputDecimal | None = None
    manual_decision_ref: Identifier | None = None
    source_type: SourceType | None = None
    source_refs: tuple[Identifier, ...] = ()
    reason_codes: tuple[str, ...] = ()
    required_route: Recommendation | None = None
    collateral_applicable: bool = False

    @property
    def contribution(self) -> Decimal | None:
        return self.accepted_value


class Reason(FrozenModel):
    code: Identifier
    audience: Literal["CLIENT", "BANK"]
    scope: Literal["APPLICATION", "CANDIDATE", "ASSET", "CHECK"]
    subject_id: Identifier | None = None
    message_ru: str
    params: dict[str, str | int | bool | None] = Field(default_factory=dict)
    source_refs: tuple[Identifier, ...] = ()
    next_action: Identifier | None = None


class NextAction(FrozenModel):
    code: Identifier
    audience: Literal["CLIENT", "BANK"]
    subject_id: Identifier | None = None
    fields: tuple[str, ...] = ()
    message_ru: str


class EvaluationResult(FrozenModel):
    schema_version: Literal["2.0"] = "2.0"
    calculation_version: Identifier = "2.0.0"
    application_id: Identifier
    revision: int
    as_of_date: ISODate
    policy_version: Identifier
    policy_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    input_fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    recommendation: Recommendation
    financial_summary: FinancialSummary
    requested_assessment: Candidate
    asset_results: tuple[AssetResult, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    offers: tuple[Candidate, ...] = ()
    reasons: tuple[Reason, ...] = ()
    requested_fields: tuple[str, ...] = ()
    next_actions: tuple[NextAction, ...] = ()


# Readable aliases for integrations which refer to these DTOs by subject.
Asset = DeclaredAsset
CheckStatus = CheckState
