from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select

from app.models import (
    CorporateAction,
    DailyPrice,
    DataIngestionRun,
    IndexMembership,
    MarketIndex,
    ResearchExperiment,
    ResearchExperimentRun,
    Security,
    TradingCalendar,
)
from app.research.composition import consensus_status
from app.research.definitions import CONSENSUS_N_OF_M_V1
from app.research.registry import COMPOSITION_POLICIES, build_composition_policy_registry
from app.research.service import (
    CompositionPolicyNotFoundError,
    ResearchInvariantError,
    ResearchService,
    ResearchValidationError,
)
from app.schemas.research import (
    CompositionEvaluationRequest,
    ExperimentCreateRequest,
)
from app.strategies import StrategyNotFoundError, StrategyValidationError
from app.strategies.registry import strategy_catalog
from app.strategies.service import normalize_parameters, strategy_fingerprint
from app.technical.definitions import CORE_TECHNICAL_SET, FEATURE_DEFINITIONS, STRATEGY_TECHNICAL_SET

START = date(2024, 1, 1)
OBSERVATION = START + timedelta(days=219)
IST = ZoneInfo("Asia/Kolkata")


def _security(symbol: str) -> Security:
    return Security(
        exchange="NSE",
        symbol=symbol,
        trading_symbol=f"{symbol}-EQ",
        company_name=f"{symbol.title()} Research",
        security_type="EQUITY",
        currency="INR",
        is_active=True,
    )


def seed_research_data(db) -> tuple[MarketIndex, dict[str, Security]]:
    universe = MarketIndex(
        name="Research Historical", symbol="RESEARCH100", provider="PHASE7", exchange="NSE"
    )
    securities = {symbol: _security(symbol) for symbol in ("ALPHA", "PULLBACK", "FUTURE")}
    db.add_all([universe, *securities.values()])
    db.flush()
    db.add_all(
        [
            IndexMembership(
                index_id=universe.id,
                security_id=securities["ALPHA"].id,
                valid_from=START,
                source="PHASE7",
            ),
            IndexMembership(
                index_id=universe.id,
                security_id=securities["PULLBACK"].id,
                valid_from=START,
                valid_to=OBSERVATION,
                source="PHASE7",
            ),
            IndexMembership(
                index_id=universe.id,
                security_id=securities["FUTURE"].id,
                valid_from=OBSERVATION + timedelta(days=1),
                source="PHASE7",
            ),
        ]
    )
    for index in range(225):
        trading_date = START + timedelta(days=index)
        db.add(
            TradingCalendar(
                exchange="NSE",
                trading_date=trading_date,
                is_trading_day=True,
                session_close=time(15, 30),
            )
        )
        alpha_close = Decimal("100") + Decimal(index) * Decimal("0.30")
        if index == 219:
            alpha_close += Decimal("3")
        pullback_close = Decimal("100") + Decimal(index) * Decimal("0.40")
        if index >= 212:
            pullback_close = Decimal("190") - Decimal(index - 212) * Decimal("2.5")
        closes = {
            "ALPHA": alpha_close,
            "PULLBACK": pullback_close,
            "FUTURE": Decimal("125"),
        }
        for symbol, close in closes.items():
            volume = (
                2_000_000
                if symbol == "ALPHA" and index == 219
                else 1_000_000 + index * 100
            )
            high = (
                close + Decimal("0.2")
                if symbol == "ALPHA" and index == 219
                else close + Decimal("1")
            )
            db.add(
                DailyPrice(
                    security_id=securities[symbol].id,
                    trading_date=trading_date,
                    open=close - Decimal("0.5"),
                    high=high,
                    low=close - Decimal("1"),
                    close=close,
                    volume=volume,
                    traded_value=close * volume,
                    source="PHASE7",
                )
            )
    db.add(
        DataIngestionRun(
            dataset_code="research_fixture",
            dataset_version="2024.v1",
            provider="PHASE7",
            status="SUCCESS",
            completed_at=datetime(2025, 1, 1, tzinfo=UTC),
            records_written=675,
        )
    )
    db.commit()
    return universe, securities


