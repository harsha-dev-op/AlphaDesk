from decimal import Decimal

from app.technical.calculators.common import FeatureValue, divide


def return_values(closes: list[Decimal], index: int) -> dict[str, Decimal | None]:
    values: dict[str, Decimal | None] = {}
    for lag in (1, 5, 10, 20):
        values[f"RET_{lag}D"] = (
            divide(closes[index], closes[index - lag]) - Decimal(1)
            if index >= lag and closes[index - lag] != 0
            else None
        )
    for code, lag in (("MOM_1M_21D", 21), ("MOM_3M_63D", 63), ("MOM_6M_126D", 126)):
        values[code] = (
            divide(closes[index], closes[index - lag]) - Decimal(1)
            if index >= lag and closes[index - lag] != 0
            else None
        )
    return values


def calculate_returns(closes: list[Decimal], output: list[dict[str, FeatureValue]]) -> None:
    for index in range(len(closes)):
        output[index].update(return_values(closes, index))
