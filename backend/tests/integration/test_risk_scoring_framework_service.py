"""Integration tests: RF-03 ScoringFrameworkService — config validation, tenancy, audit."""

from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.core.config import settings
from app.modules.audit.models import AuditEvent
from app.modules.risk_scoring.errors import (
    DuplicateScoringFrameworkError,
    InvalidScoringFrameworkError,
    InvalidScoringFrameworkQueryError,
    ScoringFrameworkNotFoundError,
    UnsupportedScoringMethodError,
)
from app.modules.risk_scoring.schemas import (
    ScoringFrameworkCreate,
    ScoringFrameworkFilter,
    ScoringFrameworkUpdate,
)
from app.modules.risk_scoring.service import ScoringFrameworkService

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()

LEVEL_NAMES = ["L1", "L2", "L3", "L4", "L5"]
# (severity, min, max, priority) — valid 1..25 coverage for a 5x5 grid
VALID_BANDS = [
    ("low", 1, 4, 4),
    ("medium", 5, 9, 3),
    ("high", 10, 16, 2),
    ("critical", 17, 25, 1),
]


def _service(db, tenant_id: UUID | None = None) -> ScoringFrameworkService:
    return ScoringFrameworkService(
        db,
        tenant_id=tenant_id or TENANT,
        request_id="req-scoring-01",
        actor="user-7",
    )


def _levels() -> list[dict]:
    return [{"value": value, "name": name} for value, name in enumerate(LEVEL_NAMES, start=1)]


def _thresholds(bands: list[tuple[str, int, int, int]] | None = None) -> list[dict]:
    return [
        {
            "severity": severity,
            "minimum_score": minimum,
            "maximum_score": maximum,
            "priority": priority,
        }
        for severity, minimum, maximum, priority in (bands or VALID_BANDS)
    ]


def _payload(name: str = "Custom 5x5", **overrides) -> ScoringFrameworkCreate:
    payload = {
        "name": name,
        "likelihood_levels": _levels(),
        "impact_levels": _levels(),
        "severity_thresholds": _thresholds(),
    }
    payload.update(overrides)
    return ScoringFrameworkCreate(**payload)


def _two_by_two_payload(name: str = "Custom 2x2") -> ScoringFrameworkCreate:
    """Valid 2x2 config: score range 1..4 → low 1-2, high 3-4."""
    return ScoringFrameworkCreate(
        name=name,
        likelihood_levels=[
            {"value": 1, "name": "Unlikely"},
            {"value": 2, "name": "Likely"},
        ],
        impact_levels=[
            {"value": 1, "name": "Minor"},
            {"value": 2, "name": "Major"},
        ],
        severity_thresholds=[
            {"severity": "low", "minimum_score": 1, "maximum_score": 2, "priority": 2},
            {"severity": "high", "minimum_score": 3, "maximum_score": 4, "priority": 1},
        ],
    )


def _events(db, resource_id) -> list[AuditEvent]:
    stmt = (
        sa.select(AuditEvent)
        .where(
            AuditEvent.resource_type == "scoring_framework",
            AuditEvent.resource_id == str(resource_id),
        )
        .order_by(AuditEvent.sequence_no)
    )
    return list(db.scalars(stmt))


# --- create -----------------------------------------------------------------


def test_create_framework_persists_with_levels_and_thresholds(db):
    created = _service(db).create(_payload())
    framework_id = created.id
    db.expunge_all()

    framework, likelihoods, impacts, thresholds = _service(db).get_detail(framework_id)
    assert framework.name == "Custom 5x5"
    assert framework.version == 1
    assert framework.method == "likelihood_impact"
    assert framework.is_system is False
    assert framework.is_active is True
    assert [level.value for level in likelihoods] == [1, 2, 3, 4, 5]
    assert [level.display_order for level in likelihoods] == [1, 2, 3, 4, 5]
    assert [level.value for level in impacts] == [1, 2, 3, 4, 5]
    assert [(t.severity, t.minimum_score, t.maximum_score) for t in thresholds] == [
        ("low", 1, 4),
        ("medium", 5, 9),
        ("high", 10, 16),
        ("critical", 17, 25),
    ]

    events = _events(db, framework_id)
    assert len(events) == 1
    assert events[0].event_type == "scoring_framework.created"
    assert events[0].after_state["name"] == "Custom 5x5"


def test_create_duplicate_name_version_rejected(db):
    _service(db).create(_payload())
    with pytest.raises(DuplicateScoringFrameworkError) as exc:
        _service(db).create(_payload())
    assert exc.value.http_status == 409
    assert exc.value.code == "SCORING_FRAMEWORK_DUPLICATE"


def test_create_with_overlapping_thresholds_rejected(db):
    # low 1-6 overlaps medium 5-9
    payload = _payload(
        severity_thresholds=_thresholds(
            [("low", 1, 6, 4), ("medium", 5, 9, 3), ("high", 10, 16, 2), ("critical", 17, 25, 1)]
        )
    )
    with pytest.raises(InvalidScoringFrameworkError) as exc:
        _service(db).create(payload)
    assert exc.value.http_status == 400
    assert exc.value.code == "SCORING_FRAMEWORK_INVALID"


