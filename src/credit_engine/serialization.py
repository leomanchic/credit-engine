"""Canonical UTF-8 JSON; only explicitly unordered collections are sorted."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any

from pydantic import BaseModel


def canonical_decimal(value: Decimal) -> str:
    """Plain notation, no exponent or redundant fractional zeroes."""
    if not value.is_finite():
        raise ValueError("Non-finite decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def normalize(value: Any, field: str = "") -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: normalize(item, key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        items = [normalize(item) for item in value]
        keys = {"financial_periods": "month", "assets": "asset_id", "asset_checks": "asset_id",
                "products": "product_id", "market_haircuts": "asset_type"}
        if field in keys:
            items.sort(key=lambda item: item[keys[field]])
        elif field in ("source_refs", "evidence_refs", "terms_months", "manual_recovery_types"):
            items.sort()
        return items
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def fingerprint(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()
