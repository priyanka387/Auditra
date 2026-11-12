"""Discovery run state machine.

Mirrors the transition-table pattern used by
``app.modules.model_inventory.domain.lifecycle``: explicit allowed
transitions, no dynamic inference. The spec fixes the transitions as
PENDING -> RUNNING, then RUNNING -> COMPLETED | FAILED | PARTIAL.
"""

from app.modules.asset_discovery.enums import DiscoveryRunStatus

RUN_INITIAL_STATUS = DiscoveryRunStatus.PENDING

RUN_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    DiscoveryRunStatus.PENDING.value: {DiscoveryRunStatus.RUNNING.value},
    DiscoveryRunStatus.RUNNING.value: {
        DiscoveryRunStatus.COMPLETED.value,
        DiscoveryRunStatus.FAILED.value,
        DiscoveryRunStatus.PARTIAL.value,
    },
    DiscoveryRunStatus.COMPLETED.value: set(),
    DiscoveryRunStatus.FAILED.value: set(),
    DiscoveryRunStatus.PARTIAL.value: set(),
}


def can_run_transition(current: str, target: str) -> bool:
    if current not in RUN_ALLOWED_TRANSITIONS or target not in RUN_ALLOWED_TRANSITIONS:
        return False
    if current == target:
        return False
    return target in RUN_ALLOWED_TRANSITIONS[current]