def composition_request(**overrides) -> CompositionEvaluationRequest:
    payload = {
        "policy_code": "CONSENSUS_N_OF_M",
        "policy_version": "1",
        "universe": "RESEARCH100",
        "observation_date": OBSERVATION,
        "as_of": datetime.combine(OBSERVATION, time(15, 30), tzinfo=IST),
        "adjustment_policy": "ADJUSTED",
        "required_match_count": 2,
        "components": [
            {"strategy_code": "MOMENTUM_TREND", "strategy_version": "1"},
            {"strategy_code": "BREAKOUT_20D", "strategy_version": "1"},
            {"strategy_code": "MEAN_REVERSION_PULLBACK", "strategy_version": "1"},
        ],
    }
    payload.update(overrides)
    return CompositionEvaluationRequest(**payload)


def experiment_request(**composition_overrides) -> ExperimentCreateRequest:
    return ExperimentCreateRequest(
        name="Three-strategy consensus",
        description="Immutable Phase 7 provenance fixture",
        composition=composition_request(**composition_overrides),
    )


def test_policy_registry_is_immutable_versioned_and_validated():
    assert list(COMPOSITION_POLICIES) == [("CONSENSUS_N_OF_M", "1")]
    with pytest.raises(TypeError):
        COMPOSITION_POLICIES[("NEW", "1")] = CONSENSUS_N_OF_M_V1  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        CONSENSUS_N_OF_M_V1.policy_version = "2"  # type: ignore[misc]
    with pytest.raises(ValueError, match="Duplicate composition policy"):
        build_composition_policy_registry((CONSENSUS_N_OF_M_V1, CONSENSUS_N_OF_M_V1))
    with pytest.raises(ValueError, match="minimum"):
        build_composition_policy_registry(
            (replace(CONSENSUS_N_OF_M_V1, minimum_components=1),)
        )


def test_phase4_feature_and_strategy_fingerprint_contracts_remain_exact():
    assert len(CORE_TECHNICAL_SET.feature_codes) == 36
    assert len(STRATEGY_TECHNICAL_SET.feature_codes) == 38
    assert "EMA_200" not in FEATURE_DEFINITIONS
    expected = {
        "MOMENTUM_TREND": "c9b095052fd63bab53c472f2d4c18163d28fa6c3bc3abcbd378fbf6a796781ef",
        "BREAKOUT_20D": "b9c7972449a3530a132ce52a0714a8a6884be7341bb960dc509cefd8483d17c0",
        "MEAN_REVERSION_PULLBACK": "f316d3159cf5398d3a8702e99dc48ce8532ae7ec898d5defd16def33b91fad03",
    }
    assert {
        definition.strategy_code: strategy_fingerprint(
            definition, normalize_parameters(definition, {}), "ADJUSTED"
        )
        for definition in strategy_catalog()
    } == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"required_match_count": 0},
        {"required_match_count": 4},
        {"components": [{"strategy_code": "MOMENTUM_TREND"}]},
        {
            "components": [
                {"strategy_code": "MOMENTUM_TREND"},
                {"strategy_code": "MOMENTUM_TREND"},
            ]
        },
        {
            "components": [
                {"strategy_code": "MOMENTUM_TREND", "strategy_version": str(index)}
                for index in range(11)
            ]
        },
    ],
)
def test_composition_request_rejects_invalid_counts_and_duplicates(payload):
    with pytest.raises(ValidationError):
        composition_request(**payload)


