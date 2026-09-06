from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class QualityStatus(StrEnum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    FAILED = "FAILED"


@dataclass(frozen=True)
class QualityCheck:
    name: str
    status: QualityStatus
    message: str
    issue_count: int = 0


def validate_ohlcv(*, open_: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: int) -> list[str]:
    issues: list[str] = []
    if any(value < 0 for value in (open_, high, low, close)):
        issues.append("prices must be non-negative")
    if high < low:
        issues.append("high is below low")
    if not low <= open_ <= high:
        issues.append("open is outside the low/high range")
    if not low <= close <= high:
        issues.append("close is outside the low/high range")
    if volume < 0:
        issues.append("volume must be non-negative")
    return issues


def missing_sessions(expected: list[date], observed: list[date]) -> list[date]:
    return sorted(set(expected) - set(observed))


def stale_sessions(expected: list[date], latest_observed: date | None) -> int:
    if not expected:
        return 0
    if latest_observed is None:
        return len(expected)
    return sum(day > latest_observed for day in expected)
