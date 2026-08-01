from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    """Return RFC-8785-compatible bytes for the artifact JSON subset.

    The collector schema intentionally permits only strings, booleans, null,
    integral quantities, arrays and objects. Rejecting floats avoids ambiguous
    host-language number formatting while UTF-16 key ordering matches JCS.
    """
    return json.dumps(_normalize(value), ensure_ascii=False, sort_keys=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(value[key]) for key in sorted(value, key=lambda item: str(item).encode("utf-16-be"))}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        raise ValueError("floating point values are outside the canonical schema")
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError("unsupported canonical JSON value")


def canonical_json(value: Any) -> str:
    return canonical_bytes(value).decode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))
