from decimal import Decimal

from app.technical.calculators.common import FeatureValue, mean


def _rsi_value(gain: Decimal, loss: Decimal) -> Decimal:
    if loss == 0:
        return Decimal(50) if gain == 0 else Decimal(100)
    if gain == 0:
        return Decimal(0)
    strength = gain / loss
    return Decimal(100) - (Decimal(100) / (Decimal(1) + strength))


def rsi_series(closes: list[Decimal]) -> list[Decimal | None]:
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    rsi: list[Decimal | None] = [None] * len(closes)
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, Decimal(0)))
        losses.append(max(-change, Decimal(0)))

    if len(closes) >= 15:
        average_gain = mean(gains[:14])
        average_loss = mean(losses[:14])
        rsi[14] = _rsi_value(average_gain, average_loss)
        for index in range(15, len(closes)):
            average_gain = (average_gain * Decimal(13) + gains[index - 1]) / Decimal(14)
            average_loss = (average_loss * Decimal(13) + losses[index - 1]) / Decimal(14)
            rsi[index] = _rsi_value(average_gain, average_loss)
    return rsi


def calculate_rsi(closes: list[Decimal], output: list[dict[str, FeatureValue]]) -> None:
    rsi = rsi_series(closes)
    for index, value in enumerate(rsi):
        output[index]["RSI_14"] = value
