"""The adapter separates business outcomes, input errors and server failures."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from credit_engine.cli import main


ROOT = Path(__file__).resolve().parents[1]


def args(case="F01"):
    folder = ROOT / "examples" / case
    return ["evaluate", "--snapshot", str(folder / "snapshot.json"), "--checks", str(folder / "checks.json"), "--policy", str(ROOT / "policies/demo-assets-2.json")]


def test_cli_stdout_is_only_canonical_business_json(capsys):
    assert main(args()) == 0
    captured = capsys.readouterr()
    assert not captured.err
    data = json.loads(captured.out)
    assert data["recommendation"] == "OFFERED"
    assert data["offers"][0]["monthly_payment"] == "105742.19"
    assert captured.out.count("\n") == 1


def test_cli_result_file_is_separate_from_diagnostics(tmp_path, capsys):
    target = tmp_path / "result.json"
    assert main(args() + ["--out", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["recommendation"] == "OFFERED"
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_declined_is_successful_business_result(capsys):
    assert main(args("F09")) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["recommendation"] == "DECLINED"
    assert not captured.err


@pytest.mark.parametrize("role,exit_code", [("snapshot", 2), ("checks", 2), ("policy", 3)])
@pytest.mark.parametrize("failure", ["missing", "json", "encoding"])
def test_cli_file_failures_have_role_specific_codes(tmp_path, capsys, role, exit_code, failure):
    target = tmp_path / "input.json"
    if failure == "json":
        target.write_text("{broken", encoding="utf-8")
    elif failure == "encoding":
        target.write_bytes(b"\xff\xfe")
    argv = args()
    argv[argv.index(f"--{role}") + 1] = str(target)
    assert main(argv) == exit_code
    captured = capsys.readouterr()
    assert captured.out == ""
    diagnostic = json.loads(captured.err)
    assert diagnostic["error"] == ("PolicyValidationError" if role == "policy" else "InputValidationError")
    assert diagnostic["errors"][0]["path"] == role


def test_cli_invalid_policy_is_not_credit_decline(tmp_path, capsys):
    target = tmp_path / "policy.json"
    data = json.loads((ROOT / "policies/demo-assets-2.json").read_text(encoding="utf-8"))
    data["products"][0]["amount_step"] = "0.00"
    target.write_text(json.dumps(data), encoding="utf-8")
    argv = args()
    argv[argv.index("--policy") + 1] = str(target)
    assert main(argv) == 3
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["error"] == "PolicyValidationError"


def test_cli_invalid_money_returns_safe_input_errors(tmp_path, capsys):
    target = tmp_path / "snapshot.json"
    data = json.loads((ROOT / "examples/F01/snapshot.json").read_text(encoding="utf-8"))
    data["requested_amount"] = "PRIVATE_INVALID_VALUE"
    target.write_text(json.dumps(data), encoding="utf-8")
    argv = args()
    argv[argv.index("--snapshot") + 1] = str(target)
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "PRIVATE_INVALID_VALUE" not in captured.err
    assert json.loads(captured.err)["errors"][0]["path"] == "requested_amount"


def test_unexpected_output_io_error_has_exit_one(tmp_path, capsys):
    assert main(args() + ["--out", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["error"] == "TechnicalError"


def test_export_schemas_uses_actual_contract(tmp_path, capsys):
    assert main(["export-schemas", "--out", str(tmp_path)]) == 0
    assert {path.name for path in tmp_path.iterdir()} == {f"{name}.schema.json" for name in ("snapshot", "checks", "policy", "result")}
    schema = json.loads((tmp_path / "snapshot.schema.json").read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["properties"]["financing_need"]["type"] == "string"
    assert schema["properties"]["schema_version"]["const"] == "2.0"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("argv", [[], ["evaluate"], ["unknown"], ["export-schemas", "--bad", "x"]])
def test_invalid_cli_syntax_has_exit_two(capsys, argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err


def test_python_module_entry_point():
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, "-I", "-m", "credit_engine", *args()], cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["recommendation"] == "OFFERED"
    assert not result.stderr
