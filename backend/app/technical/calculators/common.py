from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class PricePoint:
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    traded_value: Decimal | None
    source: str


FeatureValue = Decimal | bool | None


def divide(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return numerator / denominator if denominator != 0 else None


def mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))