def test_unknown_policy_strategy_version_and_parameters_are_rejected(db):
    seed_research_data(db)
    service = ResearchService(db)
    with pytest.raises(CompositionPolicyNotFoundError, match="Unknown"):
        service.evaluate(composition_request(policy_code="UNKNOWN"))
    with pytest.raises(CompositionPolicyNotFoundError, match="Unsupported"):
        service.evaluate(composition_request(policy_version="2"))
    components = composition_request().model_dump()["components"]
    components[0]["strategy_code"] = "UNKNOWN"
    with pytest.raises(StrategyNotFoundError, match="Unknown"):
        service.evaluate(composition_request(components=components))
    components[0]["strategy_code"] = "MOMENTUM_TREND"
    components[0]["strategy_version"] = "99"
    with pytest.raises(StrategyNotFoundError, match="Unsupported"):
        service.evaluate(composition_request(components=components))
    components[0]["strategy_version"] = "1"
    components[0]["parameter_overrides"] = {"unknown": Decimal("1")}
    with pytest.raises(StrategyValidationError, match="Unknown strategy parameter"):
        service.evaluate(composition_request(components=components))


@pytest.mark.parametrize(
    ("matched", "insufficient", "required", "expected"),
    [
        (3, 0, 2, "MATCHED"),
        (2, 1, 2, "MATCHED"),
        (1, 0, 2, "NOT_MATCHED"),
        (1, 1, 2, "INSUFFICIENT_FEATURE_HISTORY"),
        (0, 2, 2, "INSUFFICIENT_FEATURE_HISTORY"),
        (1, 0, 1, "MATCHED"),
        (2, 0, 3, "NOT_MATCHED"),
    ],
)
def test_consensus_semantics(matched, insufficient, required, expected):
    assert (
        consensus_status(
            matched_count=matched,
            insufficient_count=insufficient,
            required_count=required,
        )
        == expected
    )


def test_composition_returns_all_members_with_component_audit_and_correct_status(db):
    seed_research_data(db)
    response = ResearchService(db).evaluate(composition_request())
    assert [item.symbol for item in response.results] == ["ALPHA", "PULLBACK"]
    assert response.evaluated_security_count == response.universe_member_count == 2
    assert response.matched_count == 1
    assert response.not_matched_count == 1
    assert response.insufficient_history_count == 0
    alpha = response.results[0]
    assert alpha.status == "MATCHED"
    assert alpha.matched_strategy_count == alpha.required_match_count == 2
    assert [item.strategy_code for item in alpha.component_results] == [
        "BREAKOUT_20D",
        "MEAN_REVERSION_PULLBACK",
        "MOMENTUM_TREND",
    ]
    assert all(item.conditions and item.result_fingerprint for item in alpha.component_results)
    assert response.union_feature_codes == sorted(response.union_feature_codes)
    assert response.engine_provenance["composition_engine_version"] == "1"


def test_insufficient_history_is_never_coerced_to_zero(db):
    seed_research_data(db)
    early = START + timedelta(days=20)
    response = ResearchService(db).evaluate(
        composition_request(
            observation_date=early,
            as_of=datetime.combine(early, time(15, 30), tzinfo=IST),
        )
    )
    assert response.insufficient_history_count == 2
    assert all(item.status == "INSUFFICIENT_FEATURE_HISTORY" for item in response.results)
    assert all(
        any(value is None for value in component.required_feature_values.values())
        for item in response.results
        for component in item.component_results
    )


def test_one_of_m_and_m_of_m_thresholds(db):
    seed_research_data(db)
    service = ResearchService(db)
    one = service.evaluate(composition_request(required_match_count=1))
    all_required = service.evaluate(composition_request(required_match_count=3))
    assert one.matched_count == 2
    assert all_required.matched_count == 0
    assert all(item.status == "NOT_MATCHED" for item in all_required.results)


def test_component_order_and_decimal_normalization_are_fingerprint_invariant(db):
    seed_research_data(db)
    service = ResearchService(db)
    first = composition_request()
    reversed_components = list(reversed(first.model_dump()["components"]))
    reversed_components[-1]["parameter_overrides"] = {"min_momentum_3m": Decimal("0.1000")}
    second = composition_request(components=reversed_components)
    left = service.evaluate(first)
    right = service.evaluate(second)
    assert left.normalized_request == right.normalized_request
    assert left.composition_config_fingerprint == right.composition_config_fingerprint
    assert left.composition_dataset_fingerprint == right.composition_dataset_fingerprint
    assert left.composition_run_fingerprint == right.composition_run_fingerprint
    assert [item.composition_result_fingerprint for item in left.results] == [
        item.composition_result_fingerprint for item in right.results
    ]


