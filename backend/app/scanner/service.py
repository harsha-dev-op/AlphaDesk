from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import MarketIndex
from app.repositories.indices import IndexRepository
from app.repositories.securities import SecurityRepository
from app.schemas.scanner import (
    MarketScanRequest,
    MarketScanResponse,
    ScannerFilterRequest,
    ScannerFeatureMetadata,
    ScannerMetadataResponse,
    ScannerOperatorMetadata,
    ScannerResultResponse,
    ScannerTimingResponse,
    ScannerUniverseMetadata,
)
from app.technical.definitions import FeatureDefinition
from app.technical.registry import CORE_TECHNICAL_SET, FEATURE_DEFINITIONS, find_feature_set
from app.technical.service import MARKET_TIMEZONE, TechnicalFeatureService, feature_set_response


class ScannerValidationError(ValueError):
    pass


class ScannerNotFoundError(ValueError):
    pass


NUMERIC_OPERATORS = (">", ">=", "<", "<=", "=", "between")
BOOLEAN_OPERATORS = ("=",)


def _universe_metadata(index: MarketIndex) -> ScannerUniverseMetadata:
    return ScannerUniverseMetadata(
        id=index.id,
        symbol=index.symbol,
        name=index.name,
        provider=index.provider,
        exchange=index.exchange,
    )


