"""Verify the installed wheel from an isolated interpreter, not src imports."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import credit_engine
from credit_engine import canonical_json, evaluate
from credit_engine.models import ApplicationSnapshot, EvaluationResult, NormalizedChecks
from credit_engine.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".artifacts" / "installed-verification"


def main() -> None:
    imported = Path(credit_engine.__file__).resolve()
    assert imported.is_relative_to(Path(sys.prefix).resolve()), imported
    assert not imported.is_relative_to(ROOT / "src"), imported
    OUT.mkdir(parents=True, exist_ok=True)
    policy_path = ROOT / "policies/demo-assets-2.json"
    policy = load_policy(policy_path)
    executable = Path(sys.executable).parent / ("credit-engine.exe" if os.name == "nt" else "credit-engine")

    def cli(argv, expected_code):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        env.pop("PYTHONPATH", None)
        completed = subprocess.run([str(executable), *map(str, argv)], cwd=OUT, env=env,
                                   text=True, encoding="utf-8", capture_output=True)
        assert completed.returncode == expected_code, (argv, completed.returncode, completed.stderr)
        return completed

    for case, expected in (("F01", "OFFERED"), ("F02", "OFFERED"), ("F04", "MANUAL_REVIEW"),
                           ("F04b", "OFFERED"), ("F09", "DECLINED")):
        folder = ROOT / "examples" / case
        snapshot = ApplicationSnapshot.model_validate_json((folder / "snapshot.json").read_text(encoding="utf-8"))
        checks = NormalizedChecks.model_validate_json((folder / "checks.json").read_text(encoding="utf-8"))
        result = evaluate(snapshot, checks, policy)
        assert result.recommendation == expected
        canonical = canonical_json(result)
        assert canonical_json(EvaluationResult.model_validate_json(canonical)) == canonical
        argv = ["evaluate", "--snapshot", folder / "snapshot.json", "--checks", folder / "checks.json", "--policy", policy_path]
        completed = cli(argv, 0)
        assert not completed.stderr and completed.stdout.strip() == canonical
        out_path = OUT / f"{case}.json"
        completed = cli([*argv, "--out", out_path], 0)
        assert not completed.stdout and not completed.stderr
        assert out_path.read_text(encoding="utf-8").strip() == canonical
        print(f"{case}: public API, canonical round-trip, CLI stdout/file: {expected}")

    folder = ROOT / "examples/F01"
    base = ["evaluate", "--snapshot", folder / "snapshot.json", "--checks", folder / "checks.json", "--policy", policy_path]
    bad = OUT / "bad.json"
    bad.write_text("{invalid", encoding="utf-8")
    argv = list(base)
    argv[argv.index("--snapshot") + 1] = bad
    error = cli(argv, 2)
    assert not error.stdout and json.loads(error.stderr)["error"] == "InputValidationError"
    argv = list(base)
    argv[argv.index("--policy") + 1] = bad
    error = cli(argv, 3)
    assert not error.stdout and json.loads(error.stderr)["error"] == "PolicyValidationError"
    error = cli([*base, "--out", OUT], 1)
    assert not error.stdout and json.loads(error.stderr)["error"] == "TechnicalError"

    schemas = OUT / "schemas"
    cli(["export-schemas", "--out", schemas], 0)
    for name in ("snapshot", "checks", "policy", "result"):
        generated = (schemas / f"{name}.schema.json").read_bytes()
        assert generated == (ROOT / "schemas" / f"{name}.schema.json").read_bytes()
    print("CLI exit codes 0/1/2/3 and four installed schema exports: PASS")
    print(f"Installed module: {imported}")


if __name__ == "__main__":
    main()
