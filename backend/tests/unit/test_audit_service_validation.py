import pytest

from app.modules.audit import repository, service
from app.modules.audit.errors import InvalidAuditEventError
from app.modules.audit.service import ActorContext, AuditService, actor_context


def test_record_event_rejects_bad_event_type():
    with pytest.raises(InvalidAuditEventError):
        service.validate_event_type("MODEL.CREATED")
    with pytest.raises(InvalidAuditEventError):
        service.validate_event_type("nope")
    with pytest.raises(InvalidAuditEventError):
        service.validate_event_type("")
    service.validate_event_type("model.created")


def test_record_event_rejects_empty_resource():
    with pytest.raises(InvalidAuditEventError):
        service.validate_resource("", "id-1")
    with pytest.raises(InvalidAuditEventError):
        service.validate_resource("model", "")
    service.validate_resource("model", "id-1")


def test_actor_context_maps_user_and_system():
    assert actor_context("user-1") == ActorContext(type="user", id="user-1")
    assert actor_context(None) == ActorContext(type="system", id=None)


def test_actor_context_supports_explicit_actor_types():
    assert actor_context("discovery-01", "connector") == ActorContext(
        type="connector", id="discovery-01"
    )
    assert actor_context("deployer", "service") == ActorContext(type="service", id="deployer")


def test_audit_source_must_be_non_empty():
    from uuid import uuid4

    with pytest.raises(InvalidAuditEventError):
        AuditService(None, uuid4(), source="")


def test_repository_module_is_append_only():
    forbidden = {"update", "delete", "remove", "merge", "truncate"}
    repo_names = {name for name in dir(repository) if not name.startswith("_")}
    assert not repo_names & forbidden
    service_names = {name for name in dir(AuditService) if not name.startswith("_")}
    assert not service_names & forbidden
