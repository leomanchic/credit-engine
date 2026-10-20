"""File and terminal adapters; the calculation itself remains pure."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .decision import evaluate
from .errors import InputValidationError, PolicyValidationError
from .models import ApplicationSnapshot, EvaluationResult, NormalizedChecks
from .policy import PolicyConfig
from .serialization import canonical_json


def _read_json(path: Path, role: str) -> Any:
    error_class = PolicyValidationError if role == "policy" else InputValidationError
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise error_class([{"path": role, "code": "FILE_READ_ERROR", "message_ru": "Не удалось прочитать JSON-файл в кодировке UTF-8"}]) from exc
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise error_class([{"path": role, "code": "INVALID_JSON", "message_ru": "Некорректный синтаксис JSON"}]) from exc


def export_schemas(directory: Path) -> None:
    """Export both the input and output contracts from the actual models."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, model in (("snapshot", ApplicationSnapshot), ("checks", NormalizedChecks), ("policy", PolicyConfig), ("result", EvaluationResult)):
        schema = model.model_json_schema(mode="serialization" if name == "result" else "validation")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        (directory / f"{name}.schema.json").write_text(json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="credit-engine", description="Расчёт демонстрационной кредитной политики")
    commands = parser.add_subparsers(dest="command", required=True)
    calculation = commands.add_parser("evaluate", help="Рассчитать рекомендацию из JSON")
    calculation.add_argument("--snapshot", required=True, type=Path)
    calculation.add_argument("--checks", required=True, type=Path)
    calculation.add_argument("--policy", required=True, type=Path)
    calculation.add_argument("--out", type=Path, help="Файл результата; без параметра JSON выводится в stdout")
    schemas = commands.add_parser("export-schemas", help="Выгрузить четыре JSON Schema")
    schemas.add_argument("--out", required=True, type=Path)
    return parser


def _diagnostic(error: InputValidationError) -> None:
    print(json.dumps(error.to_dict(), ensure_ascii=False, sort_keys=True), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    # JSON pipes use UTF-8 even under isolated Python on a legacy Windows locale.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    try:
        if args.command == "export-schemas":
            export_schemas(args.out)
            return 0
        policy = PolicyConfig.model_validate(_read_json(args.policy, "policy"))
        snapshot = ApplicationSnapshot.model_validate(_read_json(args.snapshot, "snapshot"))
        checks = NormalizedChecks.model_validate(_read_json(args.checks, "checks"))
        output = canonical_json(evaluate(snapshot, checks, policy)) + "\n"
        if args.out is None:
            sys.stdout.write(output)
        else:
            args.out.write_text(output, encoding="utf-8")
        return 0
    except PolicyValidationError as exc:
        _diagnostic(exc)
        return 3
    except InputValidationError as exc:
        _diagnostic(exc)
        return 2
    except Exception:
        print(json.dumps({"error": "TechnicalError", "message_ru": "Непредвиденная техническая ошибка; результат не сформирован"}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
