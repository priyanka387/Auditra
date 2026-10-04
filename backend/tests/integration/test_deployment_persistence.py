import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.modules.model_inventory.domain.errors import (
    DuplicateDeploymentError,
    DuplicatePrimaryEndpointError,
)
from app.modules.model_inventory.models import DeploymentEndpoint, ModelDeployment, ModelVersion
from app.modules.model_inventory.repositories.deployment_endpoint_repository import (
    commit as commit_endpoint,
)
from app.modules.model_inventory.repositories.deployment_endpoint_repository import (
    create_endpoint,
    get_endpoint,
    list_endpoints,
)
from app.modules.model_inventory.repositories.deployment_repository import (
    DeploymentFilters,
    create_deployment,
    get_deployment,
    query_deployments,
)
from app.modules.model_inventory.repositories.deployment_repository import (
    commit as commit_deployment,
)


def _deployment_fields(parent_version, **overrides):
    fields = {
        "id": uuid.uuid4(),
        "tenant_id": settings.default_tenant_id,
        "model_version_id": parent_version.id,
        "name": "fraud-detection-prod",
        "environment": "production",
        "deployment_kind": "online_inference",
        "status": "planned",
        "status_source": "manual",
        "target_type": "kubernetes",
        "target_name": "prod-cluster",
        "runtime": "vllm",
        "source": "manual",
        "configuration": {"tensor_parallel_size": 2},
        "metadata_": {"deployment_ticket": "DEP-1209"},
    }
    fields.update(overrides)
    return fields


def _endpoint_fields(deployment, **overrides):
    fields = {
        "id": uuid.uuid4(),
        "tenant_id": settings.default_tenant_id,
        "deployment_id": deployment.id,
        "name": "primary",
        "endpoint_type": "inference",
        "protocol": "https",
        "url": "https://fraud.example.com/v1/infer",
        "auth_type": "external_secret",
        "auth_reference": "secret://prod/fraud",
        "is_primary": True,
        "status": "active",
        "health_status": "unknown",
    }
    fields.update(overrides)
    return fields


def test_round_trip_and_jsonb_persistence(db, parent_version):
    deployment = create_deployment(db, **_deployment_fields(parent_version))
    commit_deployment(db)

    fetched = get_deployment(db, settings.default_tenant_id, deployment.id)
    assert fetched is not None
    assert fetched.configuration == {"tensor_parallel_size": 2}
    assert fetched.metadata_ == {"deployment_ticket": "DEP-1209"}
    assert fetched.created_at.tzinfo is not None
    assert fetched.updated_at.tzinfo is not None
    assert fetched.record_version == 1
    assert fetched.status == "planned"

    endpoint = create_endpoint(db, **_endpoint_fields(fetched))
    commit_endpoint(db)
    fetched_endpoint = get_endpoint(db, settings.default_tenant_id, endpoint.id)
    assert fetched_endpoint.url == "https://fraud.example.com/v1/infer"
    assert fetched_endpoint.is_primary is True
    assert fetched_endpoint.metadata_ == {}


def test_unique_identity_constraint_maps_to_domain_error(db, parent_version):
    create_deployment(db, **_deployment_fields(parent_version))
    commit_deployment(db)
    create_deployment(db, **_deployment_fields(parent_version, id=uuid.uuid4()))
    with pytest.raises(DuplicateDeploymentError):
        commit_deployment(db)


def test_null_target_name_still_enforced_and_distinguished_by_target(db, parent_version):
    create_deployment(
        db,
        **_deployment_fields(parent_version, id=uuid.uuid4(), target_name=None, namespace=None),
    )
    commit_deployment(db)
    with pytest.raises(DuplicateDeploymentError):
        create_deployment(
            db,
            **_deployment_fields(
                parent_version, id=uuid.uuid4(), target_name=None, namespace=None
            ),
        )
        commit_deployment(db)

    create_deployment(
        db,
        **_deployment_fields(parent_version, id=uuid.uuid4(), target_name="other-cluster"),
    )
    commit_deployment(db)
    _, total = query_deployments(
        db,
        settings.default_tenant_id,
        DeploymentFilters(),
        page=1,
        page_size=20,
        sort_by="created_at",
        sort_order="desc",
        include_archived=False,
    )
    assert total == 2


def test_version_delete_blocked_by_deployment_fk(db, parent_version):
    deployment = create_deployment(db, **_deployment_fields(parent_version))
    commit_deployment(db)

    with pytest.raises(IntegrityError):
        db.execute(sa.delete(ModelVersion).where(ModelVersion.id == parent_version.id))
        db.commit()
    db.rollback()
    assert get_deployment(db, settings.default_tenant_id, deployment.id) is not None


def test_partial_unique_primary_endpoint(db, parent_version):
    deployment = create_deployment(db, **_deployment_fields(parent_version))
    commit_deployment(db)
    create_endpoint(db, **_endpoint_fields(deployment))
    commit_endpoint(db)

    create_endpoint(
        db, **_endpoint_fields(deployment, id=uuid.uuid4(), name="primary-2")
    )
    with pytest.raises(DuplicatePrimaryEndpointError):
        commit_endpoint(db)


