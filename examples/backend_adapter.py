"""Executable backend call using explicitly synthetic persisted fixture data.

Database transactions, authenticating checks, and publishing remain backend work.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from credit_engine import canonical_json, evaluate
from credit_engine.models import ApplicationSnapshot, EvaluationResult, NormalizedChecks
from credit_engine.policy import load_policy


def client_view(result: EvaluationResult) -> dict:
    """Explicit whitelist: never forward the entire internal evaluation to a client."""
    data = result.model_dump(mode="json")
    public_offer_fields = ("candidate_key", "product_id", "amount", "term_months", "annual_rate",
                           "monthly_payment", "last_payment", "total_payment", "interest", "funding_gap")
    return {
        "application_id": data["application_id"], "revision": data["revision"],
        "recommendation": data["recommendation"],
        "offers": [{key: offer[key] for key in public_offer_fields} for offer in data["offers"]],
        "reasons": [item for item in data["reasons"] if item["audience"] == "CLIENT"],
        "requested_fields": data["requested_fields"],
        "next_actions": [item for item in data["next_actions"] if item["audience"] == "CLIENT"],
    }


def evaluate_saved_fixture(case_directory: Path) -> EvaluationResult:
    snapshot = ApplicationSnapshot.model_validate_json((case_directory / "snapshot.json").read_text(encoding="utf-8"))
    checks = NormalizedChecks.model_validate_json((case_directory / "checks.json").read_text(encoding="utf-8"))
    reference = json.loads((case_directory / "policy_ref.json").read_text(encoding="utf-8"))
    policy = load_policy(case_directory / reference["path"])
    return evaluate(snapshot, checks, policy)


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic local backend adapter demonstration")
    parser.add_argument("--case", default="F01", help="Directory under examples, e.g. F01/F02/F04b")
    parser.add_argument("--internal", action="store_true", help="Print complete internal result")
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent / args.case
    result = evaluate_saved_fixture(directory)
    print(canonical_json(result if args.internal else client_view(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
