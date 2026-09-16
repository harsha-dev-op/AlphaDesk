import argparse
from datetime import UTC, date, datetime, time, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.providers import DemoMarketDataProvider
from app.database.session import SessionLocal
from app.models import CorporateAction, DailyPrice, DataIngestionRun, FundamentalReport, IndexDailyPrice, IndexMembership, MarketIndex, Security, StrategyDefinition, TradingCalendar

logger = structlog.get_logger(__name__)


def seed_demo(session: Session) -> dict[str, int]:
    provider = DemoMarketDataProvider()
    if session.scalar(select(Security.id).where(Security.symbol == "ALPHAIND")):
        market_index = session.scalar(
            select(MarketIndex).where(MarketIndex.symbol == "NIFTYDEMO100")
        )
        index_price_count = (
            _seed_index_prices(session, provider, market_index)
            if market_index is not None
            else 0
        )
        session.commit()
        return {
            "securities": 0,
            "prices": 0,
            "index_prices": index_price_count,
            "message": "demo data already present",
        }

    logger.info("ingestion_started", provider=provider.code, dataset="phase1_demo")
    run = DataIngestionRun(dataset_code="phase1_demo", dataset_version="2025.03.v1", provider=provider.code, status="RUNNING")
    session.add(run)
    session.flush()

    securities: dict[str, Security] = {}
    for payload in provider.get_security_master():
        security = Security(**payload)
        session.add(security)
        securities[security.symbol] = security
    session.flush()

    market_index = MarketIndex(name="NIFTY Demo 100", symbol="NIFTYDEMO100", provider=provider.code, exchange="NSE")
    session.add(market_index)
    session.flush()
    index_price_count = _seed_index_prices(session, provider, market_index, run.id)

    for payload in provider.get_daily_prices(date(2025, 1, 2), date(2025, 3, 31)):
        symbol = payload.pop("symbol")
        session.add(DailyPrice(security_id=securities[symbol].id, **payload))
    for payload in provider.get_corporate_actions():
        symbol = payload.pop("symbol")
        session.add(CorporateAction(security_id=securities[symbol].id, **payload))
    for payload in provider.get_index_membership():
        symbol = payload.pop("symbol")
        payload.pop("index_symbol")
        session.add(IndexMembership(index_id=market_index.id, security_id=securities[symbol].id, **payload))
    for payload in provider.get_fundamentals():
        symbol = payload.pop("symbol")
        session.add(FundamentalReport(security_id=securities[symbol].id, **payload))

    day = date(2025, 1, 1)
    while day <= date(2025, 3, 31):
        is_weekday = day.weekday() < 5
        is_holiday = day == date(2025, 2, 26)
        session.add(TradingCalendar(exchange="NSE", trading_date=day, is_trading_day=is_weekday and not is_holiday, session_open=time(9, 15) if is_weekday and not is_holiday else None, session_close=time(15, 30) if is_weekday and not is_holiday else None, session_type="REGULAR" if is_weekday and not is_holiday else "CLOSED", notes="Demo exchange holiday fixture" if is_holiday else ("Weekend" if not is_weekday else None), data_origin="DEMO"))
        day += timedelta(days=1)

    session.add(StrategyDefinition(strategy_code="PHASE2_PLACEHOLDER", name="Reserved Phase 2 Strategy Contract", version="0.0.0", description="Schema placeholder only; contains no trading logic.", is_active=False))
    price_count = len(provider.get_daily_prices(date(2025, 1, 2), date(2025, 3, 31)))
    run.status = "SUCCESS"
    run.completed_at = datetime.now(UTC)
    run.records_written = len(securities) + price_count + index_price_count
    session.commit()
    logger.info("ingestion_completed", provider=provider.code, records=run.records_written)
    return {
        "securities": len(securities),
        "prices": price_count,
        "index_prices": index_price_count,
    }


def _seed_index_prices(
    session: Session,
    provider: DemoMarketDataProvider,
    market_index: MarketIndex,
    ingestion_run_id=None,
) -> int:
    existing = set(
        session.scalars(
            select(IndexDailyPrice.trading_date).where(
                IndexDailyPrice.index_id == market_index.id,
                IndexDailyPrice.source_mode == "DEMO",
            )
        )
    )
    inserted = 0
    for raw_payload in provider.get_index_daily_prices():
        payload = dict(raw_payload)
        payload.pop("index_symbol")
        if payload["trading_date"] in existing:
            continue
        session.add(
            IndexDailyPrice(
                index_id=market_index.id,
                ingestion_run_id=ingestion_run_id,
                **payload,
            )
        )
        inserted += 1
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed deterministic AlphaDesk demo data")
    parser.parse_args()
    with SessionLocal() as session:
        result = seed_demo(session)
    print(result)


if __name__ == "__main__":
    main()
