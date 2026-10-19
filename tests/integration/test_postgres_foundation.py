from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from align_api.config import get_settings

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_RUN_INTEGRATION") != "true",
    reason="set ALIGN_RUN_INTEGRATION=true inside the Compose network to run PostgreSQL checks",
)


def test_postgres_has_pgvector_08_and_role_separation() -> None:
    settings = get_settings()
    api_engine = create_engine(settings.database_url)
    worker_engine = create_engine(settings.worker_database_url)
    try:
        with api_engine.connect() as connection:
            assert connection.execute(text("SELECT current_user")).scalar_one() == "align_api"
            extension = connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            ).scalar_one()
            major, minor, *_ = (int(part) for part in extension.split("."))
            assert major > 0 or minor >= 8
            assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() >= 0
        with worker_engine.connect() as connection:
            assert connection.execute(text("SELECT current_user")).scalar_one() == "align_worker"
            assert connection.execute(
                text("SELECT has_table_privilege(current_user, 'sessions', 'SELECT')")
            ).scalar_one() is False
            assert connection.execute(
                text("SELECT has_table_privilege(current_user, 'courses', 'SELECT')")
            ).scalar_one() is False
            assert connection.execute(
                text("SELECT has_table_privilege(current_user, 'enrollments', 'SELECT')")
            ).scalar_one() is False
            assert connection.execute(
                text("SELECT has_table_privilege(current_user, 'audit_events', 'DELETE')")
            ).scalar_one() is False
            assert connection.execute(
                text("SELECT has_table_privilege(current_user, 'job_stages', 'UPDATE')")
            ).scalar_one() is True
    finally:
        api_engine.dispose()
        worker_engine.dispose()


def test_b1_schema_privileges_and_approved_context_immutability() -> None:
    settings = get_settings()
    api_engine = create_engine(settings.database_url)
    worker_engine = create_engine(settings.worker_database_url)
    api_connection = api_engine.connect()
    transaction = api_connection.begin()
    course_id = uuid4()
    context_id = uuid4()
    try:
        for table in (
            "context_versions",
            "context_conflicts",
            "learning_outcomes",
            "file_assets",
            "normative_file_links",
            "parse_artifacts",
            "parser_checkpoints",
            "processing_time_reservations",
            "source_units",
            "evidence_items",
        ):
            assert api_connection.execute(
                text("SELECT to_regclass(:table_name) IS NOT NULL"), {"table_name": f"public.{table}"}
            ).scalar_one()
        assert api_connection.execute(
            text("SELECT has_table_privilege(current_user, 'context_versions', 'INSERT')")
        ).scalar_one()
        with worker_engine.connect() as worker_connection:
            assert worker_connection.execute(
                text("SELECT has_table_privilege(current_user, 'context_versions', 'UPDATE')")
            ).scalar_one()
            assert worker_connection.execute(
                text("SELECT has_table_privilege(current_user, 'evidence_items', 'UPDATE')")
            ).scalar_one() is False
            assert worker_connection.execute(
                text("SELECT has_table_privilege(current_user, 'file_assets', 'INSERT')")
            ).scalar_one() is False

        api_connection.execute(
            text(
                "INSERT INTO courses (id, code, title, active, revision) "
                "VALUES (:id, :code, :title, true, 1)"
            ),
            {"id": course_id, "code": f"B1-{course_id.hex[:10]}", "title": "Konteks test"},
        )
        api_connection.execute(
            text(
                "INSERT INTO context_versions (id, course_id, version_no, state, fields_json, revision) "
                "VALUES (:id, :course_id, 1, 'draft', '{}'::jsonb, 1)"
            ),
            {"id": context_id, "course_id": course_id},
        )
        api_connection.execute(text("UPDATE context_versions SET state='approved' WHERE id=:id"), {"id": context_id})
        with pytest.raises(Exception, match="APPROVED_CONTEXT_IMMUTABLE"):
            with api_connection.begin_nested():
                api_connection.execute(
                    text("UPDATE context_versions SET fields_json=CAST(:payload AS jsonb) WHERE id=:id"),
                    {"id": context_id, "payload": '{"changed":true}'},
                )
    finally:
        transaction.rollback()
        api_connection.close()
        api_engine.dispose()
        worker_engine.dispose()

