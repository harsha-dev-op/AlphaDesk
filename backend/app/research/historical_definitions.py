from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class CompositionExecutionPolicyDefinition:
    policy_code: str
    policy_version: str
    display_name: str
    backtest_profile_code: str
    backtest_profile_version: str
    signal_timing: Literal["AFTER_SESSION_CLOSE"]
    entry_timing: Literal["IMMEDIATE_NEXT_VALID_RAW_OPEN"]
    exit_timing: Literal["OPEN_AFTER_FIXED_HOLDING_SESSIONS"]
    direction: Literal["LONG_ONLY"]
    overlap_policy: Literal["ONE_OPEN_POSITION_PER_SECURITY"]
    default_holding_sessions: int
    minimum_holding_sessions: int
    maximum_holding_sessions: int


COMPOSITION_NEXT_OPEN_FIXED_HOLD_V1 = CompositionExecutionPolicyDefinition(
    policy_code="COMPOSITION_NEXT_OPEN_FIXED_HOLD",
    policy_version="1",
    display_name="Composition next-open fixed-hold simulation",
    backtest_profile_code="NEXT_OPEN_FIXED_HOLD",
    backtest_profile_version="1",
    signal_timing="AFTER_SESSION_CLOSE",
    entry_timing="IMMEDIATE_NEXT_VALID_RAW_OPEN",
    exit_timing="OPEN_AFTER_FIXED_HOLDING_SESSIONS",
    direction="LONG_ONLY",
    overlap_policy="ONE_OPEN_POSITION_PER_SECURITY",
    default_holding_sessions=20,
    minimum_holding_sessions=1,
    maximum_holding_sessions=252,
)


def find_composition_execution_policy(
    code: str,
    version: str,
) -> CompositionExecutionPolicyDefinition | None:
    if (
        code == COMPOSITION_NEXT_OPEN_FIXED_HOLD_V1.policy_code
        and version == COMPOSITION_NEXT_OPEN_FIXED_HOLD_V1.policy_version
    ):
        return COMPOSITION_NEXT_OPEN_FIXED_HOLD_V1
    return None


__all__ = [
    "COMPOSITION_NEXT_OPEN_FIXED_HOLD_V1",
    "CompositionExecutionPolicyDefinition",
    "find_composition_execution_policy",
]
