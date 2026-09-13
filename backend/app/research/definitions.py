from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class CompositionPolicyDefinition:
    policy_code: str
    policy_version: str
    display_name: str
    description: str
    minimum_components: int
    maximum_components: int
    aggregation_rule: Literal["AT_LEAST_N_MATCHED"]
    insufficient_history_rule: Literal["COULD_CHANGE_THRESHOLD"]
    component_order_policy: Literal["CANONICAL_STRATEGY_PARAMETERS"]


CONSENSUS_N_OF_M_V1 = CompositionPolicyDefinition(
    policy_code="CONSENSUS_N_OF_M",
    policy_version="1",
    display_name="Consensus N of M",
    description=(
        "Matches when at least N independently evaluated immutable strategies match. "
        "Insufficient component history remains explicit when it could change the threshold outcome."
    ),
    minimum_components=2,
    maximum_components=10,
    aggregation_rule="AT_LEAST_N_MATCHED",
    insufficient_history_rule="COULD_CHANGE_THRESHOLD",
    component_order_policy="CANONICAL_STRATEGY_PARAMETERS",
)


__all__ = ["CONSENSUS_N_OF_M_V1", "CompositionPolicyDefinition"]
