"""Safe, typed validation failures distinct from credit recommendations."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError


class InputValidationError(ValueError):
    """An invalid input contract, with stable field-level diagnostics."""

    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        self.issues = errors
        super().__init__("; ".join(f"{e['path']}: {e['message_ru']}" for e in errors))

    def to_dict(self) -> dict[str, Any]:
        return {"error": type(self).__name__, "errors": self.errors}


class PolicyValidationError(InputValidationError):
    """A server policy configuration cannot be handled by this algorithm."""


def from_pydantic(exc: ValidationError, prefix: str = "") -> InputValidationError:
    messages = {
        "missing": "Обязательное поле отсутствует",
        "extra_forbidden": "Неизвестное поле",
        "literal_error": "Неподдерживаемое значение",
        "enum": "Неподдерживаемое значение",
        "bool_type": "Требуется логическое значение",
        "int_type": "Требуется целое число",
        "string_type": "Требуется строка",
        "date_type": "Требуется дата YYYY-MM-DD",
        "date_from_datetime_parsing": "Требуется дата YYYY-MM-DD",
        "greater_than": "Значение должно быть больше допустимой нижней границы",
        "greater_than_equal": "Значение меньше допустимой нижней границы",
        "less_than_equal": "Значение превышает допустимую верхнюю границу",
        "too_long": "Слишком много элементов",
        "string_too_long": "Строка слишком длинная",
        "string_too_short": "Строка не должна быть пустой",
        "string_pattern_mismatch": "Неверный формат строки",
    }
    issues = []
    for item in exc.errors(include_url=False, include_input=False):
        path = ".".join(str(p) for p in item["loc"])
        path = f"{prefix}.{path}" if prefix and path else prefix or path
        # Pydantic wraps exceptions raised by a custom model constructor into a
        # root value_error; preserve the already-safe, precise inner locations.
        nested = item.get("ctx", {}).get("error")
        if isinstance(nested, InputValidationError):
            for issue in nested.errors:
                child_path = f"{path}.{issue['path']}" if path and issue["path"] else path or issue["path"]
                issues.append({**issue, "path": child_path})
            continue
        code = item["type"]
        message = messages.get(code)
        if message is None:
            message = item["msg"] if any("а" <= c.lower() <= "я" for c in item["msg"]) else "Некорректное значение поля"
        issues.append({"path": path, "code": code.upper(), "message_ru": message})
    return InputValidationError(issues)