def test_repeated_run_is_deterministic_and_exec_timestamp_is_not_fingerprinted(db):
    seed_research_data(db)
    service = ResearchService(db)
    first = service.evaluate(composition_request())
    second = service.evaluate(composition_request())
    assert second.executed_at >= first.executed_at
    assert first.composition_config_fingerprint == second.composition_config_fingerprint
    assert first.composition_dataset_fingerprint == second.composition_dataset_fingerprint
    assert first.composition_run_fingerprint == second.composition_run_fingerprint


def test_composition_uses_one_shared_feature_load_and_bounded_queries(db, monkeypatch):
    seed_research_data(db)
    service = ResearchService(db)
    compute_calls = 0
    original = service.technical.compute_latest_batch

    def counted(*args, **kwargs):
        nonlocal compute_calls
        compute_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(service.technical, "compute_latest_batch", counted)
    select_count = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count
        if statement.lstrip().lower().startswith("select"):
            select_count += 1

    engine = db.get_bind()
    event.listen(engine, "after_cursor_execute", count_selects)
    try:
        response = service.evaluate(composition_request())
    finally:
        event.remove(engine, "after_cursor_execute", count_selects)
    assert compute_calls == 1
    assert select_count == 7
    assert sum(len(item.component_results) for item in response.results) == 6


def test_future_price_action_revision_and_membership_are_invariant(db):
    _, securities = seed_research_data(db)
    service = ResearchService(db)
    request = composition_request()
    before = service.evaluate(request)
    future_price = db.scalar(
        select(DailyPrice).where(
            DailyPrice.security_id == securities["ALPHA"].id,
            DailyPrice.trading_date == OBSERVATION + timedelta(days=1),
        )
    )
    assert future_price is not None
    future_price.close = future_price.high
    original = CorporateAction(
        security_id=securities["ALPHA"].id,
        action_type="STOCK_SPLIT",
        ex_date=OBSERVATION + timedelta(days=1),
        ratio_numerator=2,
        ratio_denominator=1,
        source="FUTURE",
        available_at=request.as_of - timedelta(hours=1),
    )
    db.add(original)
    db.commit()
    db.add(
        CorporateAction(
            security_id=securities["ALPHA"].id,
            action_type="STOCK_SPLIT",
            ex_date=OBSERVATION,
            ratio_numerator=4,
            ratio_denominator=1,
            source="REVISION",
            available_at=request.as_of + timedelta(days=1),
            supersedes_action_id=original.id,
        )
    )
    db.commit()
    after = service.evaluate(request)
    assert [item.symbol for item in after.results] == ["ALPHA", "PULLBACK"]
    assert after.composition_dataset_fingerprint == before.composition_dataset_fingerprint
    assert after.composition_run_fingerprint == before.composition_run_fingerprint


def test_availability_clock_and_raw_adjusted_modes_are_explicit(db):
    _, securities = seed_research_data(db)
    service = ResearchService(db)
    preclose = service.evaluate(
        composition_request(
            as_of=datetime.combine(OBSERVATION, time(15, 29, 59), tzinfo=IST)
        )
    )
    assert all(item.status == "INSUFFICIENT_FEATURE_HISTORY" for item in preclose.results)
    db.add(
        CorporateAction(
            security_id=securities["ALPHA"].id,
            action_type="STOCK_SPLIT",
            ex_date=OBSERVATION,
            ratio_numerator=2,
            ratio_denominator=1,
            source="KNOWN",
            available_at=datetime.combine(OBSERVATION - timedelta(days=2), time(10), tzinfo=IST),
        )
    )
    db.commit()
    raw = service.evaluate(composition_request(adjustment_policy="RAW"))
    adjusted = service.evaluate(composition_request(adjustment_policy="ADJUSTED"))
    assert raw.composition_config_fingerprint != adjusted.composition_config_fingerprint
    assert raw.composition_dataset_fingerprint != adjusted.composition_dataset_fingerprint


