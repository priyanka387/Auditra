"""Unit tests: Asset Lifecycle policy (Sub-feature 8).

The lifecycle rules are deterministic pure functions of (current status,
missed completed runs, policy): no database, no clock, no source content.
Persistence and discovery-run integration are covered by
``tests/integration/test_asset_lifecycle.py``.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.asset_discovery.asset_lifecycle import (
    CompletedRunIndex,
    LifecyclePolicy,
    decide_lifecycle_status,
    is_evaluable_run_status,
)
from app.modules.asset_discovery.enums import (
    DiscoveredAssetStatus,
    DiscoveryRunStatus,
)
from app.modules.asset_discovery.errors import InvalidAssetLifecyclePolicyError

ACTIVE = DiscoveredAssetStatus.ACTIVE.value
STALE = DiscoveredAssetStatus.STALE.value
DISAPPEARED = DiscoveredAssetStatus.DISAPPEARED.value
IGNORED = DiscoveredAssetStatus.IGNORED.value

# defaults: one missed completed run -> STALE, three -> DISAPPEARED
POLICY = LifecyclePolicy()

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


# --- decide_lifecycle_status ------------------------------------------------


def test_observed_or_never_missed_assets_resolve_to_active():
    # zero misses is the "observed in the latest applicable run" case and
    # also the reactivation case for a previously stale/disappeared asset
    for current in (ACTIVE, STALE, DISAPPEARED):
        assert decide_lifecycle_status(current, 0, POLICY) == ACTIVE


def test_single_missed_completed_run_marks_asset_stale():
    assert decide_lifecycle_status(ACTIVE, 1, POLICY) == STALE
    assert decide_lifecycle_status(STALE, 1, POLICY) == STALE


def test_disappearance_threshold_is_deterministic_at_the_boundary():
    # one below the default threshold stays STALE
    assert decide_lifecycle_status(ACTIVE, 2, POLICY) == STALE
    assert decide_lifecycle_status(STALE, 2, POLICY) == STALE
    # exactly at / above the threshold the asset is DISAPPEARED
    assert decide_lifecycle_status(ACTIVE, 3, POLICY) == DISAPPEARED
    assert decide_lifecycle_status(STALE, 3, POLICY) == DISAPPEARED
    assert decide_lifecycle_status(ACTIVE, 99, POLICY) == DISAPPEARED


def test_ignored_assets_never_transition():
    for missed in (0, 1, 2, 3, 100):
        assert decide_lifecycle_status(IGNORED, missed, POLICY) == IGNORED


def test_custom_policy_thresholds_decide_deterministically():
    policy = LifecyclePolicy(stale_after_missed_runs=2, disappeared_after_missed_runs=4)
    assert decide_lifecycle_status(ACTIVE, 1, policy) == ACTIVE
    assert decide_lifecycle_status(ACTIVE, 2, policy) == STALE
    assert decide_lifecycle_status(ACTIVE, 3, policy) == STALE
    assert decide_lifecycle_status(ACTIVE, 4, policy) == DISAPPEARED


# --- LifecyclePolicy ---------------------------------------------------------


def test_policy_rejects_invalid_thresholds():
    with pytest.raises(InvalidAssetLifecyclePolicyError):
        LifecyclePolicy(stale_after_missed_runs=0)
    with pytest.raises(InvalidAssetLifecyclePolicyError):
        LifecyclePolicy(stale_after_missed_runs=1, disappeared_after_missed_runs=0)
    with pytest.raises(InvalidAssetLifecyclePolicyError):
        # stale must be reachable: disappearance requires a higher threshold
        LifecyclePolicy(stale_after_missed_runs=3, disappeared_after_missed_runs=3)
    with pytest.raises(InvalidAssetLifecyclePolicyError):
        LifecyclePolicy(stale_after_missed_runs=3, disappeared_after_missed_runs=2)


def test_policy_is_built_from_settings_source():
    source = SimpleNamespace(
        discovery_lifecycle_stale_after_missed_runs=2,
        discovery_lifecycle_disappeared_after_missed_runs=5,
    )
    policy = LifecyclePolicy.from_settings(source)
    assert policy.stale_after_missed_runs == 2
    assert policy.disappeared_after_missed_runs == 5


def test_default_policy_matches_settings():
    policy = LifecyclePolicy.from_settings()
    assert policy.stale_after_missed_runs == 1
    assert policy.disappeared_after_missed_runs == 3


# --- run eligibility ---------------------------------------------------------


def test_only_successful_terminal_runs_are_evaluable():
    assert is_evaluable_run_status(DiscoveryRunStatus.COMPLETED.value) is True
    assert is_evaluable_run_status(DiscoveryRunStatus.PARTIAL.value) is True


@pytest.mark.parametrize(
    "status",
    [
        DiscoveryRunStatus.PENDING.value,
        DiscoveryRunStatus.RUNNING.value,
        DiscoveryRunStatus.FAILED.value,
        "UNKNOWN",
        "",
    ],
)
def test_non_evaluable_run_statuses_are_rejected(status):
    assert is_evaluable_run_status(status) is False


# --- CompletedRunIndex -------------------------------------------------------


def test_index_counts_completed_runs_finished_after_last_seen():
    observing = uuid4()
    later = uuid4()
    # observing run completed at T0+10m, a later run at T0+20m; the asset
    # was observed at T0+5m (i.e. while the observing run was still running)
    index = CompletedRunIndex(
        [
            (T0 + timedelta(minutes=10), observing),
            (T0 + timedelta(minutes=20), later),
        ]
    )
    # only the *later* run is a miss: the observing run never counts
    assert index.missed_since(T0 + timedelta(minutes=5), observing) == 1
    assert index.missed_since(T0 + timedelta(minutes=5), None) == 2
    assert index.missed_since(T0 + timedelta(minutes=5), uuid4()) == 2


def test_index_ignores_runs_completed_before_last_seen():
    old = uuid4()
    observing = uuid4()
    index = CompletedRunIndex(
        [
            (T0, old),
            (T0 + timedelta(hours=1), observing),
        ]
    )
    # last seen after the first run completed: only the observing run is
    # in range and it is excluded
    assert index.missed_since(T0 + timedelta(minutes=30), observing) == 0


def test_index_treats_completion_exactly_at_last_seen_as_not_missed():
    observing = uuid4()
    index = CompletedRunIndex([(T0, observing)])
    assert index.missed_since(T0, observing) == 0
    assert index.missed_since(T0, None) == 0


def test_index_is_order_independent():
    first, second = uuid4(), uuid4()
    runs = [(T0, first), (T0 + timedelta(hours=1), second)]
    forward = CompletedRunIndex(runs)
    backward = CompletedRunIndex(list(reversed(runs)))
    moment = T0 - timedelta(minutes=1)
    assert forward.missed_since(moment, None) == backward.missed_since(moment, None) == 2
    assert forward.missed_since(moment, second) == backward.missed_since(moment, second) == 1


def test_index_handles_no_completed_runs():
    index = CompletedRunIndex([])
    assert index.missed_since(T0, None) == 0
    assert index.missed_since(T0, uuid4()) == 0
