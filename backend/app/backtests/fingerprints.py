from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal


def canonical(value: object) -> object:
    if isinstance(value, Decimal):
        normalized = value.normalize()
        return "0" if normalized == 0 else format(normalized, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.isoformat()
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    return value


def fingerprint(value: object) -> str:
    encoded = json.dumps(canonical(value), separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def fingerprint_canonical(value: object) -> str:
    """Fingerprint data whose leaves are already in canonical JSON form."""
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["canonical", "fingerprint", "fingerprint_canonical"]