def test_naive_as_of_future_observation_and_empty_membership_are_rejected(db):
    seed_research_data(db)
    service = ResearchService(db)
    with pytest.raises(ResearchValidationError, match="timezone"):
        service.evaluate(
            composition_request(as_of=datetime.combine(OBSERVATION, time(15, 30)))
        )
    with pytest.raises(ResearchValidationError, match="cannot be after"):
        service.evaluate(
            composition_request(
                as_of=datetime.combine(OBSERVATION - timedelta(days=1), time(15, 30), tzinfo=IST)
            )
        )
    db.add(MarketIndex(name="Empty Research", symbol="EMPTY7", provider="PHASE7", exchange="NSE"))
    db.commit()
    with pytest.raises(ResearchValidationError, match="no members"):
        service.evaluate(composition_request(universe="EMPTY7"))


def test_ephemeral_evaluation_does_not_persist(db):
    seed_research_data(db)
    ResearchService(db).evaluate(composition_request())
    assert db.scalar(select(func.count()).select_from(ResearchExperiment)) == 0
    assert db.scalar(select(func.count()).select_from(ResearchExperimentRun)) == 0


def test_create_experiment_persists_normalized_definition_and_initial_run(db):
    seed_research_data(db)
    created = ResearchService(db).create_experiment(experiment_request())
    assert isinstance(created.experiment.id, UUID)
    assert created.experiment.latest_run is not None
    assert created.experiment.latest_run.replay_status == "INITIAL"
    assert created.experiment.config_fingerprint == created.initial_evaluation.composition_config_fingerprint
    assert created.experiment.latest_run.run_fingerprint == created.initial_evaluation.composition_run_fingerprint
    assert len(created.experiment.latest_run.member_outcomes) == 2
    assert db.scalar(select(func.count()).select_from(ResearchExperiment)) == 1
    assert db.scalar(select(func.count()).select_from(ResearchExperimentRun)) == 1


def test_experiment_list_detail_runs_and_exact_replay_are_stable(db):
    seed_research_data(db)
    service = ResearchService(db)
    created = service.create_experiment(experiment_request())
    experiment_id = created.experiment.id
    replay = service.replay(experiment_id)
    assert replay.run.replay_status == "REPRODUCED"
    assert replay.run.reference_run_id == created.experiment.latest_run.id
    listing = service.list_experiments(page=1, page_size=25)
    assert listing.total == 1
    assert listing.items[0].latest_replay_status == "REPRODUCED"
    detail = service.experiment_detail(experiment_id)
    assert detail.latest_run is not None and detail.latest_run.id == replay.run.id
    runs = service.experiment_runs(experiment_id, page=1, page_size=25)
    assert runs.total == 2
    assert [item.replay_status for item in runs.items] == ["INITIAL", "REPRODUCED"]


def test_experiment_definition_and_run_records_reject_mutation_and_deletion(db):
    seed_research_data(db)
    created = ResearchService(db).create_experiment(experiment_request())
    experiment = db.get(ResearchExperiment, created.experiment.id)
    assert experiment is not None
    experiment.name = "Mutated"
    with pytest.raises(ValueError, match="immutable"):
        db.commit()
    db.rollback()
    run = db.scalar(
        select(ResearchExperimentRun).where(
            ResearchExperimentRun.experiment_id == created.experiment.id
        )
    )
    assert run is not None
    run.replay_status = "REPRODUCED"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()
    db.delete(db.get(ResearchExperimentRun, run.id))
    with pytest.raises(ValueError, match="append-only"):
        db.commit()