def test_archived_primary_frees_slot(db, parent_version):
    deployment = create_deployment(db, **_deployment_fields(parent_version))
    commit_deployment(db)
    first = create_endpoint(db, **_endpoint_fields(deployment))
    commit_endpoint(db)

    from datetime import UTC, datetime

    first.archived_at = datetime.now(UTC)
    commit_endpoint(db)

    second = create_endpoint(
        db, **_endpoint_fields(deployment, id=uuid.uuid4(), name="primary-2")
    )
    commit_endpoint(db)

    active = list_endpoints(db, settings.default_tenant_id, deployment.id)
    assert [ep.id for ep in active] == [second.id]
    with_archived = list_endpoints(
        db, settings.default_tenant_id, deployment.id, include_archived=True
    )
    assert len(with_archived) == 2


def test_query_filters_search_and_archive_exclusion(db, parent_version):
    version = parent_version
    production = create_deployment(
        db,
        **_deployment_fields(
            version, name="prod-us-east", environment="production", status="active",
            target_name="prod-cluster", region="us-east-1",
        ),
    )
    staging = create_deployment(
        db,
        **_deployment_fields(
            version,
            id=uuid.uuid4(),
            name="staging-west",
            environment="staging",
            status="deploying",
            target_type="vm",
            target_name="stage-vm",
            runtime="triton",
            namespace="ai-staging",
        ),
    )
    archived = create_deployment(
        db,
        **_deployment_fields(
            version, id=uuid.uuid4(), name="old-edge", target_name="edge-01"
        ),
    )
    commit_deployment(db)

    archived.archived_at = sa.func.now()
    archived.status = "deprecated"
    commit_deployment(db)

    def _query(**filters):
        return query_deployments(
            db,
            settings.default_tenant_id,
            DeploymentFilters(**filters),
            page=1,
            page_size=20,
            sort_by="created_at",
            sort_order="desc",
            include_archived=False,
        )

    assert _query()[1] == 2
    assert {d.id for d in _query()[0]} == {production.id, staging.id}

    by_env, total = _query(environment="production")
    assert total == 1 and by_env[0].id == production.id

    by_status, total = _query(status="deploying")
    assert total == 1 and by_status[0].id == staging.id

    by_kind_and_target, total = _query(deployment_kind="online_inference", target_type="vm")
    assert total == 1 and by_kind_and_target[0].id == staging.id

    by_runtime, total = _query(runtime="triton")
    assert total == 1 and by_runtime[0].id == staging.id

    by_version, total = _query(model_version_id=version.id)
    assert total == 2 and len(by_version) == 2

    by_model, total = _query(model_id=version.model_id)
    assert total == 2 and {d.id for d in by_model} == {production.id, staging.id}

    search_cluster, total = _query(search="prod-cluster")
    assert total == 1 and search_cluster[0].id == production.id

    search_namespace, total = _query(search="ai-staging")
    assert total == 1 and search_namespace[0].id == staging.id

    combined, total = _query(environment="production", status="active")
    assert total == 1 and combined[0].id == production.id

    with_archived, total = query_deployments(
        db,
        settings.default_tenant_id,
        DeploymentFilters(),
        page=1,
        page_size=20,
        sort_by="created_at",
        sort_order="desc",
        include_archived=True,
    )
    assert total == 3
    assert any(d.id == archived.id for d in with_archived)

    from app.modules.model_inventory.domain.errors import InvalidSortFieldError

    with pytest.raises(InvalidSortFieldError):
        query_deployments(
            db,
            settings.default_tenant_id,
            DeploymentFilters(),
            page=1,
            page_size=20,
            sort_by="name; DROP TABLE models",
            sort_order="desc",
            include_archived=False,
        )


def test_cross_tenant_isolation(db, parent_version):
    foreign_id = uuid.uuid4()
    create_deployment(
        db,
        **_deployment_fields(parent_version, id=foreign_id, tenant_id=uuid.uuid4()),
    )
    commit_deployment(db)

    assert get_deployment(db, settings.default_tenant_id, foreign_id) is None
    items, total = query_deployments(
        db,
        settings.default_tenant_id,
        DeploymentFilters(),
        page=1,
        page_size=20,
        sort_by="created_at",
        sort_order="desc",
        include_archived=True,
    )
    assert total == 0
    assert all(isinstance(item, ModelDeployment) for item in items)

    deployment = create_deployment(
        db, **_deployment_fields(parent_version, id=uuid.uuid4())
    )
    commit_deployment(db)
    foreign_endpoint = create_endpoint(
        db,
        **_endpoint_fields(deployment, id=uuid.uuid4(), tenant_id=uuid.uuid4()),
    )
    commit_endpoint(db)
    assert get_endpoint(db, settings.default_tenant_id, foreign_endpoint.id) is None

    own_endpoint = create_endpoint(
        db, **_endpoint_fields(deployment, id=uuid.uuid4(), name="own", is_primary=False)
    )
    commit_endpoint(db)
    visible = list_endpoints(db, settings.default_tenant_id, deployment.id)
    assert [ep.id for ep in visible] == [own_endpoint.id]
    assert isinstance(foreign_endpoint, DeploymentEndpoint)
