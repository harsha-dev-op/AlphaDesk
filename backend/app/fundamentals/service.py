from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.fundamentals.definitions import (
    CONCEPT_DEFINITIONS,
    CONCEPT_REGISTRY_CODE,
    CONCEPT_REGISTRY_VERSION,
    METRIC_DEFINITIONS,
    METRIC_ENGINE_CODE,
    METRIC_ENGINE_VERSION,
    RELATIVE_STRENGTH_CODE,
    RELATIVE_STRENGTH_VERSION,
    RS_PERIODS,
)
from app.fundamentals.schemas import (
    ClassificationResponse,
    ConceptMetadata,
    FilingResponse,
    FundamentalFactResponse,
    FundamentalMetricResponse,
    FundamentalMetricsResponse,
    FundamentalsMetadataResponse,
    PeerResponse,
    RelativeStrengthMetricResponse,
    RelativeStrengthResponse,
    ResearchSummaryResponse,
    SecurityFundamentalsResponse,
)
from app.ingestion.nse.definitions import DataOrigin
from app.models import (
    DailyPrice,
    FundamentalFact,
    FundamentalFiling,
    IndexDailyPrice,
    IndexMembership,
    MarketIndex,
    Security,
    SecurityIndustryClassification,
)


class FundamentalNotFoundError(LookupError):
    pass


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.00000001"))


