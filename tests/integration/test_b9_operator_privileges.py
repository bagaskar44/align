from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from align_api.config import get_settings

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_RUN_INTEGRATION") != "true",
    reason="requires the isolated PostgreSQL integration profile",
)


def test_operator_has_lms_registration_scope_without_import_write_access() -> None:
    url = get_settings().operator_database_url
    assert url is not None
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as db:
            for table in ("users", "courses", "lti_platforms", "lti_identity_bindings"):
                assert db.execute(
                    text("SELECT has_table_privilege(current_user, :table, 'SELECT')"),
                    {"table": table},
                ).scalar_one() is True
            assert db.execute(
                text("SELECT has_table_privilege(current_user, 'lti_platforms', 'INSERT')")
            ).scalar_one() is True
            assert db.execute(
                text("SELECT has_table_privilege(current_user, 'audit_events', 'INSERT')")
            ).scalar_one() is True
            assert db.execute(
                text(
                    "SELECT has_column_privilege(current_user, 'audit_events', 'created_at', 'SELECT')"
                )
            ).scalar_one() is True
            assert db.execute(
                text("SELECT has_table_privilege(current_user, 'audit_events', 'SELECT')")
            ).scalar_one() is False
            assert db.execute(
                text("SELECT has_table_privilege(current_user, 'lms_import_events', 'INSERT')")
            ).scalar_one() is False
            assert db.execute(
                text("SELECT has_table_privilege(current_user, 'users', 'UPDATE')")
            ).scalar_one() is False
    finally:
        engine.dispose()