def test_replay_detects_dataset_drift(db):
    _, securities = seed_research_data(db)
    service = ResearchService(db)
    created = service.create_experiment(experiment_request())
    historical = db.scalar(
        select(DailyPrice).where(
            DailyPrice.security_id == securities["ALPHA"].id,
            DailyPrice.trading_date == OBSERVATION - timedelta(days=1),
        )
    )
    assert historical is not None
    historical.close += Decimal("0.1")
    db.commit()
    replay = service.replay(created.experiment.id)
    assert replay.run.replay_status == "DATASET_DRIFT_DETECTED"


def test_replay_detects_engine_or_result_drift_with_same_config_and_dataset(db, monkeypatch):
    seed_research_data(db)
    service = ResearchService(db)
    created = service.create_experiment(experiment_request())
    original = service.evaluate

    def changed(request):
        result = original(request)
        return result.model_copy(update={"composition_run_fingerprint": "f" * 64})

    monkeypatch.setattr(service, "evaluate", changed)
    replay = service.replay(created.experiment.id)
    assert replay.run.replay_status == "ENGINE_OR_RESULT_DRIFT_DETECTED"


def test_replay_rejects_config_fingerprint_mismatch(db, monkeypatch):
    seed_research_data(db)
    service = ResearchService(db)
    created = service.create_experiment(experiment_request())
    original = service.evaluate

    def changed(request):
        result = original(request)
        return result.model_copy(update={"composition_config_fingerprint": "0" * 64})

    monkeypatch.setattr(service, "evaluate", changed)
    with pytest.raises(ResearchInvariantError, match="configuration fingerprint"):
        service.replay(created.experiment.id)
    assert service.experiment_runs(created.experiment.id, page=1, page_size=25).total == 1


def test_research_api_endpoints_validation_pagination_and_uuid_errors(db, client):
    seed_research_data(db)
    metadata = client.get("/api/v1/research/compositions/metadata")
    assert metadata.status_code == 200
    assert metadata.json()["policies"][0]["policy_code"] == "CONSENSUS_N_OF_M"
    payload = composition_request().model_dump(mode="json")
    evaluated = client.post("/api/v1/research/compositions/evaluate", json=payload)
    assert evaluated.status_code == 200
    assert evaluated.json()["evaluated_security_count"] == 2
    assert db.scalar(select(func.count()).select_from(ResearchExperiment)) == 0

    created = client.post(
        "/api/v1/research/experiments",
        json={"name": "API experiment", "description": None, "composition": payload},
    )
    assert created.status_code == 201
    experiment_id = created.json()["experiment"]["id"]
    assert client.get("/api/v1/research/experiments?page=1&page_size=10").status_code == 200
    assert client.get(f"/api/v1/research/experiments/{experiment_id}").status_code == 200
    assert client.get(f"/api/v1/research/experiments/{experiment_id}/runs").status_code == 200
    replay = client.post(f"/api/v1/research/experiments/{experiment_id}/runs")
    assert replay.status_code == 200
    assert replay.json()["run"]["replay_status"] == "REPRODUCED"
    assert client.get("/api/v1/research/experiments?page_size=101").status_code == 422
    assert client.get("/api/v1/research/experiments/not-a-uuid").status_code == 422
    assert client.get(f"/api/v1/research/experiments/{UUID(int=0)}").status_code == 404


def test_research_api_returns_typed_4xx_without_internal_trace(db, client):
    seed_research_data(db)
    payload = composition_request().model_dump(mode="json")
    payload["policy_code"] = "UNKNOWN"
    missing_policy = client.post("/api/v1/research/compositions/evaluate", json=payload)
    assert missing_policy.status_code == 404
    assert "traceback" not in missing_policy.text.lower()
    payload["policy_code"] = "CONSENSUS_N_OF_M"
    payload["components"][0]["parameter_overrides"] = {"min_momentum_3m": "99"}
    invalid_parameter = client.post("/api/v1/research/compositions/evaluate", json=payload)
    assert invalid_parameter.status_code == 422
    assert "traceback" not in invalid_parameter.text.lower()