def test_create_with_gap_in_thresholds_rejected(db):
    # medium starts at 6, leaving 5 uncovered
    payload = _payload(
        severity_thresholds=_thresholds(
            [("low", 1, 4, 4), ("medium", 6, 9, 3), ("high", 10, 16, 2), ("critical", 17, 25, 1)]
        )
    )
    with pytest.raises(InvalidScoringFrameworkError):
        _service(db).create(payload)


def test_create_with_thresholds_not_covering_score_range_rejected(db):
    # critical stops at 20, but a 5x5 grid can score 25
    payload = _payload(
        severity_thresholds=_thresholds(
            [("low", 1, 4, 4), ("medium", 5, 9, 3), ("high", 10, 16, 2), ("critical", 17, 20, 1)]
        )
    )
    with pytest.raises(InvalidScoringFrameworkError):
        _service(db).create(payload)


@pytest.mark.parametrize(
    "bands",
    [
        # duplicate severity: two low bands
        [("low", 1, 4, 4), ("low", 5, 9, 3), ("high", 10, 16, 2), ("critical", 17, 25, 1)],
        # duplicate priority: medium carries low's priority
        [("low", 1, 4, 4), ("medium", 5, 9, 4), ("high", 10, 16, 2), ("critical", 17, 25, 1)],
    ],
    ids=["duplicate-severity", "duplicate-priority"],
)
def test_create_with_duplicate_severity_or_priority_rejected(db, bands):
    with pytest.raises(InvalidScoringFrameworkError):
        _service(db).create(_payload(severity_thresholds=_thresholds(bands)))


def test_create_with_unsupported_method_rejected(db):
    # the schema enum never admits an unknown method…
    with pytest.raises(ValidationError):
        _payload(method="weighted")
    # …and the service validates defensively for programmatic callers
    with pytest.raises(UnsupportedScoringMethodError) as exc:
        ScoringFrameworkService.validate_config(
            "weighted", [1, 2, 3, 4, 5], [1, 2, 3, 4, 5], _payload().severity_thresholds
        )
    assert exc.value.http_status == 400


def test_get_framework_from_other_tenant_returns_404(db):
    created = _service(db).create(_two_by_two_payload())
    with pytest.raises(ScoringFrameworkNotFoundError) as exc:
        _service(db, tenant_id=OTHER_TENANT).get(created.id)
    assert exc.value.http_status == 404


# --- update -----------------------------------------------------------------


def test_update_framework_changes_name_and_is_active(db):
    created = _service(db).create(_two_by_two_payload())
    framework_id = created.id
    updated = _service(db).update(
        framework_id, ScoringFrameworkUpdate(name="  Renamed Matrix  ", is_active=False)
    )
    assert updated.name == "Renamed Matrix"
    assert updated.is_active is False
    db.expunge_all()

    fetched = _service(db).get(framework_id)
    assert fetched.name == "Renamed Matrix"
    assert fetched.is_active is False

    events = _events(db, framework_id)
    assert len(events) == 2
    update_event = events[-1]
    assert update_event.event_type == "scoring_framework.updated"
    assert sorted(update_event.changed_fields) == ["is_active", "name"]
    assert update_event.before_state["name"] == "Custom 2x2"
    assert update_event.after_state["name"] == "Renamed Matrix"


def test_update_framework_cannot_change_version_method_or_levels(db):
    created = _service(db).create(_two_by_two_payload())
    framework_id = created.id
    # schema rejects version/method/levels in the payload (Ruling 4); the
    # service must leave the stored configuration untouched either way
    _service(db).update(framework_id, ScoringFrameworkUpdate(name="Still 2x2"))
    db.expunge_all()

    framework, likelihoods, impacts, thresholds = _service(db).get_detail(framework_id)
    assert framework.version == 1
    assert framework.method == "likelihood_impact"
    assert [level.value for level in likelihoods] == [1, 2]
    assert [level.value for level in impacts] == [1, 2]
    assert [(t.minimum_score, t.maximum_score) for t in thresholds] == [(1, 2), (3, 4)]

    with pytest.raises(ValidationError):
        ScoringFrameworkUpdate(version=2)
    with pytest.raises(ValidationError):
        ScoringFrameworkUpdate(method="weighted")
    with pytest.raises(ValidationError):
        ScoringFrameworkUpdate(likelihood_levels=[])


# --- list -------------------------------------------------------------------


def test_list_frameworks_search_and_pagination(db):
    _service(db).create(_payload(name="Alpha Matrix"))
    _service(db).create(_two_by_two_payload(name="Beta Matrix"))

    items, total = _service(db).list(ScoringFrameworkFilter(search="Alpha"))
    assert total == 1
    assert [item.name for item in items] == ["Alpha Matrix"]

    page1, full_total = _service(db).list(ScoringFrameworkFilter(page_size=2))
    page2, _ = _service(db).list(ScoringFrameworkFilter(page=2, page_size=2))
    assert full_total == 2
    assert len(page1) == 2
    assert len(page2) == 0
    names = [item.name for item in page1]
    assert sorted(names) == ["Alpha Matrix", "Beta Matrix"]


def test_list_frameworks_invalid_sort_returns_400(db):
    _service(db).create(_two_by_two_payload())
    # bypass the schema Literal to prove the repository whitelist too
    filters = ScoringFrameworkFilter.model_construct(sort_by="version")
    with pytest.raises(InvalidScoringFrameworkQueryError) as exc:
        _service(db).list(filters)
    assert exc.value.http_status == 400