class FundamentalIntelligenceService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def metadata(self) -> FundamentalsMetadataResponse:
        return FundamentalsMetadataResponse(
            concept_registry_code=CONCEPT_REGISTRY_CODE,
            concept_registry_version=CONCEPT_REGISTRY_VERSION,
            metric_engine_code=METRIC_ENGINE_CODE,
            metric_engine_version=METRIC_ENGINE_VERSION,
            relative_strength_code=RELATIVE_STRENGTH_CODE,
            relative_strength_version=RELATIVE_STRENGTH_VERSION,
            concepts=[
                ConceptMetadata(
                    code=item.code,
                    statement=item.statement,
                    fact_kind=item.fact_kind,
                    aliases=list(item.aliases),
                )
                for item in CONCEPT_DEFINITIONS.values()
            ],
            metrics=METRIC_DEFINITIONS,
            relative_strength_periods=RS_PERIODS,
        )

    def _security(self, symbol: str) -> Security:
        security = self.session.scalar(
            select(Security).where(func.lower(Security.symbol) == symbol.strip().lower())
        )
        if security is None:
            raise FundamentalNotFoundError("Security not found")
        return security

    def _eligible_filings(self, security_id: UUID, as_of: datetime) -> list[FundamentalFiling]:
        rows = list(
            self.session.scalars(
                select(FundamentalFiling)
                .options(selectinload(FundamentalFiling.facts))
                .where(
                    FundamentalFiling.security_id == security_id,
                    FundamentalFiling.available_at <= as_of,
                )
                .order_by(
                    FundamentalFiling.available_at,
                    FundamentalFiling.period_end,
                    FundamentalFiling.id,
                )
            )
        )
        by_id = {row.id: row for row in rows}
        superseded = {
            row.supersedes_filing_id
            for row in rows
            if row.supersedes_filing_id is not None and row.supersedes_filing_id in by_id
        }
        return [row for row in rows if row.id not in superseded]

    @staticmethod
    def _select_scope(
        filings: list[FundamentalFiling], requested_scope: str
    ) -> tuple[list[FundamentalFiling], str | None, bool]:
        exact = [row for row in filings if row.scope == requested_scope]
        if exact:
            return exact, requested_scope, False
        if requested_scope == "CONSOLIDATED":
            fallback = [row for row in filings if row.scope == "STANDALONE"]
            if fallback:
                return fallback, "STANDALONE", True
        return [], None, False

    def fundamentals(
        self, symbol: str, *, as_of: datetime, scope: str = "CONSOLIDATED"
    ) -> SecurityFundamentalsResponse:
        security = self._security(symbol)
        selected, effective_scope, fallback = self._select_scope(
            self._eligible_filings(security.id, as_of), scope
        )
        warnings = ["STANDALONE_FALLBACK_USED"] if fallback else []
        return SecurityFundamentalsResponse(
            security_id=security.id,
            symbol=security.symbol,
            requested_scope=scope,
            effective_scope=effective_scope,
            fallback_used=fallback,
            status="PARTIAL" if fallback else "READY" if selected else "UNAVAILABLE",
            as_of=as_of,
            filings=[self._filing_response(row) for row in sorted(selected, key=lambda item: (item.period_end, item.available_at), reverse=True)],
            warnings=warnings or ([] if selected else ["NO_ELIGIBLE_FUNDAMENTAL_FILINGS"]),
        )

    @staticmethod
    def _filing_response(row: FundamentalFiling) -> FilingResponse:
        return FilingResponse(
            id=row.id,
            source_filing_id=row.source_filing_id,
            filing_type=row.filing_type,
            reporting_frequency=row.reporting_frequency,
            period_start=row.period_start,
            period_end=row.period_end,
            fiscal_year=row.fiscal_year,
            fiscal_quarter=row.fiscal_quarter,
            scope=row.scope,
            audit_status=row.audit_status,
            submission_at=row.submission_at,
            available_at=row.available_at,
            revision_status=row.revision_status,
            supersedes_filing_id=row.supersedes_filing_id,
            parser_version=row.parser_version,
            normalized_fingerprint=row.normalized_fingerprint,
            facts=[
                FundamentalFactResponse(
                    normalized_concept=fact.normalized_concept,
                    source_concept=fact.source_concept,
                    value=fact.value,
                    unit=fact.unit,
                    scale=fact.scale,
                    fact_kind=fact.fact_kind,
                    value_nature=fact.value_nature,
                    period_start=fact.period_start,
                    period_end=fact.period_end,
                )
                for fact in sorted(row.facts, key=lambda item: (item.normalized_concept or "", item.source_concept))
            ],
        )

    @staticmethod
    def _facts_by_concept(filings: list[FundamentalFiling]) -> dict[str, list[tuple[FundamentalFiling, FundamentalFact]]]:
        grouped: dict[str, list[tuple[FundamentalFiling, FundamentalFact]]] = defaultdict(list)
        for filing in filings:
            for fact in filing.facts:
                if fact.normalized_concept and fact.value is not None:
                    grouped[fact.normalized_concept].append((filing, fact))
        for values in grouped.values():
            values.sort(key=lambda item: (item[1].period_end, item[0].available_at), reverse=True)
        return grouped

    @staticmethod
    def _ttm(
        grouped: dict[str, list[tuple[FundamentalFiling, FundamentalFact]]], concept: str
    ) -> tuple[Decimal | None, list[date], str | None]:
        quarterly: list[tuple[FundamentalFiling, FundamentalFact]] = []
        seen_periods: set[date] = set()
        for filing, fact in grouped.get(concept, []):
            if fact.value_nature != "QUARTERLY" or filing.reporting_frequency != "QUARTERLY":
                continue
            if not 70 <= (fact.period_end - fact.period_start).days <= 100:
                continue
            if fact.period_end not in seen_periods:
                quarterly.append((filing, fact))
                seen_periods.add(fact.period_end)
            if len(quarterly) == 4:
                break
        if len(quarterly) < 4:
            return None, [fact.period_end for _, fact in quarterly], "FOUR_INDEPENDENT_QUARTERS_REQUIRED"
        ascending = sorted((fact for _, fact in quarterly), key=lambda fact: fact.period_end)
        if any(left.period_end >= right.period_start for left, right in zip(ascending, ascending[1:])):
            return None, [fact.period_end for fact in ascending], "QUARTERLY_PERIODS_OVERLAP"
        return sum((Decimal(fact.value) for fact in ascending), Decimal(0)), [fact.period_end for fact in ascending], None

    @staticmethod
    def _growth(
        grouped: dict[str, list[tuple[FundamentalFiling, FundamentalFact]]], concept: str
    ) -> tuple[Decimal | None, list[date], str | None]:
        values = [
            item
            for item in grouped.get(concept, [])
            if item[1].value_nature == "QUARTERLY"
            and 70 <= (item[1].period_end - item[1].period_start).days <= 100
        ]
        if not values:
            return None, [], "LATEST_INDEPENDENT_QUARTER_REQUIRED"
        current_filing, current = values[0]
        prior = next(
            (
                fact
                for filing, fact in values[1:]
                if filing.fiscal_year == current_filing.fiscal_year - 1
                and filing.fiscal_quarter == current_filing.fiscal_quarter
            ),
            None,
        )
        if prior is None or prior.value in {None, Decimal(0)}:
            return None, [current.period_end], "COMPARABLE_PRIOR_YEAR_QUARTER_REQUIRED"
        return Decimal(current.value) / Decimal(prior.value) - Decimal(1), [prior.period_end, current.period_end], None

    @staticmethod
    def _latest(grouped: dict[str, list[tuple[FundamentalFiling, FundamentalFact]]], concept: str) -> tuple[Decimal | None, list[date]]:
        values = grouped.get(concept, [])
        if not values:
            return None, []
        fact = values[0][1]
        return Decimal(fact.value), [fact.period_end]

    @staticmethod
    def _latest_quarter(
        grouped: dict[str, list[tuple[FundamentalFiling, FundamentalFact]]], concept: str
    ) -> tuple[Decimal | None, list[date], str | None]:
        value = next(
            (
                fact
                for _, fact in grouped.get(concept, [])
                if fact.value_nature == "QUARTERLY"
                and 70 <= (fact.period_end - fact.period_start).days <= 100
            ),
            None,
        )
        if value is None:
            return None, [], "LATEST_INDEPENDENT_QUARTER_REQUIRED"
        return Decimal(value.value), [value.period_end], None

    def metrics(self, symbol: str, *, as_of: datetime) -> FundamentalMetricsResponse:
        security = self._security(symbol)
        filings, scope, _ = self._select_scope(self._eligible_filings(security.id, as_of), "CONSOLIDATED")
        grouped = self._facts_by_concept(filings)
        computed: dict[str, tuple[Decimal | None, list[date], str | None]] = {}
        for code, concept in (
            ("REVENUE_LATEST_QUARTER", "REVENUE"),
            ("PAT_LATEST_QUARTER", "PROFIT_AFTER_TAX"),
            ("EPS_LATEST_QUARTER", "EPS_BASIC"),
        ):
            computed[code] = self._latest_quarter(grouped, concept)
        for code, concept in (("REVENUE_YOY", "REVENUE"), ("PAT_YOY", "PROFIT_AFTER_TAX"), ("EPS_YOY", "EPS_BASIC")):
            computed[code] = self._growth(grouped, concept)
        for code, concept in (("REVENUE_TTM", "REVENUE"), ("PAT_TTM", "PROFIT_AFTER_TAX"), ("EPS_TTM", "EPS_BASIC")):
            computed[code] = self._ttm(grouped, concept)

        revenue_ttm, revenue_periods, _ = computed["REVENUE_TTM"]
        pat_ttm, pat_periods, _ = computed["PAT_TTM"]
        eps_ttm, eps_periods, _ = computed["EPS_TTM"]
        equity, equity_periods = self._latest(grouped, "TOTAL_EQUITY")
        debt, debt_periods = self._latest(grouped, "TOTAL_BORROWINGS")
        cfo, cfo_periods, cfo_reason = self._ttm(grouped, "CASH_FLOW_FROM_OPERATIONS")
        capex, capex_periods, capex_reason = self._ttm(grouped, "CAPITAL_EXPENDITURE")

        computed["NET_MARGIN_TTM"] = (
            (None, sorted(set(revenue_periods + pat_periods)), "NONZERO_REVENUE_TTM_REQUIRED")
            if revenue_ttm in {None, Decimal(0)} or pat_ttm is None
            else (pat_ttm / revenue_ttm, sorted(set(revenue_periods + pat_periods)), None)
        )
        debt_to_equity_periods = sorted(set(debt_periods + equity_periods))
        if debt is None:
            computed["DEBT_TO_EQUITY"] = (
                None,
                debt_to_equity_periods,
                "TOTAL_BORROWINGS_REQUIRED",
            )
        elif equity in {None, Decimal(0)}:
            computed["DEBT_TO_EQUITY"] = (
                None,
                debt_to_equity_periods,
                "NONZERO_EQUITY_REQUIRED",
            )
        else:
            computed["DEBT_TO_EQUITY"] = (
                debt / equity,
                debt_to_equity_periods,
                None,
            )
        equity_history = grouped.get("TOTAL_EQUITY", [])
        prior_equity = None
        prior_equity_period = None
        if equity_history:
            latest_equity_end = equity_history[0][1].period_end
            prior = next(
                (
                    fact
                    for _, fact in equity_history[1:]
                    if (latest_equity_end - fact.period_end).days >= 300
                ),
                None,
            )
            if prior is not None:
                prior_equity = Decimal(prior.value)
                prior_equity_period = prior.period_end
        average_equity = (
            None
            if equity is None or prior_equity is None
            else (equity + prior_equity) / Decimal(2)
        )
        roe_periods = sorted(set(pat_periods + equity_periods + ([prior_equity_period] if prior_equity_period else [])))
        computed["ROE_TTM"] = (
            (None, roe_periods, "PAT_TTM_AND_TWO_NONZERO_EQUITY_PERIODS_REQUIRED")
            if pat_ttm is None or average_equity in {None, Decimal(0)}
            else (pat_ttm / average_equity, roe_periods, None)
        )
        computed["FCF_TTM"] = (
            (None, sorted(set(cfo_periods + capex_periods)), cfo_reason or capex_reason or "CFO_AND_CAPEX_TTM_REQUIRED")
            if cfo is None or capex is None
            else (cfo - capex, sorted(set(cfo_periods + capex_periods)), None)
        )
        latest_close = self.session.scalar(
            select(DailyPrice.close)
            .where(
                DailyPrice.security_id == security.id,
                DailyPrice.trading_date <= as_of.date(),
                DailyPrice.ingested_at <= as_of,
            )
            .order_by(DailyPrice.trading_date.desc())
            .limit(1)
        )
        computed["PE_TTM"] = (
            (None, eps_periods, "POSITIVE_EPS_TTM_AND_POINT_IN_TIME_CLOSE_REQUIRED")
            if eps_ttm is None or eps_ttm <= 0 or latest_close is None
            else (Decimal(latest_close) / eps_ttm, eps_periods, None)
        )

        return FundamentalMetricsResponse(
            security_id=security.id,
            symbol=security.symbol,
            as_of=as_of,
            source_scope=scope,
            metrics=[
                FundamentalMetricResponse(
                    code=code,
                    version=METRIC_ENGINE_VERSION,
                    value=_round(value) if value is not None else None,
                    status="AVAILABLE" if value is not None else "UNAVAILABLE",
                    reason=reason,
                    as_of=as_of,
                    source_scope=scope,
                    underlying_periods=periods,
                )
                for code, (value, periods, reason) in computed.items()
            ],
        )

    def _classification_row(self, security_id: UUID, as_of: datetime) -> SecurityIndustryClassification | None:
        return self.session.scalar(
            select(SecurityIndustryClassification)
            .where(
                SecurityIndustryClassification.security_id == security_id,
                SecurityIndustryClassification.snapshot_date <= as_of.date(),
                SecurityIndustryClassification.available_at <= as_of,
            )
            .order_by(SecurityIndustryClassification.snapshot_date.desc(), SecurityIndustryClassification.available_at.desc())
            .limit(1)
        )

    def classification(self, symbol: str, *, as_of: datetime) -> ClassificationResponse:
        security = self._security(symbol)
        row = self._classification_row(security.id, as_of)
        if row is None:
            return ClassificationResponse(
                security_id=security.id, symbol=security.symbol, status="UNAVAILABLE", as_of=as_of,
                snapshot_date=None, available_at=None, macro_economic_sector=None, sector=None,
                industry=None, basic_industry=None, source=None, parser_version=None,
                normalized_fingerprint=None, sector_benchmark_symbol=None,
                sector_benchmark_status="UNAVAILABLE", peers={},
                warnings=["NO_CLASSIFICATION_SNAPSHOT_AVAILABLE_AS_OF"],
            )
        eligible = list(
            self.session.scalars(
                select(SecurityIndustryClassification)
                .where(
                    SecurityIndustryClassification.snapshot_date <= as_of.date(),
                    SecurityIndustryClassification.available_at <= as_of,
                )
                .order_by(SecurityIndustryClassification.security_id, SecurityIndustryClassification.snapshot_date.desc(), SecurityIndustryClassification.available_at.desc())
            )
        )
        latest: dict[UUID, SecurityIndustryClassification] = {}
        for candidate in eligible:
            latest.setdefault(candidate.security_id, candidate)
        securities = {
            item.id: item
            for item in self.session.scalars(select(Security).where(Security.id.in_(latest.keys())))
        }
        def peers(field: str) -> list[PeerResponse]:
            expected = getattr(row, field)
            return [
                PeerResponse(security_id=item.security_id, symbol=securities[item.security_id].symbol, company_name=securities[item.security_id].company_name)
                for item in latest.values()
                if item.security_id != security.id and getattr(item, field) == expected and item.security_id in securities
            ]
        benchmark = self.session.get(MarketIndex, row.sector_benchmark_index_id) if row.sector_benchmark_index_id else None
        return ClassificationResponse(
            security_id=security.id, symbol=security.symbol, status="READY", as_of=as_of,
            snapshot_date=row.snapshot_date, available_at=row.available_at,
            macro_economic_sector=row.macro_economic_sector, sector=row.sector,
            industry=row.industry, basic_industry=row.basic_industry, source=row.source,
            parser_version=row.parser_version, normalized_fingerprint=row.normalized_fingerprint,
            sector_benchmark_symbol=benchmark.symbol if benchmark else None,
            sector_benchmark_status="AVAILABLE" if benchmark else "UNAVAILABLE",
            peers={key: sorted(peers(key), key=lambda item: item.symbol) for key in ("sector", "industry", "basic_industry")},
            warnings=[] if benchmark else ["SECTOR_BENCHMARK_MAPPING_UNAVAILABLE"],
        )

    @staticmethod
    def _relative_value(security_prices: list[tuple[date, Decimal]], benchmark_prices: list[tuple[date, Decimal]], sessions: int) -> tuple[Decimal | None, Decimal | None, Decimal | None, date | None, date | None]:
        benchmark_by_date = dict(benchmark_prices)
        common = [(day, close, benchmark_by_date[day]) for day, close in security_prices if day in benchmark_by_date]
        if len(common) < sessions + 1:
            return None, None, None, None, None
        start_day, start_security, start_benchmark = common[-(sessions + 1)]
        end_day, end_security, end_benchmark = common[-1]
        if start_security <= 0 or start_benchmark <= 0:
            return None, None, None, None, None
        security_return = end_security / start_security - Decimal(1)
        benchmark_return = end_benchmark / start_benchmark - Decimal(1)
        return security_return - benchmark_return, security_return, benchmark_return, start_day, end_day

    def _index_prices(self, index: MarketIndex | None, as_of: datetime) -> list[tuple[date, Decimal]]:
        if index is None:
            return []
        return [
            (day, Decimal(close))
            for day, close in self.session.execute(
                select(IndexDailyPrice.trading_date, IndexDailyPrice.close)
                .where(
                    IndexDailyPrice.index_id == index.id,
                    IndexDailyPrice.source_mode == "OFFICIAL",
                    IndexDailyPrice.trading_date <= as_of.date(),
                    IndexDailyPrice.available_at <= as_of,
                )
                .order_by(IndexDailyPrice.trading_date)
            )
        ]

    def relative_strength(self, symbol: str, *, as_of: datetime, universe: str = "NIFTY200") -> RelativeStrengthResponse:
        security = self._security(symbol)
        index = self.session.scalar(select(MarketIndex).where(MarketIndex.symbol == universe, MarketIndex.provider == "OFFICIAL_NSE_INDICES_PUBLIC"))
        member_ids: list[UUID] = []
        if index is not None:
            member_ids = list(
                self.session.scalars(
                    select(IndexMembership.security_id).where(
                        IndexMembership.index_id == index.id,
                        IndexMembership.valid_from <= as_of.date(),
                        or_(IndexMembership.valid_to.is_(None), IndexMembership.valid_to >= as_of.date()),
                    )
                )
            )
        benchmark_prices = self._index_prices(index, as_of)
        all_prices: dict[UUID, list[tuple[date, Decimal]]] = defaultdict(list)
        ids = sorted(set(member_ids + [security.id]), key=str)
        if ids:
            for security_id, day, close in self.session.execute(
                select(DailyPrice.security_id, DailyPrice.trading_date, DailyPrice.close)
                .where(
                    DailyPrice.security_id.in_(ids),
                    DailyPrice.trading_date <= as_of.date(),
                    DailyPrice.ingested_at <= as_of,
                    DailyPrice.data_origin == DataOrigin.OFFICIAL_NSE_PUBLIC.value,
                )
                .order_by(DailyPrice.security_id, DailyPrice.trading_date)
            ):
                all_prices[security_id].append((day, Decimal(close)))
        metrics: list[RelativeStrengthMetricResponse] = []
        for code, sessions in RS_PERIODS.items():
            value, security_return, benchmark_return, start, end = self._relative_value(all_prices[security.id], benchmark_prices, sessions)
            eligible_values = [
                candidate
                for member_id in member_ids
                if (candidate := self._relative_value(all_prices[member_id], benchmark_prices, sessions)[0]) is not None
            ]
            percentile = None
            if value is not None and eligible_values:
                percentile = Decimal(sum(1 for candidate in eligible_values if candidate <= value)) * Decimal(100) / Decimal(len(eligible_values))
            metrics.append(
                RelativeStrengthMetricResponse(
                    code=code, version=RELATIVE_STRENGTH_VERSION, sessions=sessions,
                    value=_round(value) if value is not None else None,
                    security_return=_round(security_return) if security_return is not None else None,
                    benchmark_return=_round(benchmark_return) if benchmark_return is not None else None,
                    percentile=_round(percentile) if percentile is not None else None,
                    status="AVAILABLE" if value is not None else "UNAVAILABLE",
                    reason=None if value is not None else "INSUFFICIENT_COMMON_POINT_IN_TIME_PRICE_HISTORY",
                    start_date=start, end_date=end, eligible_security_count=len(eligible_values),
                )
            )
        classification = self._classification_row(security.id, as_of)
        sector_index = self.session.get(MarketIndex, classification.sector_benchmark_index_id) if classification and classification.sector_benchmark_index_id else None
        sector_prices = self._index_prices(sector_index, as_of)
        sector_metrics: list[RelativeStrengthMetricResponse] = []
        for code, sessions in RS_PERIODS.items():
            value, security_return, benchmark_return, start, end = self._relative_value(
                all_prices[security.id], sector_prices, sessions
            )
            sector_metrics.append(
                RelativeStrengthMetricResponse(
                    code=code, version=RELATIVE_STRENGTH_VERSION, sessions=sessions,
                    value=_round(value) if value is not None else None,
                    security_return=_round(security_return) if security_return is not None else None,
                    benchmark_return=_round(benchmark_return) if benchmark_return is not None else None,
                    percentile=None,
                    status="AVAILABLE" if value is not None else "UNAVAILABLE",
                    reason=None if value is not None else "INSUFFICIENT_COMMON_POINT_IN_TIME_SECTOR_HISTORY",
                    start_date=start, end_date=end, eligible_security_count=0,
                )
            )
        sector_ready = any(item.status == "AVAILABLE" for item in sector_metrics)
        return RelativeStrengthResponse(
            security_id=security.id, symbol=security.symbol, as_of=as_of, universe=universe,
            benchmark=universe,
            definition="security close-to-close price return minus benchmark close-to-close price return over common sessions",
            metrics=metrics, sector_benchmark=sector_index.symbol if sector_index else None,
            sector_metrics=sector_metrics,
            sector_metrics_status="AVAILABLE" if sector_ready else "UNAVAILABLE",
            warnings=([] if sector_ready else ["SECTOR_BENCHMARK_HISTORY_UNAVAILABLE"])
            if sector_index else ["SECTOR_BENCHMARK_MAPPING_UNAVAILABLE"],
        )

    def research_summary(self, symbol: str, *, as_of: datetime) -> ResearchSummaryResponse:
        security = self._security(symbol)
        latest_rows = list(self.session.execute(
            select(DailyPrice.trading_date, DailyPrice.close, DailyPrice.data_origin)
            .where(DailyPrice.security_id == security.id, DailyPrice.trading_date <= as_of.date(), DailyPrice.ingested_at <= as_of)
            .order_by(DailyPrice.trading_date.desc())
            .limit(2)
        ))
        latest = latest_rows[0] if latest_rows else None
        one_day_return = None
        if len(latest_rows) == 2 and Decimal(latest_rows[1][1]) > 0:
            one_day_return = Decimal(latest_rows[0][1]) / Decimal(latest_rows[1][1]) - Decimal(1)
        fundamentals = self.fundamentals(symbol, as_of=as_of)
        classification = self.classification(symbol, as_of=as_of)
        relative = self.relative_strength(symbol, as_of=as_of)
        rs_ready = any(item.status == "AVAILABLE" for item in relative.metrics)
        return ResearchSummaryResponse(
            security_id=security.id, symbol=security.symbol, company_name=security.company_name,
            as_of=as_of, latest_market_date=latest[0] if latest else None,
            latest_close=Decimal(latest[1]) if latest else None,
            one_day_return=_round(one_day_return) if one_day_return is not None else None,
            data_source=latest[2] if latest else None,
            macro_economic_sector=classification.macro_economic_sector,
            sector=classification.sector,
            industry=classification.industry,
            basic_industry=classification.basic_industry,
            market_relative_strength=relative.metrics,
            sector_benchmark=relative.sector_benchmark,
            sector_relative_strength=relative.sector_metrics,
            fundamental_status=fundamentals.status,
            classification_status=classification.status,
            relative_strength_status="READY" if rs_ready else "UNAVAILABLE",
            warnings=sorted(set(fundamentals.warnings + classification.warnings + relative.warnings)),
        )