class ScannerService:
    def __init__(self, session: Session):
        self.indices = IndexRepository(session)
        self.securities = SecurityRepository(session)
        self.technical = TechnicalFeatureService(session)

    def metadata(self) -> ScannerMetadataResponse:
        features = [
            ScannerFeatureMetadata(
                code=definition.code,
                display_name=definition.name,
                family=definition.category,
                version=definition.version,
                value_type=definition.output_type,
                unit=definition.unit,
                minimum_observations=definition.minimum_observations,
                supported_operators=list(
                    BOOLEAN_OPERATORS if definition.output_type == "BOOLEAN" else NUMERIC_OPERATORS
                ),
            )
            for definition in (
                FEATURE_DEFINITIONS[code] for code in CORE_TECHNICAL_SET.feature_codes
            )
        ]
        operators = [
            ScannerOperatorMetadata(code=code, label=code.title() if code == "between" else code, requires_upper_value=code == "between")
            for code in NUMERIC_OPERATORS
        ]
        return ScannerMetadataResponse(
            feature_set=feature_set_response(CORE_TECHNICAL_SET),
            features=features,
            operators=operators,
            adjustment_policies=["RAW", "ADJUSTED"],
            universes=[_universe_metadata(index) for index in self.indices.list()],
            latest_observation_date=self.securities.latest_price_date(),
        )

    @staticmethod
    def _validate_filter(
        definition: FeatureDefinition,
        condition: ScannerFilterRequest,
    ) -> None:
        if definition.output_type == "BOOLEAN":
            if condition.operator not in BOOLEAN_OPERATORS:
                raise ScannerValidationError(
                    f"Feature {definition.code} supports only the = operator"
                )
            if not isinstance(condition.value, bool):
                raise ScannerValidationError(f"Feature {definition.code} requires a boolean value")
            return
        if isinstance(condition.value, bool):
            raise ScannerValidationError(f"Feature {definition.code} requires a numeric value")
        if condition.operator == "between":
            if condition.upper_value is None:
                raise ScannerValidationError("between requires upper_value")
            if Decimal(condition.value) > condition.upper_value:
                raise ScannerValidationError("between requires value to be less than or equal to upper_value")

    @staticmethod
    def _matches(candidate: Decimal | bool, condition: ScannerFilterRequest) -> bool:
        if condition.operator == "=":
            return candidate == condition.value
        if isinstance(candidate, bool) or isinstance(condition.value, bool):
            return False
        expected = Decimal(condition.value)
        if condition.operator == ">":
            return candidate > expected
        if condition.operator == ">=":
            return candidate >= expected
        if condition.operator == "<":
            return candidate < expected
        if condition.operator == "<=":
            return candidate <= expected
        if condition.operator == "between" and condition.upper_value is not None:
            return expected <= candidate <= condition.upper_value
        raise ScannerValidationError(f"Unsupported operator: {condition.operator}")

    def scan(self, request: MarketScanRequest) -> MarketScanResponse:
        started = time.perf_counter()
        if request.as_of.tzinfo is None or request.as_of.utcoffset() is None:
            raise ScannerValidationError("as_of must include a timezone offset")
        if request.observation_date > request.as_of.astimezone(MARKET_TIMEZONE).date():
            raise ScannerValidationError("observation_date cannot be after the scanner as_of date")
        feature_set = find_feature_set(request.feature_set, request.feature_set_version)
        if (
            not feature_set
            or feature_set.code != CORE_TECHNICAL_SET.code
            or feature_set.version != CORE_TECHNICAL_SET.version
        ):
            raise ScannerNotFoundError("Feature set or version not found")

        for condition in request.filters:
            definition = FEATURE_DEFINITIONS.get(condition.feature)
            if not definition or condition.feature not in feature_set.feature_codes:
                raise ScannerValidationError(f"Unknown feature: {condition.feature}")
            self._validate_filter(definition, condition)

        phase = time.perf_counter()
        market_index = self.indices.get_by_name_or_symbol(request.universe)
        if not market_index:
            raise ScannerNotFoundError("Universe not found")
        memberships = self.indices.members_as_of(market_index.id, request.observation_date)
        securities_by_id = {membership.security.id: membership.security for membership in memberships}
        securities = sorted(
            securities_by_id.values(),
            key=lambda security: (security.symbol.casefold(), str(security.id)),
        )
        universe_resolution_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        feature_responses = self.technical.compute_latest_batch(
            securities,
            adjustment_policy=request.adjustment_policy,
            as_of=request.as_of,
            observation_date=request.observation_date,
        )
        feature_computation_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        matched = []
        unavailable_count = 0
        for security, response in zip(securities, feature_responses, strict=True):
            if not response.items or response.items[0].observation_date != request.observation_date:
                unavailable_count += 1
                continue
            observation = response.items[0]
            if observation.available_at > request.as_of:
                unavailable_count += 1
                continue
            values = [observation.values[condition.feature] for condition in request.filters]
            if any(value is None for value in values):
                unavailable_count += 1
                continue
            if all(
                self._matches(value, condition)
                for value, condition in zip(values, request.filters, strict=True)
            ):
                matched.append((security, response, observation))
        predicate_evaluation_ms = (time.perf_counter() - phase) * 1_000

        phase = time.perf_counter()
        requested_codes = tuple(dict.fromkeys(condition.feature for condition in request.filters))
        results = [
            ScannerResultResponse(
                security_id=security.id,
                symbol=security.symbol,
                company_name=security.company_name,
                exchange=security.exchange,
                observation_date=observation.observation_date,
                as_of=request.as_of,
                available_at=observation.available_at,
                matched_values={code: observation.values[code] for code in requested_codes},
                feature_versions={code: response.feature_versions[code] for code in requested_codes},
                feature_set_code=response.feature_set.code,
                feature_set_version=response.feature_set.version,
                adjustment_policy=response.adjustment_policy,
                dataset=response.dataset,
                input_fingerprint=response.dataset.fingerprint,
                quality_warnings=response.quality_warnings,
            )
            for security, response, observation in matched
        ]
        digest = hashlib.sha256()
        digest.update(
            (
                f"{market_index.id}|{request.observation_date}|{request.as_of.isoformat()}|"
                f"{feature_set.code}|{feature_set.version}|{request.adjustment_policy}|{request.logic}"
            ).encode()
        )
        for condition in request.filters:
            digest.update(
                f"|{condition.feature}|{condition.operator}|{condition.value}|{condition.upper_value}".encode()
            )
        for security, response in zip(securities, feature_responses, strict=True):
            digest.update(f"|{security.id}|{response.dataset.fingerprint}".encode())
        response_build_ms = (time.perf_counter() - phase) * 1_000
        total_service_ms = (time.perf_counter() - started) * 1_000

        return MarketScanResponse(
            universe=_universe_metadata(market_index),
            observation_date=request.observation_date,
            as_of=request.as_of,
            executed_at=datetime.now(UTC),
            feature_set=feature_set_response(feature_set),
            feature_versions={code: FEATURE_DEFINITIONS[code].version for code in feature_set.feature_codes},
            adjustment_policy=request.adjustment_policy,
            logic=request.logic,
            filters=request.filters,
            universe_member_count=len(securities),
            evaluated_security_count=len(feature_responses),
            unavailable_security_count=unavailable_count,
            matched_count=len(results),
            scan_fingerprint=digest.hexdigest(),
            timings=ScannerTimingResponse(
                universe_resolution_ms=round(universe_resolution_ms, 3),
                feature_computation_ms=round(feature_computation_ms, 3),
                predicate_evaluation_ms=round(predicate_evaluation_ms, 3),
                response_build_ms=round(response_build_ms, 3),
                total_service_ms=round(total_service_ms, 3),
            ),
            results=results,
        )
