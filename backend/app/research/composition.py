from __future__ import annotations

from app.schemas.research import CompositionStatus


def consensus_status(
    *,
    matched_count: int,
    insufficient_count: int,
    required_count: int,
) -> CompositionStatus:
    if matched_count >= required_count:
        return "MATCHED"
    if matched_count + insufficient_count >= required_count:
        return "INSUFFICIENT_FEATURE_HISTORY"
    return "NOT_MATCHED"


__all__ = ["consensus_status"]
