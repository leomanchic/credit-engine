"""Stateless recommendation orchestration, ordered as specification sections 8/13."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from pydantic import BaseModel, ValidationError

from .collateral import evaluate_collateral
from .errors import InputValidationError, PolicyValidationError, from_pydantic
from .finance import annuity, calculation_context, cash_limit, financial_summary
from .models import (ApplicationSnapshot, Candidate, EvaluationResult, FinancialSummary,
                     NextAction, NormalizedChecks)
from .offers import CALCULATION_VERSION, candidate_key, search
from .policy import PolicyConfig
from .reasons import reason
from .serialization import canonical_json, fingerprint
from .scoring import candidate_score

GENERAL_CHECKS = ("company", "credit_history", "financials", "debt", "business_profile")
ROUTE_PRIORITY = {"NEEDS_INFO": 0, "WAITING_CHECKS": 1, "MANUAL_REVIEW": 2}


def required_months(as_of_date: date) -> tuple[str, ...]:
    current = as_of_date.year * 12 + as_of_date.month - 1
    return tuple(f"{index // 12:04d}-{index % 12 + 1:02d}" for index in range(current - 12, current))


def required_history_window(as_of_date: date) -> tuple[date, date]:
    """A01: twelve calendar months, represented with inclusive date endpoints.

    A leap-day anniversary clamps to the last day of February in the prior year.
    """
    year = as_of_date.year - 1
    start = date(year, as_of_date.month, min(as_of_date.day, monthrange(year, as_of_date.month)[1]))
    return start, as_of_date - timedelta(days=1)


def _fresh_complete(check, on):
    return check.state == "COMPLETE" and check.checked_on <= on <= check.valid_until


def _validate_model(model, value, prefix, *, policy=False):
    # Revalidate even model_construct/model_copy inputs and detach mutable mappings.
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    try:
        return model.model_validate(value)
    except PolicyValidationError:
        raise
    except InputValidationError as exc:
        issues = [{**e, "path": f"{prefix}.{e['path']}" if e["path"] else prefix} for e in exc.errors]
        raise (PolicyValidationError if policy else InputValidationError)(issues) from None
    except ValidationError as exc:
        error = from_pydantic(exc, prefix)
        raise (PolicyValidationError(error.errors) if policy else error) from None


def _cross_validate(snapshot, checks, policy):
    issues = []

    def invalid(path, code, message):
        issues.append({"path": path, "code": code, "message_ru": message})

    if snapshot.as_of_date.year < 2:
        raise InputValidationError([{"path": "snapshot.as_of_date", "code": "DATE_WINDOW_OUT_OF_RANGE",
                                     "message_ru": "Дата должна позволять представить предыдущие 12 месяцев в ISO YYYY-MM-DD"}])

    if snapshot.application_id != checks.application_id:
        invalid("checks.application_id", "APPLICATION_MISMATCH", "Проверки относятся к другой заявке")
    if snapshot.revision != checks.revision:
        invalid("checks.revision", "REVISION_MISMATCH", "Проверки относятся к другой ревизии")
    try:
        product = policy.product(snapshot.product_id)
    except (KeyError, ValueError):
        invalid("snapshot.product_id", "UNSUPPORTED_PRODUCT", "Продукт отсутствует в выбранной политике")
        product = None
    if product:
        if snapshot.purpose != product.purpose:
            invalid("snapshot.purpose", "PURPOSE_MISMATCH", "Назначение не соответствует продукту")
        if snapshot.requested_term_months not in product.terms_months:
            invalid("snapshot.requested_term_months", "UNSUPPORTED_TERM", "Срок не разрешён выбранным продуктом")
    months = required_months(snapshot.as_of_date)
    for index, period in enumerate(snapshot.financial_periods):
        if period.month not in months:
            invalid(f"snapshot.financial_periods.{index}.month", "PERIOD_OUTSIDE_WINDOW", "Месяц вне требуемого окна полных месяцев")
    entries = [(f"checks.{name}", getattr(checks, name)) for name in GENERAL_CHECKS]
    entries += [(f"checks.asset_checks.{i}", check) for i, check in enumerate(checks.asset_checks)]
    assets = {asset.asset_id: asset for asset in snapshot.assets}
    for path, check in entries:
        if check.checked_on is not None and check.checked_on > snapshot.as_of_date:
            invalid(f"{path}.checked_on", "FUTURE_CHECK", "Дата проверки позже даты расчёта")
        if path.startswith("checks.asset_checks"):
            asset = assets.get(check.asset_id)
            if asset is None:
                invalid(f"{path}.asset_id", "UNKNOWN_ASSET", "Проверка ссылается на отсутствующий актив")
            payload = check.payload
            if payload:
                if payload.valuation_date and payload.valuation_date > snapshot.as_of_date:
                    invalid(f"{path}.payload.valuation_date", "FUTURE_VALUATION", "Оценка не может быть из будущего")
                if payload.approval_date and payload.approval_date > snapshot.as_of_date:
                    invalid(f"{path}.payload.approval_date", "FUTURE_APPROVAL", "Одобрение не может быть из будущего")
                if asset and payload.valuation_method == "MANUAL_RECOVERY" and asset.asset_type not in policy.collateral.manual_recovery_types:
                    invalid(f"{path}.payload.valuation_method", "UNSUPPORTED_VALUATION_METHOD", "Ручная чистая оценка не разрешена для этого типа актива")
    company = checks.company.payload
    if company and company.registration_date > snapshot.as_of_date:
        invalid("checks.company.payload.registration_date", "FUTURE_REGISTRATION", "Дата регистрации позже даты расчёта")
    if issues:
        raise InputValidationError(issues)


def evaluate(snapshot: ApplicationSnapshot, checks: NormalizedChecks, policy: PolicyConfig) -> EvaluationResult:
    """Evaluate a saved revision. Also accepts mappings at the Python boundary.

    No IO, clock, randomness or mutation. Load JSON and trusted checks externally.
    """
    policy = _validate_model(PolicyConfig, policy, "policy", policy=True)
    snapshot = _validate_model(ApplicationSnapshot, snapshot, "snapshot")
    checks = _validate_model(NormalizedChecks, checks, "checks")
    _cross_validate(snapshot, checks, policy)
    with calculation_context(policy.rounding.precision):
        return _evaluate(snapshot, checks, policy)


def _evaluate(snapshot, checks, policy):
    product = policy.product(snapshot.product_id)
    input_hash = fingerprint({"snapshot": snapshot, "checks": checks})
    policy_hash = fingerprint(policy)
    window = required_months(snapshot.as_of_date)
    collateral = evaluate_collateral(snapshot, checks, policy)
    messages, fields, routes = [], set(), []
    summary = FinancialSummary(period_start=window[0], period_end=window[-1])
    original = Candidate(
        candidate_key=candidate_key(input_hash, policy_hash, snapshot.product_id,
                                    snapshot.requested_amount, snapshot.requested_term_months),
        product_id=snapshot.product_id, amount=snapshot.requested_amount,
        term_months=snapshot.requested_term_months, annual_rate=product.annual_rate,
        status="NOT_EVALUATED", collateral_applicable=product.collateral_required,
    )

    def issue(route, code, path=None, *, scope="APPLICATION", subject_id=None, refs=(), params=None, action=None):
        routes.append(route)
        if route == "NEEDS_INFO" and path:
            fields.add(path)
        action = action or {"NEEDS_INFO": "PROVIDE_INFORMATION", "WAITING_CHECKS": "REFRESH_CHECK", "MANUAL_REVIEW": "BANK_REVIEW"}[route]
        messages.append(reason(code, scope=scope, subject_id=subject_id,
                               audience="BANK" if route != "NEEDS_INFO" else "CLIENT",
                               params={**(params or {}), **({"path": path} if path else {})},
                               source_refs=refs, next_action=action))

    def finish(recommendation, *, requested=None, candidates=(), offers=()):
        request = requested or original.model_copy(update={"reason_codes": tuple(sorted({r.code for r in messages}))})
        unique = {canonical_json(message): message for message in messages}
        ordered = tuple(unique[key] for key in sorted(unique))
        actions = []
        for message in ordered:
            if message.next_action:
                path = message.params.get("path")
                actions.append(NextAction(code=message.next_action, audience=message.audience,
                                          subject_id=message.subject_id, fields=(path,) if path else (),
                                          message_ru=message.message_ru))
        unique_actions = {canonical_json(action): action for action in actions}
        return EvaluationResult(
            calculation_version=CALCULATION_VERSION,
            application_id=snapshot.application_id, revision=snapshot.revision, as_of_date=snapshot.as_of_date,
            policy_version=policy.policy_version, policy_hash=policy_hash, input_fingerprint=input_hash,
            recommendation=recommendation, financial_summary=summary,
            requested_assessment=request, asset_results=collateral.results,
            candidates=candidates, offers=offers, reasons=ordered, requested_fields=tuple(sorted(fields)),
            next_actions=tuple(unique_actions[key] for key in sorted(unique_actions)),
        )

    if _fresh_complete(checks.company, snapshot.as_of_date) and not checks.company.payload.is_active:
        messages.append(reason("COMPANY_INACTIVE"))
        return finish("DECLINED")

    for name in GENERAL_CHECKS:
        check = getattr(checks, name)
        path = f"checks.{name}"
        refs = check.evidence_refs
        if check.state == "MISSING":
            code = "HISTORY_UNAVAILABLE" if name == "credit_history" else "DEBT_UNKNOWN" if name == "debt" else "CHECK_MISSING"
            issue("NEEDS_INFO", code, path, scope="CHECK", subject_id=name, refs=refs)
        elif check.state in ("PENDING", "ERROR"):
            code = "CHECK_PROVIDER_UNAVAILABLE" if check.state == "ERROR" else "HISTORY_UNAVAILABLE" if name == "credit_history" else "CHECK_PENDING"
            issue("WAITING_CHECKS", code, path, scope="CHECK", subject_id=name, refs=refs)
        elif check.valid_until < snapshot.as_of_date:
            issue("WAITING_CHECKS", "CHECK_EXPIRED", path, scope="CHECK", subject_id=name, refs=refs)
        elif check.state == "UNRESOLVED":
            issue("MANUAL_REVIEW", "CHECK_REVIEW_REQUIRED", path, scope="CHECK", subject_id=name, refs=refs)

    if snapshot.existing_monthly_debt is None:
        issue("NEEDS_INFO", "DEBT_UNKNOWN", "snapshot.existing_monthly_debt")
    if _fresh_complete(checks.debt, snapshot.as_of_date):
        if not checks.debt.payload.obligations_complete:
            issue("NEEDS_INFO", "DEBT_UNKNOWN", "checks.debt.payload.obligations_complete", refs=checks.debt.evidence_refs)
        if checks.debt.payload.service_pattern == "IRREGULAR":
            issue("MANUAL_REVIEW", "IRREGULAR_DEBT_SERVICE", refs=checks.debt.evidence_refs)
    if _fresh_complete(checks.financials, snapshot.as_of_date):
        for name in ("consolidated_accounts_confirmed", "expense_classification_confirmed"):
            if not getattr(checks.financials.payload, name):
                issue("NEEDS_INFO", "FINANCIAL_CONFIRMATION_REQUIRED", f"checks.financials.payload.{name}", refs=checks.financials.evidence_refs)
    if _fresh_complete(checks.business_profile, snapshot.as_of_date) and checks.business_profile.payload.cashflow_pattern == "SEASONAL":
        issue("MANUAL_REVIEW", "SEASONAL_CASHFLOW", refs=checks.business_profile.evidence_refs)
    if _fresh_complete(checks.credit_history, snapshot.as_of_date):
        history = checks.credit_history.payload
        if history.active_overdue_days > 0:
            issue("MANUAL_REVIEW", "ACTIVE_OVERDUE", refs=checks.credit_history.evidence_refs)
        young = (_fresh_complete(checks.company, snapshot.as_of_date)
                 and checks.company.payload.registration_date > date.fromisoformat(window[0] + "-01"))
        required_start, required_end = required_history_window(snapshot.as_of_date)
        if not young and (history.window_start > required_start or history.window_end < required_end):
            issue("WAITING_CHECKS", "HISTORY_UNAVAILABLE", "checks.credit_history",
                  scope="CHECK", subject_id="credit_history", refs=checks.credit_history.evidence_refs,
                  params={"required_window_start": required_start.isoformat(),
                          "required_window_end": required_end.isoformat(),
                          "actual_window_start": history.window_start.isoformat(),
                          "actual_window_end": history.window_end.isoformat()}, action="REQUEST_FULL_HISTORY")

    periods = {period.month: period for period in snapshot.financial_periods}
    if _fresh_complete(checks.company, snapshot.as_of_date):
        registration = checks.company.payload.registration_date
        start = date.fromisoformat(window[0] + "-01")
        if registration > start:
            issue("MANUAL_REVIEW", "SHORT_OPERATING_HISTORY", params={"registration_date": registration.isoformat()})
        required = [month for month in window if date.fromisoformat(month + "-01") >= registration]
        for month in required:
            if month not in periods:
                issue("NEEDS_INFO", "FINANCIAL_PERIOD_MISSING", f"snapshot.financial_periods[{month}]")
    for period in sorted(snapshot.financial_periods, key=lambda p: p.month):
        for name in ("operating_receipts", "operating_payments", "tax_payments", "maintenance_capex"):
            if getattr(period, name) is None:
                issue("NEEDS_INFO", "FINANCIAL_VALUE_MISSING", f"snapshot.financial_periods[{period.month}].{name}", refs=period.source_refs)

    # Untrusted or incomplete totals remain unavailable, never a partial full score.
    if not routes:
        summary = financial_summary(snapshot.financial_periods, snapshot.existing_monthly_debt, precision=policy.rounding.precision)
    if routes:
        return finish(min(routes, key=ROUTE_PRIORITY.get))

    result = search(snapshot, checks, policy, summary, collateral.collateral_limit, input_hash, policy_hash)
    messages.extend(result.reasons)
    for asset in collateral.results:
        for code in asset.reason_codes:
            messages.append(reason(code, scope="ASSET", subject_id=asset.asset_id, source_refs=asset.source_refs, audience="BANK"))
    if result.offers:
        if product.collateral_required and collateral.unresolved:
            potential = search(snapshot, checks, policy, summary, collateral.collateral_limit,
                               input_hash, policy_hash, ignore_collateral=True)
            if potential.offers and max(c.amount for c in potential.offers) > max(c.amount for c in result.offers):
                messages.append(reason("ADDITIONAL_COLLATERAL_POSSIBLE", audience="BANK",
                                       next_action="OPTIONAL_COLLATERAL_ASSESSMENT"))
        return finish("OFFERED", requested=result.requested, candidates=result.candidates, offers=result.offers)
    reviews = [c for c in result.candidates if c.status == "REVIEW"]

    if product.collateral_required:
        potential = search(snapshot, checks, policy, summary, collateral.collateral_limit, input_hash, policy_hash, ignore_collateral=True)
    else:
        potential = result
    acceptable = [c for c in potential.candidates if c.status in ("AUTO_ELIGIBLE", "REVIEW")]
    if not acceptable:
        max_cash = max(cash_limit(summary.C, summary.D, term, product.annual_rate, product.min_dscr,
                                  precision=policy.rounding.precision) for term in product.terms_months)
        if max_cash < product.min_amount:
            code = "CASHFLOW_BELOW_PRODUCT_MIN"
        elif min(snapshot.requested_amount, snapshot.financing_need, product.max_amount) < product.min_amount:
            code = "FINANCING_NEED_LIMIT" if snapshot.financing_need < product.min_amount else "PRODUCT_LIMIT"
        else:
            # Distinguish an inadequate score even at the product minimum from
            # a nondefault amount grid which has no usable reduced amount.
            minimum_scores = [candidate_score(
                summary.C, summary.D,
                annuity(product.min_amount, term, product.annual_rate, precision=policy.rounding.precision),
                checks, summary.positive_months, snapshot.as_of_date, policy,
            ).total for term in product.terms_months]
            code = "SCORE_BELOW_MIN" if max(minimum_scores) < policy.scoring.review_score_min else "AMOUNT_STEP_LIMIT"
        messages.append(reason(code, audience="BANK" if code == "SCORE_BELOW_MIN" else "CLIENT"))
        return finish("DECLINED", requested=result.requested, candidates=result.candidates)

    if reviews:
        messages.append(reason("SCORE_REVIEW", audience="BANK", next_action="BANK_REVIEW"))
        return finish("MANUAL_REVIEW", requested=result.requested, candidates=result.candidates)
    if product.collateral_required:
        if not snapshot.assets:
            issue("NEEDS_INFO", "ADD_COLLATERAL", "snapshot.assets", action="ADD_COLLATERAL")
        for asset_issue in collateral.unresolved:
            issue(asset_issue.recommendation, asset_issue.code, asset_issue.path,
                  scope="ASSET", subject_id=asset_issue.asset_id, refs=asset_issue.source_refs)
        if routes:
            return finish(min(routes, key=ROUTE_PRIORITY.get), requested=result.requested, candidates=result.candidates)
        messages.append(reason("COLLATERAL_INSUFFICIENT"))
    return finish("DECLINED", requested=result.requested, candidates=result.candidates)
