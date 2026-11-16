"""Unit tests: RF-01 risk domain models and enums."""

import sqlalchemy as sa

from app.modules.risk.enums import RiskSource, RiskStatus
from app.modules.risk.models import Risk, RiskCategory


def test_risk_status_values():
    assert {status.value for status in RiskStatus} == {"open", "in_review", "accepted"}


def test_risk_source_values():
    assert {source.value for source in RiskSource} == {
        "manual",
        "asset_discovery",
        "assessment",
        "incident",
        "audit",
        "policy_review",
        "vendor_review",
        "other",
    }


def test_risk_table_shape():
    assert Risk.__tablename__ == "risks"
    columns = {column.name for column in Risk.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "title",
        "description",
        "category_id",
        "source",
        "asset_id",
        "owner",
        "team",
        "status",
        "created_by",
        "updated_by",
        "archived_at",
        "created_at",
        "updated_at",
    } <= columns

    assert RiskCategory.__tablename__ == "risk_categories"
    category_columns = {column.name for column in RiskCategory.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "name",
        "description",
        "slug",
        "is_system",
        "is_active",
        "created_at",
        "updated_at",
    } <= category_columns


def test_risk_check_constraints():
    def sql_of(constraint: sa.CheckConstraint) -> str:
        return (
            constraint.sqltext if isinstance(constraint.sqltext, str) else constraint.sqltext.text
        )

    checks = {
        constraint.name: sql_of(constraint)
        for constraint in Risk.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    status_sql = checks["ck_risks_status"]
    for value in ("open", "in_review", "accepted"):
        assert value in status_sql
    assert "mitigated" not in status_sql

    source_sql = checks["ck_risks_source"]
    for value in (
        "manual",
        "asset_discovery",
        "assessment",
        "incident",
        "audit",
        "policy_review",
        "vendor_review",
        "other",
    ):
        assert value in source_sql


def test_risk_relationships_restrict_delete():
    category_fk = next(
        fk for fk in Risk.__table__.foreign_keys if fk.target_fullname == "risk_categories.id"
    )
    asset_fk = next(
        fk for fk in Risk.__table__.foreign_keys if fk.target_fullname == "discovered_assets.id"
    )
    assert category_fk.ondelete == "RESTRICT"
    assert asset_fk.ondelete == "RESTRICT"
