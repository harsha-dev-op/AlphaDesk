from decimal import Decimal

from app.technical.calculators.common import FeatureValue, PricePoint, divide, mean


def liquidity_values(points: list[PricePoint], volumes: list[Decimal], index: int) -> dict[str, Decimal | None]:
    values: dict[str, Decimal | None] = {}
    for window in (20, 50):
        average = mean(volumes[index - window + 1 : index + 1]) if index >= window - 1 else None
        values[f"AVG_VOLUME_{window}"] = average
        values[f"VOLUME_RATIO_{window}"] = divide(volumes[index], average) if average is not None else None
    traded_average: Decimal | None = None
    if index >= 19:
        traded_values = [points[position].traded_value for position in range(index - 19, index + 1)]
        if all(value is not None for value in traded_values):
            traded_average = mean([Decimal(value) for value in traded_values if value is not None])
    values["AVG_TRADED_VALUE_20"] = traded_average
    return values


def calculate_liquidity(points: list[PricePoint], output: list[dict[str, FeatureValue]]) -> None:
    volumes = [Decimal(point.volume) for point in points]
    for index in range(len(points)):
        output[index].update(liquidity_values(points, volumes, index))
