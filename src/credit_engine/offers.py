"""Finite, policy-driven candidate search and full re-assessment of every pair."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .finance import (annuity, calculation_context, cash_limit, display_dscr,
                      display_schedule, dscr, floor_money, floor_step, meets_dscr)
from .models import Candidate, Reason
from .reasons import reason
from .scoring import candidate_score
from .serialization import fingerprint

CALCULATION_VERSION = "2.0.0"


def candidate_key(input_fingerprint, policy_hash, product_id, amount, months):
    return fingerprint({"input_fingerprint": input_fingerprint, "policy_hash": policy_hash,
                        "calculation_version": CALCULATION_VERSION, "product_id": product_id,
                        "amount": format(amount, ".2f"), "term_months": months})


@dataclass(frozen=True)
class SearchResult:
    requested: Candidate
    candidates: tuple[Candidate, ...]
    offers: tuple[Candidate, ...]
    reasons: tuple[Reason, ...]


def assess(snapshot, checks, policy, summary, amount, months, collateral_limit,
           input_fingerprint, policy_hash, *, ignore_collateral=False):
    product = policy.product(snapshot.product_id)
    precision = policy.rounding.precision
    key = candidate_key(input_fingerprint, policy_hash, snapshot.product_id, amount, months)
    C, D = summary.C, summary.D
    with calculation_context(precision):
        payment = annuity(amount, months, product.annual_rate, precision=precision)
        ratio = dscr(C, D, payment, precision=precision)
        minimum_limit = cash_limit(C, D, months, product.annual_rate, product.min_dscr, precision=precision)
        score = candidate_score(C, D, payment, checks, summary.positive_months, snapshot.as_of_date, policy)
        hard = []
        if not product.min_amount <= amount <= product.max_amount:
            hard.append("PRODUCT_LIMIT")
        if amount > snapshot.financing_need:
            hard.append("FINANCING_NEED_LIMIT")
        if not meets_dscr(C, D, payment, product.min_dscr, precision=precision):
            hard.append("CASHFLOW_LIMIT")
        if not ignore_collateral and product.collateral_required and amount > collateral_limit:
            hard.append("COLLATERAL_LIMIT")
        status = "REJECTED"
        if not hard and score.total is not None:
            if score.total >= product.auto_score_min:
                status = "AUTO_ELIGIBLE"
            elif score.total >= policy.scoring.review_score_min:
                status = "REVIEW"

        codes = list(hard)
        limits = []
        reasons = []
        cap_values = [("CASHFLOW_LIMIT", minimum_limit), ("PRODUCT_LIMIT", product.max_amount),
                      ("FINANCING_NEED_LIMIT", snapshot.financing_need)]
        if not ignore_collateral and product.collateral_required:
            cap_values.append(("COLLATERAL_LIMIT", collateral_limit))
        for code, value in cap_values:
            if snapshot.requested_amount > value or code in hard:
                limits.append(code)
                if code not in codes:
                    codes.append(code)
                reasons.append(reason(code, scope="CANDIDATE", subject_id=key,
                                      params={"limit": format(floor_money(value, precision=precision), ".2f"),
                                              "requested": format(snapshot.requested_amount, ".2f"),
                                              **({"minimum": format(product.min_amount, ".2f")} if code == "PRODUCT_LIMIT" else {})}))
        if score.total is not None and score.total < policy.scoring.review_score_min:
            codes.append("SCORE_BELOW_MIN")
        elif status == "REVIEW":
            codes.append("SCORE_REVIEW")
        if amount < snapshot.financing_need:
            codes.append("FUNDING_GAP")
            reasons.append(reason("FUNDING_GAP", scope="CANDIDATE", subject_id=key,
                                  params={"funding_gap": format(snapshot.financing_need - amount, ".2f")}))

        original_payment = annuity(snapshot.requested_amount, snapshot.requested_term_months,
                                   product.annual_rate, precision=precision)
        if months > snapshot.requested_term_months and status in ("AUTO_ELIGIBLE", "REVIEW") and not meets_dscr(
            C, D, original_payment, product.min_dscr, precision=precision
        ):
            codes.append("LONGER_TERM_FITS_CAPACITY")

        same_term_score = candidate_score(
            C, D, annuity(snapshot.requested_amount, months, product.annual_rate, precision=precision),
            checks, summary.positive_months, snapshot.as_of_date, policy,
        )
        if amount < snapshot.requested_amount and score.total > same_term_score.total:
            codes.append("LOWER_AMOUNT_IMPROVES_SCORE")
            band = next(b for b in policy.scoring.payment_bands if b.points == score.components.payment)
            if band.min_dscr is not None:
                score_limit = cash_limit(C, D, months, product.annual_rate, band.min_dscr, precision=precision)
                reasons.append(reason("LOWER_AMOUNT_IMPROVES_SCORE", scope="CANDIDATE", subject_id=key,
                                      params={"dscr_threshold": format(band.min_dscr, "f"),
                                              "score_cash_limit": format(floor_money(score_limit, precision=precision), ".2f"),
                                              "score_before": same_term_score.total, "score_after": score.total}))
        codes = tuple(sorted(set(codes)))
        detailed = {r.code for r in reasons}
        for code in codes:
            if code not in detailed:
                reasons.append(reason(code, scope="CANDIDATE", subject_id=key,
                                      audience="BANK" if code.startswith("SCORE_") else "CLIENT"))
        candidate = Candidate(
            candidate_key=key, product_id=snapshot.product_id, amount=amount, term_months=months,
            annual_rate=product.annual_rate, status=status,
            **display_schedule(amount, months, product.annual_rate, snapshot.financing_need, precision=precision),
            dscr=display_dscr(ratio, policy.rounding.dscr_places, precision=precision),
            score_total=score.total, score_components=score.components,
            cash_limit=floor_money(minimum_limit, precision=precision),
            collateral_limit=floor_money(collateral_limit, precision=precision) if product.collateral_required else None,
            collateral_applicable=product.collateral_required,
            limiting_factors=tuple(sorted(set(limits))), reason_codes=codes,
        )
        return candidate, tuple(reasons)


def search(snapshot, checks, policy, summary, collateral_limit, input_fingerprint, policy_hash,
           *, ignore_collateral=False):
    product = policy.product(snapshot.product_id)
    precision = policy.rounding.precision
    pairs = set()
    boundaries = {product.min_dscr}
    boundaries.update(b.min_dscr for b in policy.scoring.payment_bands
                      if b.min_dscr is not None and b.min_dscr >= product.min_dscr)
    with calculation_context(precision):
        for months in product.terms_months:
            pairs.add((snapshot.requested_amount, months))
            caps = [snapshot.requested_amount, snapshot.financing_need, product.max_amount]
            if product.collateral_required and not ignore_collateral:
                caps.append(collateral_limit)
            bounds = [min(caps)]
            for k in sorted(boundaries):
                bounds.append(min(*caps, cash_limit(summary.C, summary.D, months, product.annual_rate, k, precision=precision)))
            for bound in bounds:
                amount = bound if bound == snapshot.requested_amount else floor_step(bound, product.amount_step, precision=precision)
                if amount >= product.min_amount:
                    pairs.add((amount, months))
        candidates, explanations = [], []
        for amount, months in sorted(pairs, key=lambda item: (item[1], item[0])):
            candidate, messages = assess(snapshot, checks, policy, summary, amount, months, collateral_limit,
                                         input_fingerprint, policy_hash, ignore_collateral=ignore_collateral)
            candidates.append(candidate)
            explanations.extend(messages)
        requested = next(c for c in candidates if c.amount == snapshot.requested_amount
                         and c.term_months == snapshot.requested_term_months)
        eligible = [c for c in candidates if c.status == "AUTO_ELIGIBLE"]
        eligible.sort(key=lambda c: (
            not (c.amount == snapshot.requested_amount and c.term_months == snapshot.requested_term_months),
            c.funding_gap, c.total_payment, c.term_months, c.amount, c.candidate_key,
        ))
        return SearchResult(requested, tuple(candidates), tuple(eligible[:product.max_offers]), tuple(explanations))
