from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, localcontext

from app.backtests.definitions import CostModelDefinition
from app.backtests.fingerprints import fingerprint
from app.schemas.backtests import CostBreakdown, CostModelMetadata

PAISE = Decimal("0.01")
RUPEE = Decimal("1")


def money(value: Decimal) -> Decimal:
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)


def cost_model_metadata(
    definition: CostModelDefinition,
    *,
    brokerage_per_order_inr: Decimal | None = None,
    brokerage_rate: Decimal | None = None,
    dp_charge_per_scrip_sell_day_inr: Decimal | None = None,
) -> CostModelMetadata:
    effective_flat = definition.default_brokerage_per_order_inr if brokerage_per_order_inr is None else brokerage_per_order_inr
    effective_rate = definition.default_brokerage_rate if brokerage_rate is None else brokerage_rate
    effective_dp = definition.default_dp_charge_per_scrip_sell_day_inr if dp_charge_per_scrip_sell_day_inr is None else dp_charge_per_scrip_sell_day_inr
    return CostModelMetadata(
        code=definition.code,
        version=definition.version,
        display_name=definition.display_name,
        stt_buy_rate=definition.stt_buy_rate,
        stt_sell_rate=definition.stt_sell_rate,
        exchange_transaction_rate=definition.exchange_transaction_rate,
        sebi_turnover_rate=definition.sebi_turnover_rate,
        gst_rate=definition.gst_rate,
        stamp_duty_buy_rate=definition.stamp_duty_buy_rate,
        gst_taxable_components=list(definition.gst_taxable_components),
        monetary_rounding=definition.monetary_rounding,
        stt_rounding=definition.stt_rounding,
        default_brokerage_per_order_inr=definition.default_brokerage_per_order_inr,
        default_brokerage_rate=definition.default_brokerage_rate,
        default_dp_charge_per_scrip_sell_day_inr=definition.default_dp_charge_per_scrip_sell_day_inr,
        effective_brokerage_per_order_inr=effective_flat,
        effective_brokerage_rate=effective_rate,
        effective_dp_charge_per_scrip_sell_day_inr=effective_dp,
        cost_model_fingerprint=cost_fingerprint(
            definition,
            brokerage_per_order_inr=effective_flat,
            brokerage_rate=effective_rate,
            dp_charge_per_scrip_sell_day_inr=effective_dp,
        ),
        broker_specific_costs_excluded_by_default=(effective_flat == 0 and effective_rate == 0 and effective_dp == 0),
    )


def cost_fingerprint(
    definition: CostModelDefinition,
    *,
    brokerage_per_order_inr: Decimal,
    brokerage_rate: Decimal,
    dp_charge_per_scrip_sell_day_inr: Decimal,
) -> str:
    return fingerprint(
        {
            "code": definition.code,
            "version": definition.version,
            "stt_buy_rate": definition.stt_buy_rate,
            "stt_sell_rate": definition.stt_sell_rate,
            "exchange_transaction_rate": definition.exchange_transaction_rate,
            "sebi_turnover_rate": definition.sebi_turnover_rate,
            "gst_rate": definition.gst_rate,
            "stamp_duty_buy_rate": definition.stamp_duty_buy_rate,
            "gst_taxable_components": definition.gst_taxable_components,
            "monetary_rounding": definition.monetary_rounding,
            "stt_rounding": definition.stt_rounding,
            "brokerage_per_order_inr": brokerage_per_order_inr,
            "brokerage_rate": brokerage_rate,
            "dp_charge_per_scrip_sell_day_inr": dp_charge_per_scrip_sell_day_inr,
            "brokerage_semantics": "FLAT_PLUS_TURNOVER_RATE_PER_ORDER",
            "dp_semantics": "ONE_CHARGE_PER_SELL_LEG",
        }
    )


class IndiaCashDeliveryCostCalculator:
    def __init__(
        self,
        definition: CostModelDefinition,
        *,
        brokerage_per_order_inr: Decimal,
        brokerage_rate: Decimal,
        dp_charge_per_scrip_sell_day_inr: Decimal,
    ):
        self.definition = definition
        self.brokerage_per_order_inr = brokerage_per_order_inr
        self.brokerage_rate = brokerage_rate
        self.dp_charge_per_scrip_sell_day_inr = dp_charge_per_scrip_sell_day_inr

    def calculate(self, turnover: Decimal, side: str) -> CostBreakdown:
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if turnover < 0:
            raise ValueError("turnover cannot be negative")
        with localcontext() as context:
            context.prec = 34
            rounded_turnover = money(turnover)
            brokerage = money(self.brokerage_per_order_inr + turnover * self.brokerage_rate)
            stt_rate = self.definition.stt_buy_rate if side == "BUY" else self.definition.stt_sell_rate
            stt = (turnover * stt_rate).quantize(RUPEE, rounding=ROUND_HALF_UP).quantize(PAISE)
            exchange = money(turnover * self.definition.exchange_transaction_rate)
            sebi = money(turnover * self.definition.sebi_turnover_rate)
            gst = money((brokerage + exchange + sebi) * self.definition.gst_rate)
            stamp = money(turnover * self.definition.stamp_duty_buy_rate) if side == "BUY" else Decimal("0.00")
            dp = money(self.dp_charge_per_scrip_sell_day_inr) if side == "SELL" else Decimal("0.00")
            total = money(brokerage + stt + exchange + sebi + gst + stamp + dp)
            return CostBreakdown(
                turnover=rounded_turnover,
                brokerage=brokerage,
                stt=stt,
                exchange_transaction_charge=exchange,
                sebi_charge=sebi,
                gst=gst,
                stamp_duty=stamp,
                dp_charge=dp,
                total_charges=total,
            )


__all__ = [
    "IndiaCashDeliveryCostCalculator",
    "cost_fingerprint",
    "cost_model_metadata",
    "money",
]
