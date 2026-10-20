"""Build synthetic acceptance inputs and independently specified expectations.

This script never imports credit_engine. Expected monetary values come from
specification sections 17/18 or the elementary zero-rate calculations stated in
each expected.json. It is safe to rerun after changing an input fixture.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MONTHS = [f"2025-{m:02}" for m in (10, 11, 12)] + [f"2026-{m:02}" for m in range(1, 10)]


def check(payload=None, state="COMPLETE", **changes):
    result = {
        "state": state,
        "checked_on": "2026-10-21",
        "valid_until": "2026-11-20",
        "source_type": "MOCK",
        "evidence_refs": ["synthetic-trusted-check"],
        "fixture_version": "demo-checks-1",
        "payload": payload,
    }
    result.update(changes)
    return result


def base():
    snapshot = {
        "schema_version": "2.0",
        "application_id": "synthetic-app-001",
        "company_id": "synthetic-company-001",
        "revision": 1,
        "as_of_date": "2026-10-21",
        "product_id": "WORKING_CAPITAL",
        "purpose": "WORKING_CAPITAL",
        "financing_need": "2000000.00",
        "requested_amount": "2000000.00",
        "requested_term_months": 24,
        "industry_code": "62.01",
        "existing_monthly_debt": "40000.00",
        "financial_periods": [
            {
                "month": month,
                "operating_receipts": "600000.00",
                "operating_payments": "350000.00",
                "tax_payments": "30000.00",
                "maintenance_capex": "20000.00",
                "source_refs": [f"synthetic-statement-{month}"],
            }
            for month in MONTHS
        ],
        "assets": [],
    }
    checks = {
        "application_id": snapshot["application_id"],
        "revision": snapshot["revision"],
        "company": check({"is_active": True, "registration_date": "2022-01-01"}),
        "credit_history": check({
            "has_history": True,
            "max_closed_overdue_days": 0,
            "active_overdue_days": 0,
            "window_start": "2025-10-21",
            "window_end": "2026-10-20",
        }),
        "financials": check({"consolidated_accounts_confirmed": True, "expense_classification_confirmed": True}),
        "debt": check({"obligations_complete": True, "service_pattern": "FLAT_OR_CONSERVATIVE_MAX"}),
        "business_profile": check({"cashflow_pattern": "REGULAR"}),
        "asset_checks": [],
    }
    return snapshot, checks


def equipment(snapshot):
    snapshot.update(product_id="EQUIPMENT", purpose="EQUIPMENT_PURCHASE")


def set_flow(snapshot, flow):
    from decimal import Decimal
    for period in snapshot["financial_periods"]:
        period["operating_receipts"] = f"{Decimal(flow) + Decimal('400000'):.2f}"


def asset(snapshot, checks, kind="VEHICLE", *, mode="MARKET_VALUE", state="COMPLETE", value="2000000.00", costs="100000.00", senior="300000.00"):
    item = {
        "asset_id": "synthetic-asset-001",
        "object_key": f"{kind}:SYNTHETIC:001",
        "asset_type": kind,
        "owner_id": snapshot["company_id"],
        "declared_value": "20000000.00" if kind == "IP_RIGHT" else value,
        "source_refs": ["synthetic-asset-document"],
    }
    snapshot["assets"].append(item)
    payload = None
    if state == "COMPLETE":
        payload = {
            "ownership_confirmed": True,
            "pledge_eligible": True,
            "exclusive_allocation": True,
            "valuation_method": mode,
            "valuation_date": "2026-10-01",
            "assessed_value": value,
            "liquidation_costs": costs,
            "senior_claims": senior,
        }
        if mode == "MANUAL_RECOVERY":
            payload.update(manual_recovery_value="2400000.00", manual_decision_ref="synthetic-operator-decision-001", approval_date="2026-10-20")
    envelope = check(payload, state, asset_id=item["asset_id"])
    if mode == "MANUAL_RECOVERY" and state == "COMPLETE":
        envelope["source_type"] = "BANK_OPERATOR"
        envelope.pop("fixture_version")
    checks["asset_checks"].append(envelope)


def write(name, snapshot, checks, expected, *, policy_variant=None):
    directory = ROOT / "examples" / name
    directory.mkdir(parents=True, exist_ok=True)
    expected = {"basis": "Synthetic data; independently specified from specification sections 17 and 18.", **expected}
    if policy_variant is None:
        relative = os.path.relpath(ROOT / "policies" / "demo-assets-2.json", directory).replace(os.sep, "/")
        reference = {"policy_version": "demo-assets-2", "path": relative}
    else:
        reference = {"policy_version": policy_variant["policy_version"], "path": "policy.json"}
        policy_path = ROOT / "policies" / "demo-assets-2.json"
        if policy_path.exists():
            config = json.loads(policy_path.read_text(encoding="utf-8"))
            config["policy_version"] = policy_variant["policy_version"]
            if policy_variant.get("zero_rate"):
                products = config["products"]
                for product in products.values() if isinstance(products, dict) else products:
                    product["annual_rate"] = "0"
            (directory / "policy.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for filename, data in (("snapshot.json", snapshot), ("checks.json", checks), ("policy_ref.json", reference), ("expected.json", expected)):
        (directory / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    s, c = base()
    write("F01", s, c, {
        "recommendation": "OFFERED",
        "financial_summary": {"C_12": "200000.00", "C_3": "200000.00", "C": "200000.00", "D": "40000.00", "positive_months": 12},
        "requested_assessment": {"score_total": 90},
        "first_offer": {"amount": "2000000.00", "term_months": 24, "monthly_payment": "105742.19", "last_payment": "105742.30", "total_payment": "2537812.67", "interest": "537812.67", "funding_gap": "0.00", "score_total": 90},
    })
    s, c = base()
    equipment(s)
    set_flow(s, "80000")
    asset(s, c, "REAL_ESTATE", value="10000000.00", costs="300000.00", senior="2000000.00")
    write("F02", s, c, {
        "recommendation": "OFFERED", "financial_summary": {"C": "80000.00"},
        "requested_assessment": {"status": "REJECTED"},
        "first_offer": {"amount": "540000.00", "term_months": 36, "funding_gap": "1460000.00"},
        "candidates_contains": [{"amount": "400000.00", "term_months": 24, "monthly_payment": "21148.44", "last_payment": "21148.41", "total_payment": "507562.53", "dscr": "1.308292", "status": "AUTO_ELIGIBLE"}],
        "asset_results_contains": [{"asset_id": "synthetic-asset-001", "accepted_value": "4200000.00"}],
        "reason_codes": ["CASHFLOW_LIMIT"],
    })
    s, c = base()
    asset(s, c, "IP_RIGHT", state="PENDING")
    write("F03", s, c, {"basis": "The 200000 monthly net flow already includes 120000 net IP income; no separate income is added from the asset card.", "recommendation": "OFFERED", "financial_summary": {"C": "200000.00"}, "first_offer": {"amount": "2000000.00", "term_months": 24, "score_total": 90}, "asset_results_contains": [{"asset_id": "synthetic-asset-001", "accepted_value": None}]})
    s, c = base()
    equipment(s)
    asset(s, c, "IP_RIGHT", state="UNRESOLVED")
    write("F04", s, c, {"recommendation": "MANUAL_REVIEW", "offers_count": 0, "asset_results_contains": [{"asset_id": "synthetic-asset-001", "accepted_value": None}], "reason_codes": ["ASSET_REVIEW_REQUIRED"]})
    s, c = base()
    equipment(s)
    s["revision"] = c["revision"] = 2
    asset(s, c, "IP_RIGHT", mode="MANUAL_RECOVERY", value="20000000.00", costs="300000.00", senior="2000000.00")
    write("F04b", s, c, {"recommendation": "OFFERED", "first_offer": {"amount": "2000000.00", "term_months": 24, "score_total": 90}, "asset_results_contains": [{"asset_id": "synthetic-asset-001", "accepted_value": "2400000.00", "valuation_method": "MANUAL_RECOVERY"}]})
    s, c = base()
    equipment(s)
    asset(s, c)
    write("F05", s, c, {"recommendation": "OFFERED", "first_offer": {"amount": "900000.00", "term_months": 12}, "candidates_contains": [{"amount": "900000.00", "term_months": 24, "monthly_payment": "47583.99", "last_payment": "47583.93", "total_payment": "1142015.70", "funding_gap": "1100000.00"}], "max_offer_amount": "900000.00", "asset_results_contains": [{"asset_id": "synthetic-asset-001", "accepted_value": "900000.00"}]})
    s, c = base()
    s["financial_periods"] = s["financial_periods"][4:]
    write("F06/missing_months", s, c, {"recommendation": "NEEDS_INFO", "reason_codes": ["FINANCIAL_PERIOD_MISSING"], "requested_fields_contain_text": MONTHS[:4], "offers_count": 0})
    s, c = base()
    s["financial_periods"][5]["tax_payments"] = None
    write("F06/missing_tax", s, c, {"recommendation": "NEEDS_INFO", "reason_codes": ["FINANCIAL_VALUE_MISSING"], "requested_fields_contain_text": ["tax_payments"], "offers_count": 0})
    s, c = base()
    c["company"]["payload"]["registration_date"] = "2026-01-15"
    s["financial_periods"] = [p for p in s["financial_periods"] if p["month"] >= "2026-02"]
    write("F07", s, c, {"recommendation": "MANUAL_REVIEW", "reason_codes": ["SHORT_OPERATING_HISTORY"], "requested_fields_empty": True, "offers_count": 0, "requested_assessment": {"score_total": None}})
    s, c = base()
    s["existing_monthly_debt"] = "0.00"
    write("F08/confirmed_zero_debt", s, c, {"recommendation": "OFFERED", "financial_summary": {"D": "0.00"}, "first_offer": {"amount": "2000000.00", "term_months": 24}})
    s, c = base()
    s["existing_monthly_debt"] = None
    c["debt"] = check(state="MISSING")
    write("F08/unknown_debt", s, c, {"recommendation": "NEEDS_INFO", "reason_codes": ["DEBT_UNKNOWN"], "offers_count": 0})
    s, c = base()
    c["credit_history"] = check(state="PENDING")
    write("F08/pending_history", s, c, {"recommendation": "WAITING_CHECKS", "reason_codes": ["HISTORY_UNAVAILABLE"], "offers_count": 0, "requested_assessment": {"score_total": None}})
    s, c = base()
    c["credit_history"]["payload"]["has_history"] = False
    write("F08/confirmed_no_history", s, c, {"recommendation": "OFFERED", "requested_assessment": {"score_total": 80}})
    s, c = base()
    c["company"]["payload"]["is_active"] = False
    s["financial_periods"] = []
    s["existing_monthly_debt"] = None
    c["debt"] = check(state="MISSING")
    write("F09", s, c, {"recommendation": "DECLINED", "reason_codes": ["COMPANY_INACTIVE"], "requested_fields_empty": True, "offers_count": 0})
    s, c = base()
    c["credit_history"]["payload"]["active_overdue_days"] = 1
    write("F10", s, c, {"recommendation": "MANUAL_REVIEW", "reason_codes": ["ACTIVE_OVERDUE"], "offers_count": 0})
    s, c = base()
    c["business_profile"]["payload"]["cashflow_pattern"] = "SEASONAL"
    write("F11/seasonal", s, c, {"recommendation": "MANUAL_REVIEW", "reason_codes": ["SEASONAL_CASHFLOW"], "offers_count": 0})
    s, c = base()
    c["debt"]["payload"]["service_pattern"] = "IRREGULAR"
    write("F11/irregular_debt", s, c, {"recommendation": "MANUAL_REVIEW", "reason_codes": ["IRREGULAR_DEBT_SERVICE"], "offers_count": 0})
    for name in ("working_capital", "equipment"):
        s, c = base()
        c["credit_history"]["payload"]["max_closed_overdue_days"] = 10
        expected = {"recommendation": "OFFERED", "requested_assessment": {"score_total": 75}, "candidates_contains": [{"amount": "1760000.00", "term_months": 24, "score_total": 85, "status": "AUTO_ELIGIBLE", "monthly_payment": "93053.13", "last_payment": "93053.16", "total_payment": "2233275.15", "dscr": "1.503159"}]}
        if name == "equipment":
            equipment(s)
            asset(s, c, "REAL_ESTATE", value="10000000.00", costs="300000.00", senior="2000000.00")
            expected["first_offer"] = {"amount": "2000000.00", "term_months": 36, "score_total": 85}
        else:
            expected["first_offer"] = {"amount": "1760000.00", "term_months": 24, "score_total": 85}
            expected["reason_codes"] = ["LOWER_AMOUNT_IMPROVES_SCORE"]
        write(f"F12/{name}", s, c, expected)
    for duplicate in ("month", "asset_id", "object_key", "revision"):
        s, c = base()
        if duplicate == "month":
            s["financial_periods"][1]["month"] = s["financial_periods"][0]["month"]
            path = "financial_periods"
        elif duplicate == "revision":
            c["revision"] = 2
            path = "revision"
        else:
            asset(s, c)
            duplicate_asset = deepcopy(s["assets"][0])
            duplicate_asset["object_key" if duplicate == "asset_id" else "asset_id"] += "-other"
            s["assets"].append(duplicate_asset)
            path = duplicate
        write(f"F13/{duplicate}", s, c, {"error": "InputValidationError", "error_path_contains": path})
    for threshold, flow in (("1.30", "182000"), ("1.50", "210000")):
        from decimal import Decimal
        for side, delta in (("below", "-0.01"), ("equal", "0"), ("above", "0.01")):
            s, c = base()
            s.update(requested_amount="1200000.00", requested_term_months=12)
            set_flow(s, Decimal(flow) + Decimal(delta))
            score = 75 if threshold == "1.30" and side == "below" else (100 if threshold == "1.50" and side != "below" else 90)
            write(f"F14/dscr_{threshold.replace('.', '_')}_{side}", s, c, {"basis": f"Independent zero-rate identity: P=1200000/12=100000; D+P=140000. C={Decimal(flow) + Decimal(delta)}. Compare exact products with threshold {threshold}.", "recommendation": "OFFERED", "requested_assessment": {"monthly_payment": "100000.00", "score_total": score, "status": "REJECTED" if threshold == "1.30" and side == "below" else "AUTO_ELIGIBLE"}}, policy_variant={"policy_version": "test-zero-rate-1", "zero_rate": True})
    s, c = base()
    s.update(requested_amount="1200000.00", requested_term_months=12)
    write("F14/zero_rate", s, c, {"basis": "Zero-rate identity P=1200000/12=100000; total and principal both 1200000; interest zero.", "recommendation": "OFFERED", "requested_assessment": {"monthly_payment": "100000.00", "last_payment": "100000.00", "total_payment": "1200000.00", "interest": "0.00"}}, policy_variant={"policy_version": "test-zero-rate-1", "zero_rate": True})
    for name, flow in (("zero_cashflow", "0"), ("negative_cashflow", "-1000")):
        s, c = base()
        set_flow(s, flow)
        write(f"F14/{name}", s, c, {"recommendation": "DECLINED", "financial_summary": {"C": f"{int(flow)}.00"}, "reason_codes": ["CASHFLOW_BELOW_PRODUCT_MIN"], "offers_count": 0})
    for name, costs in (("zero_recovery", "1300000.00"), ("negative_raw_recovery", "1400000.00")):
        s, c = base()
        equipment(s)
        asset(s, c, costs=costs, senior="0.00")
        write(f"F14/{name}", s, c, {"recommendation": "DECLINED", "reason_codes": ["COLLATERAL_INSUFFICIENT"], "offers_count": 0, "asset_results_contains": [{"asset_id": "synthetic-asset-001", "accepted_value": "0.00"}]})
    s, c = base()
    write("F15/base", s, c, {"recommendation": "OFFERED", "metamorphic": "repeatable_immutable"})
    s["financial_periods"].reverse()
    write("F15/permuted_periods", s, c, {"recommendation": "OFFERED", "same_canonical_result_as": "../base"})
    s, c = base()
    write("F15/new_policy_version", s, c, {"recommendation": "OFFERED", "different_policy_hash_from": "../base"}, policy_variant={"policy_version": "demo-assets-2-revision-test"})
    s, c = base()
    c["debt"]["payload"]["obligations_complete"] = False
    write("F16/incomplete_debt", s, c, {"recommendation": "NEEDS_INFO", "reason_codes": ["DEBT_UNKNOWN"], "offers_count": 0})
    s, c = base()
    c["business_profile"] = check(state="UNRESOLVED", checked_on="2026-09-01", valid_until="2026-10-20")
    write("F16/expired_unresolved", s, c, {"recommendation": "WAITING_CHECKS", "reason_codes": ["CHECK_EXPIRED"], "offers_count": 0})
    s, c = base()
    asset(s, c)
    c["asset_checks"].append(deepcopy(c["asset_checks"][0]))
    write("F16/duplicate_asset_check", s, c, {"error": "InputValidationError", "error_path_contains": "asset_checks"})
    s, c = base()
    equipment(s)
    asset(s, c, "IP_RIGHT", state="UNRESOLVED")
    c["company"]["payload"]["registration_date"] = "2025-04-01"
    c["credit_history"]["payload"]["max_closed_overdue_days"] = 31
    for index, period in enumerate(s["financial_periods"]):
        period["operating_receipts"] = "399000.00" if index < 6 else "801000.00"
    write("F17/score_below_minimum", s, c, {"basis": "Six positive months => stability 0; closed overdue 31 => history 0; 18 full months => tenure 5. Even maximum payment points 40 yield only 45.", "recommendation": "DECLINED", "reason_codes": ["SCORE_BELOW_MIN"], "requested_fields_empty": True, "offers_count": 0})
    s, c = base()
    equipment(s)
    asset(s, c, "IP_RIGHT", state="UNRESOLVED")
    set_flow(s, "20000")
    write("F17/cash_below_minimum", s, c, {"basis": "C/k-D=20000/1.30-40000<0. Payment capacity zero at all terms; extra collateral cannot alter it.", "recommendation": "DECLINED", "reason_codes": ["CASHFLOW_BELOW_PRODUCT_MIN"], "requested_fields_empty": True, "offers_count": 0})


if __name__ == "__main__":
    main()
