from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import structlog

from app.models import CorporateAction, DailyPrice

PRICE_QUANTUM = Decimal("0.0001")
logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AdjustedPrice:
    trading_date: object
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    traded_value: Decimal | None
    source: str
    adjustment_factor: Decimal


class PriceAdjustmentService:
    """Derive split/bonus adjusted prices without modifying the raw tape."""

    @staticmethod
    def factor_for(action: CorporateAction) -> Decimal:
        numerator = Decimal(action.ratio_numerator or 0)
        denominator = Decimal(action.ratio_denominator or 0)
        if numerator <= 0 or denominator <= 0:
            raise ValueError(f"{action.action_type} requires a positive ratio")
        if action.action_type == "STOCK_SPLIT":
            return denominator / numerator
        if action.action_type == "BONUS":
            return denominator / (denominator + numerator)
        return Decimal("1")

    def adjust(self, prices: list[DailyPrice | object], actions: list[CorporateAction]) -> list[AdjustedPrice]:
        adjusted: list[AdjustedPrice] = []
        relevant = [action for action in actions if action.action_type in {"STOCK_SPLIT", "BONUS"}]
        for price in prices:
            factor = Decimal("1")
            for action in relevant:
                if price.trading_date < action.ex_date:
                    try:
                        factor *= self.factor_for(action)
                    except ValueError:
                        logger.error("adjustment_processing_error", action_id=str(action.id), action_type=action.action_type)
                        raise
            volume_factor = Decimal("1") / factor if factor else Decimal("1")
            adjusted.append(
                AdjustedPrice(
                    trading_date=price.trading_date,
                    open=(Decimal(price.open) * factor).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP),
                    high=(Decimal(price.high) * factor).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP),
                    low=(Decimal(price.low) * factor).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP),
                    close=(Decimal(price.close) * factor).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP),
                    volume=int((Decimal(price.volume) * volume_factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
                    traded_value=price.traded_value,
                    source=price.source,
                    adjustment_factor=factor,
                )
            )
        return adjusted
