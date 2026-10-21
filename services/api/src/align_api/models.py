from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RevisionMixin(TimestampMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class User(RevisionMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("normalized_email", name="uq_users_normalized_email"),
        CheckConstraint("revision >= 1", name="ck_users_revision_positive"),
        CheckConstraint("global_role IN ('admin','member')", name="ck_users_global_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    global_role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enrollments: Mapped[list[Enrollment]] = relationship(back_populates="user")


class Session(RevisionMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        Index("ix_sessions_user_active", "user_id", "revoked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship()


class Course(RevisionMixin, Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("code", name="uq_courses_code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    approved_context_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "context_versions.id", use_alter=True, name="fk_courses_approved_context_version"
        )
    )


class Enrollment(RevisionMixin, Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("course_id", "user_id", name="uq_enrollments_course_user"),
        CheckConstraint("role IN ('lecturer','student')", name="ck_enrollments_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    user: Mapped[User] = relationship(back_populates="enrollments")
    course: Mapped[Course] = relationship()


class MethodManifest(TimestampMixin, Base):
    __tablename__ = "method_manifests"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_method_manifests_sha256"),
        CheckConstraint(
            "kind IN ('parser','parser_resource','model','retrieval','rules','prompt','routing','batching')",
            name="ck_method_manifests_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    version_label: Mapped[str] = mapped_column(String(120), nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReleaseManifest(TimestampMixin, Base):
    __tablename__ = "release_manifests"
    __table_args__ = (
        UniqueConstraint("config_hash", name="uq_release_manifests_config_hash"),
        CheckConstraint(
            "state IN ('draft','verified','active','retired')", name="ck_release_manifests_state"
        ),
        Index(
            "uq_release_single_active",
            "state",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version_label: Mapped[str] = mapped_column(String(120), nullable=False)
    parser_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("method_manifests.id"), nullable=False
    )
    model_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("method_manifests.id"), nullable=False
    )
    retrieval_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("method_manifests.id"), nullable=False
    )
    rules_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("method_manifests.id"), nullable=False
    )
    prompt_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("method_manifests.id"), nullable=False
    )
    routing_policy_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("method_manifests.id"), nullable=False
    )
    batching_policy_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("method_manifests.id"), nullable=False
    )
    grounding_registry_version: Mapped[str] = mapped_column(String(80), nullable=False)
    lint_registry_version: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    activated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("type", "input_fingerprint", name="uq_jobs_type_input_fingerprint"),
        CheckConstraint(
            "status IN ('queued','running','ready_for_review','blocked','failed')",
            name="ck_jobs_status",
        ),
        Index("ix_jobs_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    stage: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_fence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_kind: Mapped[str | None] = mapped_column(String(32))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    job_limits_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class JobStage(TimestampMixin, Base):
    __tablename__ = "job_stages"
    __table_args__ = (
        UniqueConstraint("job_id", "stage_name", "input_hash", name="uq_job_stages_fingerprint"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','blocked','failed')",
            name="ck_job_stages_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_job_stages_attempt_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    output_refs_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(120))


class OutboxEvent(TimestampMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_outbox_attempt_count"),
        Index("ix_outbox_pending", "delivered_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BudgetAccount(RevisionMixin, Base):
    __tablename__ = "budget_accounts"
    __table_args__ = (
        CheckConstraint("currency = 'USD'", name="ck_budget_accounts_currency"),
        CheckConstraint(
            "confirmed_total >= 0 AND reserve_floor >= 0 AND planned_topup >= 0",
            name="ck_budget_accounts_nonnegative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    confirmed_total: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    reserve_floor: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    planned_topup: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UsageReservation(TimestampMixin, Base):
    __tablename__ = "usage_reservations"
    __table_args__ = (
        UniqueConstraint("call_id", name="uq_usage_reservations_call"),
        CheckConstraint("amount_reserved >= 0", name="ck_usage_reservations_nonnegative"),
        CheckConstraint(
            "status IN ('held','settled','released','reconciliation_required')",
            name="ck_usage_reservations_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    budget_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budget_accounts.id"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    call_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    amount_reserved: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="held")


class UsageEntry(TimestampMixin, Base):
    __tablename__ = "usage_entries"
    __table_args__ = (
        UniqueConstraint("call_id", name="uq_usage_entries_call"),
        UniqueConstraint("logical_call_id", "attempt_no", name="uq_usage_entries_logical_attempt"),
        CheckConstraint(
            "actual_cost IS NULL OR actual_cost >= 0", name="ck_usage_entries_cost_nonnegative"
        ),
        CheckConstraint("status IN ('known','pending')", name="ck_usage_entries_status"),
        CheckConstraint("attempt_no >= 1", name="ck_usage_entries_attempt_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    logical_call_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(240))
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("method_manifests.id"), nullable=False
    )
    usage_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    rates_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    actual_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    worker_id: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    object_type: Mapped[str] = mapped_column(String(120), nullable=False)
    object_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    old_version_refs_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    new_version_refs_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class IdempotencyRecord(TimestampMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "actor_id", "method", "route", "key", name="uq_idempotency_actor_method_route_key"
        ),
        Index("ix_idempotency_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    route: Mapped[str] = mapped_column(String(240), nullable=False)
    key: Mapped[str] = mapped_column(String(36), nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Operation(TimestampMixin, Base):
    """Operational smoke work that never represents an academic analysis job."""

    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("kind", "idempotency_key", name="uq_operations_kind_idempotency"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed')", name="ck_operations_status"
        ),
        CheckConstraint("attempt_count >= 0", name="ck_operations_attempt_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ContextVersion(RevisionMixin, Base):
    __tablename__ = "context_versions"
    __table_args__ = (
        UniqueConstraint("course_id", "version_no", name="uq_context_versions_course_version"),
        CheckConstraint("version_no >= 1 AND revision >= 1", name="ck_context_versions_revision"),
        CheckConstraint("state IN ('draft','approved')", name="ck_context_versions_state"),
        Index("ix_context_versions_course_state", "course_id", "state", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    fields_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    instruction_sources_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    content_hash: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContextConflict(TimestampMixin, Base):
    __tablename__ = "context_conflicts"
    __table_args__ = (
        UniqueConstraint(
            "context_version_id", "field_path", name="uq_context_conflicts_version_field"
        ),
        CheckConstraint(
            "field_path IN ('course_code','course_title','context_description','language','number_locale')",
            name="ck_context_conflicts_field",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("context_versions.id", ondelete="CASCADE"), nullable=False
    )
    field_path: Mapped[str] = mapped_column(String(80), nullable=False)
    candidate_values_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    resolution_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    resolution_history_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LearningOutcome(TimestampMixin, Base):
    __tablename__ = "learning_outcomes"
    __table_args__ = (
        UniqueConstraint("context_version_id", "code", name="uq_learning_outcomes_version_code"),
        CheckConstraint(
            "origin IN ('manual','extracted','suggested')", name="ck_learning_outcomes_origin"
        ),
        CheckConstraint(
            "resolution_status IN ('proposed','confirmed','conflicting','not_provided')",
            name="ck_learning_outcomes_resolution",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("context_versions.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(4000), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    source_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    resolution_status: Mapped[str] = mapped_column(String(24), nullable=False)


class FileAsset(RevisionMixin, Base):
    __tablename__ = "file_assets"
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_file_assets_object_key"),
        CheckConstraint(
            "purpose IN ('context','rubric','submission')", name="ck_file_assets_purpose"
        ),
        CheckConstraint(
            "validation_state IN ('validated','rejected','quarantined')",
            name="ck_file_assets_validation_state",
        ),
        CheckConstraint("size_bytes > 0", name="ck_file_assets_positive_size"),
        CheckConstraint(
            "(purpose = 'context' AND course_id IS NOT NULL AND assignment_id IS NULL AND submission_id IS NULL) OR (purpose = 'rubric' AND assignment_id IS NOT NULL AND course_id IS NOT NULL AND submission_id IS NULL) OR (purpose = 'submission' AND submission_id IS NOT NULL AND assignment_id IS NULL AND course_id IS NOT NULL)",
            name="ck_file_assets_scope",
        ),
        UniqueConstraint("id", "submission_id", name="uq_file_assets_id_submission"),
        Index("ix_file_assets_course_purpose", "course_id", "purpose", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "submissions.id", ondelete="RESTRICT", use_alter=True, name="fk_file_assets_submission"
        )
    )
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "assignments.id", ondelete="CASCADE", use_alter=True, name="fk_file_assets_assignment"
        )
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(240), nullable=False)
    validation_state: Mapped[str] = mapped_column(String(20), nullable=False)
    preflight_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NormativeFileLink(TimestampMixin, Base):
    __tablename__ = "normative_file_links"
    __table_args__ = (
        CheckConstraint(
            "source_role IN ('kk','rpp','rubric')", name="ck_normative_file_links_role"
        ),
        CheckConstraint(
            "(context_version_id IS NOT NULL)::integer + (assignment_version_id IS NOT NULL)::integer = 1",
            name="ck_normative_file_links_one_version",
        ),
        UniqueConstraint(
            "file_asset_id",
            "context_version_id",
            "source_role",
            name="uq_normative_context_file_role",
        ),
        UniqueConstraint(
            "file_asset_id",
            "assignment_version_id",
            "source_role",
            name="uq_normative_assignment_file_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    file_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("file_assets.id", ondelete="RESTRICT"), nullable=False
    )
    context_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("context_versions.id", ondelete="RESTRICT")
    )
    assignment_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "assignment_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_normative_links_assignment_version",
        )
    )
    source_role: Mapped[str] = mapped_column(String(16), nullable=False)


class ParseArtifact(TimestampMixin, Base):
    __tablename__ = "parse_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope_hash", "fingerprint", name="uq_parse_artifacts_scoped_fingerprint"
        ),
        CheckConstraint(
            "(submission_version_id IS NOT NULL)::integer + (context_version_id IS NOT NULL)::integer + (assignment_version_id IS NOT NULL)::integer = 1",
            name="ck_parse_artifacts_one_namespace",
        ),
        CheckConstraint(
            "status IN ('queued','running','parsed','partially_parsed','unreadable','unsupported','failed','blocked')",
            name="ck_parse_artifacts_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    file_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("file_assets.id", ondelete="RESTRICT"), nullable=False
    )
    submission_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "submission_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_parse_artifacts_submission_version",
        )
    )
    context_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("context_versions.id", ondelete="RESTRICT")
    )
    assignment_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "assignment_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_parse_artifacts_assignment_version",
        )
    )
    parser_manifest_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("method_manifests.id"))
    parser_resource_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("method_manifests.id")
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    limitations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    artifact_object_key: Mapped[str | None] = mapped_column(String(240))


class ParserCheckpoint(TimestampMixin, Base):
    __tablename__ = "parser_checkpoints"
    __table_args__ = (
        UniqueConstraint("parse_artifact_id", "task_key", name="uq_parser_checkpoints_task"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','blocked','failed')",
            name="ck_parser_checkpoints_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    parse_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parse_artifacts.id", ondelete="CASCADE"), nullable=False
    )
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    namespace_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    canonical_unit_locator_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_resource_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("method_manifests.id")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    output_refs_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    covered_unit_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    limitations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admission_node: Mapped[str | None] = mapped_column(String(120))
    admission_lease: Mapped[str | None] = mapped_column(String(120))
    admission_fence: Mapped[int | None] = mapped_column(Integer)
    actual_active_ms: Mapped[int | None] = mapped_column(Integer)
    peak_memory_bytes: Mapped[int | None] = mapped_column(Integer)


class ProcessingTimeReservation(TimestampMixin, Base):
    __tablename__ = "processing_time_reservations"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "task_key", "attempt_no", name="uq_processing_time_reservation_attempt"
        ),
        CheckConstraint(
            "reserved_active_ms >= 0 AND (actual_active_ms IS NULL OR actual_active_ms >= 0)",
            name="ck_processing_time_reservation_nonnegative",
        ),
        CheckConstraint(
            "state IN ('held','settled','released')", name="ck_processing_time_reservation_state"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_active_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_active_ms: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="held")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceUnit(TimestampMixin, Base):
    __tablename__ = "source_units"
    __table_args__ = (
        UniqueConstraint(
            "parse_artifact_id", "locator_key", name="uq_source_units_artifact_locator"
        ),
        CheckConstraint(
            "parse_status IN ('parsed','partially_parsed','unreadable','unsupported','failed')",
            name="ck_source_units_parse_status",
        ),
        Index("ix_source_units_artifact_order", "parse_artifact_id", "source_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    parse_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parse_artifacts.id", ondelete="CASCADE"), nullable=False
    )
    locator_key: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_units.id", ondelete="CASCADE")
    )
    unit_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_order: Mapped[int] = mapped_column(Integer, nullable=False)
    anchor_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    parse_status: Mapped[str] = mapped_column(String(24), nullable=False)
    limitations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )


class EvidenceItem(TimestampMixin, Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        UniqueConstraint(
            "source_unit_id", "file_asset_id", "type", name="uq_evidence_items_source_type"
        ),
        CheckConstraint(
            "(submission_version_id IS NOT NULL)::integer + (context_version_id IS NOT NULL)::integer + (assignment_version_id IS NOT NULL)::integer = 1",
            name="ck_evidence_items_one_namespace",
        ),
        CheckConstraint(
            "type IN ('paragraph','table','diagram','image_text','spreadsheet_cell','spreadsheet_range','numeric_observation','lecturer_transcription')",
            name="ck_evidence_items_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_units.id", ondelete="CASCADE"), nullable=False
    )
    submission_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "submission_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_evidence_items_submission_version",
        )
    )
    context_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("context_versions.id", ondelete="RESTRICT")
    )
    assignment_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "assignment_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_evidence_items_assignment_version",
        )
    )
    file_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("file_assets.id", ondelete="RESTRICT"), nullable=False
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    anchor_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_original_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_normalized_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    parse_status: Mapped[str] = mapped_column(String(24), nullable=False)
    limitations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    parser_manifest_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("method_manifests.id"))


class Assignment(RevisionMixin, Base):
    __tablename__ = "assignments"
    __table_args__ = (
        CheckConstraint(
            "administrative_state IN ('open','closed','archived')",
            name="ck_assignments_admin_state",
        ),
        Index("ix_assignments_course_active", "course_id", "active_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assignment_versions.id", use_alter=True, name="fk_assignments_active_version")
    )
    administrative_state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")


class AssignmentVersion(RevisionMixin, Base):
    __tablename__ = "assignment_versions"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "version_no", name="uq_assignment_versions_assignment_version"
        ),
        CheckConstraint(
            "version_no >= 1 AND revision >= 1", name="ck_assignment_versions_revision"
        ),
        CheckConstraint("state IN ('draft','published')", name="ck_assignment_versions_state"),
        CheckConstraint("mode IN ('individual','group')", name="ck_assignment_versions_mode"),
        CheckConstraint(
            "late_policy IN ('accept_with_label','reject_after_due')",
            name="ck_assignment_versions_late_policy",
        ),
        CheckConstraint("due_at > opens_at", name="ck_assignment_versions_schedule"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("context_versions.id", ondelete="RESTRICT"), nullable=False
    )
    instruction_original: Mapped[str] = mapped_column(String(30000), nullable=False)
    instruction_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    instruction_summary: Mapped[str | None] = mapped_column(String(2000))
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="individual")
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    late_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="accept_with_label"
    )
    limits_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    criterion_set_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("criterion_set_versions.id", use_alter=True)
    )
    specification_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("specification_versions.id", use_alter=True)
    )
    package_approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    package_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CriterionSetVersion(RevisionMixin, Base):
    __tablename__ = "criterion_set_versions"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "version_no", name="uq_criterion_sets_assignment_version"
        ),
        CheckConstraint("version_no >= 1 AND revision >= 1", name="ck_criterion_sets_revision"),
        CheckConstraint("state IN ('draft','approved')", name="ck_criterion_sets_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False
    )
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("context_versions.id", ondelete="RESTRICT"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    instruction_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    content_hash: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Criterion(TimestampMixin, Base):
    __tablename__ = "criteria"
    __table_args__ = (
        UniqueConstraint("criterion_set_version_id", "code", name="uq_criteria_set_code"),
        UniqueConstraint("id", "criterion_set_version_id", name="uq_criteria_id_set"),
        CheckConstraint("display_order >= 0", name="ck_criteria_display_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    criterion_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("criterion_set_versions.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    descriptor: Mapped[str] = mapped_column(String(4000), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Requirement(TimestampMixin, Base):
    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint("criterion_set_version_id", "code", name="uq_requirements_set_code"),
        UniqueConstraint("id", "criterion_set_version_id", name="uq_requirements_id_set"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    criterion_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("criterion_set_versions.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    text: Mapped[str] = mapped_column(String(2000), nullable=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    obligation_key: Mapped[str] = mapped_column(String(240), nullable=False)
    normative_source_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )


class CriterionRequirementLink(Base):
    __tablename__ = "criterion_requirement_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["criterion_id", "criterion_set_version_id"],
            ["criteria.id", "criteria.criterion_set_version_id"],
            ondelete="CASCADE",
            name="fk_criterion_requirement_criterion_scope",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "criterion_set_version_id"],
            ["requirements.id", "requirements.criterion_set_version_id"],
            ondelete="CASCADE",
            name="fk_criterion_requirement_requirement_scope",
        ),
    )

    criterion_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    criterion_set_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class CriterionOutcomeLink(Base):
    __tablename__ = "criterion_outcome_links"

    criterion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("criteria.id", ondelete="CASCADE"), primary_key=True
    )
    learning_outcome_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("learning_outcomes.id", ondelete="CASCADE"), primary_key=True
    )


class NumericPolicyVersion(RevisionMixin, Base):
    __tablename__ = "numeric_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "policy_key", "version_no", name="uq_numeric_policy_version"
        ),
        CheckConstraint("version_no >= 1 AND revision >= 1", name="ck_numeric_policy_revision"),
        CheckConstraint("state IN ('draft','approved')", name="ck_numeric_policy_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False
    )
    policy_key: Mapped[str] = mapped_column(String(80), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SpecificationVersion(RevisionMixin, Base):
    __tablename__ = "specification_versions"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "version_no", name="uq_specifications_assignment_version"
        ),
        CheckConstraint("version_no >= 1 AND revision >= 1", name="ck_specifications_revision"),
        CheckConstraint("schema_version IN (1,2)", name="ck_specifications_schema_version"),
        CheckConstraint("state IN ('draft','approved')", name="ck_specifications_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    criterion_set_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("criterion_set_versions.id", ondelete="RESTRICT"), nullable=False
    )
    context_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("context_versions.id", ondelete="RESTRICT"), nullable=False
    )
    instruction_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_lint_report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("spec_lint_reports.id", use_alter=True)
    )
    approved_lint_resolution_revision: Mapped[int | None] = mapped_column(Integer)
    fidelity_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    fidelity_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CheckElement(TimestampMixin, Base):
    __tablename__ = "check_elements"
    __table_args__ = (
        UniqueConstraint(
            "specification_version_id", "element_code", name="uq_check_elements_spec_code"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    specification_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("specification_versions.id", ondelete="CASCADE"), nullable=False
    )
    element_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    element_code: Mapped[str] = mapped_column(String(80), nullable=False)
    criterion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("criteria.id", ondelete="RESTRICT"), nullable=False
    )
    obligation_key: Mapped[str] = mapped_column(String(240), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_question: Mapped[str] = mapped_column(String(2000), nullable=False)
    grounding_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ElementRequirementLink(Base):
    __tablename__ = "element_requirement_links"
    check_element_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("check_elements.id", ondelete="CASCADE"), primary_key=True
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirements.id", ondelete="RESTRICT"), primary_key=True
    )


class SpecLintReport(TimestampMixin, Base):
    __tablename__ = "spec_lint_reports"
    __table_args__ = (
        CheckConstraint("resolution_revision >= 1", name="ck_spec_lint_resolution_revision"),
        CheckConstraint(
            "initial_status IN ('failed','needs_review','passed')",
            name="ck_spec_lint_initial_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    specification_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("specification_versions.id", ondelete="CASCADE"), nullable=False
    )
    source_spec_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_package_hashes_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    registry_versions_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    initial_status: Mapped[str] = mapped_column(String(16), nullable=False)
    issues_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    resolution_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SpecLintResolution(TimestampMixin, Base):
    __tablename__ = "spec_lint_resolutions"
    __table_args__ = (
        UniqueConstraint("lint_report_id", "issue_code", name="uq_spec_lint_resolution_issue"),
        CheckConstraint(
            "resolution IN ('acknowledged_faithful','acknowledged_human_boundary')",
            name="ck_spec_lint_resolution_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lint_report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spec_lint_reports.id", ondelete="CASCADE"), nullable=False
    )
    issue_code: Mapped[str] = mapped_column(String(240), nullable=False)
    resolution: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class AssignmentGroup(RevisionMixin, Base):
    __tablename__ = "groups"
    __table_args__ = (
        UniqueConstraint("assignment_id", "name", name="uq_groups_assignment_name"),
        CheckConstraint("revision >= 1", name="ck_groups_revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class GroupMembership(TimestampMixin, Base):
    __tablename__ = "group_memberships"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_memberships_group_user"),
        Index(
            "uq_group_memberships_active_assignment_user",
            "assignment_id",
            "user_id",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Submission(RevisionMixin, Base):
    """Mutable upload thread. Final versions and roster snapshots are separate rows."""

    __tablename__ = "submissions"
    __table_args__ = (
        CheckConstraint(
            "(individual_user_id IS NOT NULL)::integer + (group_id IS NOT NULL)::integer = 1",
            name="ck_submissions_one_owner",
        ),
        CheckConstraint("draft_revision >= 1 AND revision >= 1", name="ck_submissions_revision"),
        ForeignKeyConstraint(
            ["active_final_version_id", "id"],
            ["submission_versions.id", "submission_versions.submission_id"],
            ondelete="RESTRICT",
            name="fk_submissions_active_final_version",
        ),
        ForeignKeyConstraint(
            ["latest_published_id", "id"],
            ["publications.id", "publications.submission_id"],
            ondelete="RESTRICT",
            name="fk_submissions_latest_publication",
            use_alter=True,
        ),
        Index(
            "uq_submissions_assignment_individual",
            "assignment_id",
            "individual_user_id",
            unique=True,
            postgresql_where=text("individual_user_id IS NOT NULL"),
        ),
        Index(
            "uq_submissions_assignment_group",
            "assignment_id",
            "group_id",
            unique=True,
            postgresql_where=text("group_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), nullable=False
    )
    individual_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="RESTRICT"))
    active_final_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    latest_published_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    draft_assignment_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_versions.id", ondelete="RESTRICT"), nullable=False
    )
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reopen_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubmissionVersion(TimestampMixin, Base):
    __tablename__ = "submission_versions"
    __table_args__ = (
        UniqueConstraint("submission_id", "version_no", name="uq_submission_versions_number"),
        UniqueConstraint("id", "submission_id", name="uq_submission_versions_id_submission"),
        CheckConstraint("version_no >= 1", name="ck_submission_versions_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignment_versions.id", ondelete="RESTRICT"), nullable=False
    )
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    finalized_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_late: Mapped[bool] = mapped_column(Boolean, nullable=False)


class SubmissionVersionFile(TimestampMixin, Base):
    __tablename__ = "submission_version_files"
    __table_args__ = (
        PrimaryKeyConstraint("submission_version_id", "file_asset_id"),
        ForeignKeyConstraint(
            ["submission_version_id", "submission_id"],
            ["submission_versions.id", "submission_versions.submission_id"],
            ondelete="RESTRICT",
            name="fk_submission_version_files_version_scope",
        ),
        ForeignKeyConstraint(
            ["file_asset_id", "submission_id"],
            ["file_assets.id", "file_assets.submission_id"],
            ondelete="RESTRICT",
            name="fk_submission_version_files_asset_scope",
        ),
        UniqueConstraint(
            "submission_version_id", "display_order", name="uq_submission_version_file_order"
        ),
        CheckConstraint("display_order >= 0", name="ck_submission_version_file_order"),
    )

    submission_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT"), nullable=False
    )
    file_asset_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)


class SubmissionVersionMember(TimestampMixin, Base):
    __tablename__ = "submission_version_members"
    __table_args__ = (PrimaryKeyConstraint("submission_version_id", "user_id"),)

    submission_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_versions.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    display_name_snapshot: Mapped[str] = mapped_column(String(240), nullable=False)
    email_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)


class AnalysisRun(TimestampMixin, Base):
    """Analysis metadata consumed by review; B4 fixtures can be explicitly synthetic."""

    __tablename__ = "analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "submission_version_id", "config_fingerprint", name="uq_analysis_run_fingerprint"
        ),
        CheckConstraint(
            "status IN ('queued','running','partial','completed','failed','blocked')",
            name="ck_analysis_runs_status",
        ),
        CheckConstraint(
            "provider_mode IN ('off','mock','live')", name="ck_analysis_runs_provider_mode"
        ),
        CheckConstraint(
            "data_origin IN ('synthetic','production')", name="ck_analysis_runs_data_origin"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    submission_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_versions.id", ondelete="RESTRICT"), nullable=False
    )
    specification_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("specification_versions.id", ondelete="RESTRICT"), nullable=False
    )
    release_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("release_manifests.id", ondelete="RESTRICT")
    )
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="off")
    data_origin: Mapped[str] = mapped_column(String(16), nullable=False, default="synthetic")
    limitations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"))
    manifest_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    stage_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    usage_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    completion_kind: Mapped[str | None] = mapped_column(String(32))


class AnalysisRunStage(TimestampMixin, Base):
    __tablename__ = "analysis_run_stages"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id", "stage_name", "input_hash", name="uq_analysis_run_stages_fingerprint"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    stage_name: Mapped[str] = mapped_column(String(40), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    output_refs_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    limitation_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetrievalChunk(TimestampMixin, Base):
    __tablename__ = "retrieval_chunks"
    __table_args__ = (
        UniqueConstraint(
            "submission_version_id",
            "evidence_item_id",
            "chunk_no",
            "chunk_hash",
            name="uq_retrieval_chunks_scope_evidence_chunk",
        ),
        UniqueConstraint("id", "submission_version_id", name="uq_retrieval_chunks_scope_id"),
        Index(
            "ix_retrieval_chunks_scope_order",
            "submission_version_id",
            "course_id",
            "assignment_id",
            "source_order",
            "evidence_item_id",
            "chunk_no",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    submission_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_versions.id", ondelete="RESTRICT"), nullable=False
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT"), nullable=False
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="RESTRICT"), nullable=False
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), nullable=False
    )
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT"), nullable=False
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False)
    tokenizer_manifest: Mapped[str] = mapped_column(String(80), nullable=False)
    token_start: Mapped[int] = mapped_column(Integer, nullable=False)
    token_end: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    anchor_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_order: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("retrieval_chunks.id", ondelete="RESTRICT")
    )
    structural_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    retrieval_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("method_manifests.id", ondelete="RESTRICT")
    )


class ChunkEvidenceLink(TimestampMixin, Base):
    __tablename__ = "chunk_evidence_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chunk_id", "submission_version_id"],
            ["retrieval_chunks.id", "retrieval_chunks.submission_version_id"],
            ondelete="CASCADE",
        ),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("retrieval_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT"), primary_key=True
    )
    submission_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_versions.id", ondelete="RESTRICT"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String(24), nullable=False, default="source")


class CoverageRecord(TimestampMixin, Base):
    __tablename__ = "coverage_records"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id", "check_element_id", name="uq_coverage_records_run_element"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    element_check_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("element_checks.id", ondelete="CASCADE"), unique=True
    )
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    check_element_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("check_elements.id", ondelete="RESTRICT"), nullable=False
    )
    retrieval_status: Mapped[str] = mapped_column(String(32), nullable=False)
    compatible_count: Mapped[int] = mapped_column(Integer, nullable=False)
    inspected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unresolved_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sufficient_for_positive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exhaustive_for_negative: Mapped[bool] = mapped_column(Boolean, nullable=False)
    inventory_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    retrieval_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    limitations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )


class CoverageInspection(TimestampMixin, Base):
    __tablename__ = "coverage_inspections"
    __table_args__ = (UniqueConstraint("coverage_record_id", "source_unit_id", name="uq_coverage_inspections_unit"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    coverage_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("coverage_records.id", ondelete="CASCADE"), nullable=False
    )
    source_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_units.id", ondelete="RESTRICT"), nullable=False
    )
    evidence_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(100))
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    inspection_status: Mapped[str | None] = mapped_column(String(24))
    observation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rule_or_call_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class GroundingEvaluation(TimestampMixin, Base):
    __tablename__ = "grounding_evaluations"
    __table_args__ = (
        UniqueConstraint("element_check_id", name="uq_grounding_evaluation_element_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    element_check_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("element_checks.id", ondelete="CASCADE"), nullable=False
    )
    mechanical_status: Mapped[str] = mapped_column(String(24), nullable=False)
    semantic_status: Mapped[str] = mapped_column(String(24), nullable=False)
    evaluation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    limitation_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    contract_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="anchored_content")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="not_run")
    decision_basis: Mapped[str | None] = mapped_column(String(32))
    mechanical_validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    semantic_support: Mapped[str] = mapped_column(String(24), nullable=False, default="not_run")
    coverage_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("coverage_records.id", ondelete="RESTRICT")
    )
    validator_manifest_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class SupportBinding(TimestampMixin, Base):
    __tablename__ = "evidence_support_bindings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    element_check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("element_checks.id", ondelete="CASCADE"), nullable=False)
    relationship_code: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT"), nullable=False
    )
    quote_spans_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    validator_refs_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class NumericLink(TimestampMixin, Base):
    __tablename__ = "numeric_links"
    __table_args__ = (
        UniqueConstraint(
            "element_check_id", "evidence_item_id", name="uq_numeric_links_element_evidence"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    element_check_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("element_checks.id", ondelete="CASCADE"), nullable=False
    )
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT"), nullable=False
    )
    numeric_engine_record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class RoutingDecision(TimestampMixin, Base):
    __tablename__ = "routing_decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    check_element_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("check_elements.id", ondelete="RESTRICT")
    )
    task: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    selected_role: Mapped[str | None] = mapped_column(String(24))
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    feature_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    guard_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    remaining_limits_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("method_manifests.id", ondelete="RESTRICT")
    )
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"))
    batching_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("method_manifests.id", ondelete="RESTRICT")
    )
    timing_refs_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class ProviderCallRecord(TimestampMixin, Base):
    __tablename__ = "provider_call_records"
    __table_args__ = (
        UniqueConstraint("logical_call_id", "attempt_no", name="uq_provider_calls_logical_attempt"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    routing_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("routing_decisions.id", ondelete="RESTRICT")
    )
    logical_call_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    task: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    usage_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    provider_request_id: Mapped[str | None] = mapped_column(String(240))


class SemanticBatchMember(TimestampMixin, Base):
    __tablename__ = "semantic_batch_members"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "batch_call_id",
            "check_element_id",
            name="uq_semantic_batch_members_entry",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    batch_call_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    check_element_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("check_elements.id", ondelete="RESTRICT"), nullable=False
    )
    entry_status: Mapped[str] = mapped_column(String(24), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class RoutingCallLink(Base):
    __tablename__ = "routing_call_links"
    __table_args__ = (PrimaryKeyConstraint("routing_decision_id", "call_id"),)

    routing_decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("routing_decisions.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class ElementCheck(TimestampMixin, Base):
    __tablename__ = "element_checks"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id", "check_element_id", name="uq_element_checks_run_element"
        ),
        CheckConstraint(
            "applicability_status IN ('applicable','not_applicable','unresolved')",
            name="ck_element_checks_applicability",
        ),
        CheckConstraint(
            "source_coverage_status IN ('sufficient_for_check','partial','unreadable','unresolved')",
            name="ck_element_checks_coverage",
        ),
        CheckConstraint(
            "evidence_relationship IN ('supported','partial','not_found','conflicting','unclear')",
            name="ck_element_checks_relationship",
        ),
        CheckConstraint(
            "check_status IS NULL OR check_status IN ('no_issue_detected','finding_candidate','unassessable')",
            name="ck_element_checks_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    check_element_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("check_elements.id", ondelete="RESTRICT"), nullable=False
    )
    applicability_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unresolved"
    )
    source_coverage_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unresolved"
    )
    evidence_relationship: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unclear"
    )
    check_status: Mapped[str | None] = mapped_column(String(32))
    observations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    interpretation: Mapped[str | None] = mapped_column(String(4000))
    reason_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class AnalysisResultEvidence(TimestampMixin, Base):
    __tablename__ = "analysis_result_evidence"
    element_check_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("element_checks.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT"), primary_key=True
    )


class FindingCandidate(TimestampMixin, Base):
    __tablename__ = "finding_candidates"
    __table_args__ = (
        CheckConstraint(
            "primary_category IN ('missing_requirement','rubric_coverage_gap','numerical_inconsistency')",
            name="ck_finding_candidates_category",
        ),
        CheckConstraint(
            "technical_validation IN ('valid','invalid')", name="ck_finding_candidates_technical"
        ),
        CheckConstraint(
            "semantic_support IN ('supported','unsupported','insufficient','not_required_deterministic')",
            name="ck_finding_candidates_semantic",
        ),
        UniqueConstraint("analysis_run_id", "dedup_key", name="uq_finding_candidates_dedup"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    primary_category: Mapped[str] = mapped_column(String(40), nullable=False)
    primary_criterion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("criteria.id", ondelete="RESTRICT"), nullable=False
    )
    obligation_key: Mapped[str] = mapped_column(String(240), nullable=False)
    issue_signature: Mapped[str] = mapped_column(String(240), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(240), nullable=False)
    claim: Mapped[str] = mapped_column(String(4000), nullable=False)
    technical_validation: Mapped[str] = mapped_column(String(16), nullable=False, default="valid")
    semantic_support: Mapped[str] = mapped_column(
        String(32), nullable=False, default="insufficient"
    )
    limitations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )


class FindingElementLink(TimestampMixin, Base):
    __tablename__ = "finding_element_links"
    finding_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finding_candidates.id", ondelete="CASCADE"), primary_key=True
    )
    check_element_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("check_elements.id", ondelete="RESTRICT"), primary_key=True
    )


class FindingRequirementLink(TimestampMixin, Base):
    __tablename__ = "finding_requirement_links"
    finding_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finding_candidates.id", ondelete="CASCADE"), primary_key=True
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirements.id", ondelete="RESTRICT"), primary_key=True
    )


class FindingCriterionLink(TimestampMixin, Base):
    __tablename__ = "finding_criterion_links"
    finding_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finding_candidates.id", ondelete="CASCADE"), primary_key=True
    )
    criterion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("criteria.id", ondelete="RESTRICT"), primary_key=True
    )


class FindingEvidenceLink(TimestampMixin, Base):
    __tablename__ = "finding_evidence_links"
    finding_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finding_candidates.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT"), primary_key=True
    )


class FindingCoverageLink(Base):
    __tablename__ = "finding_coverage_links"

    finding_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finding_candidates.id", ondelete="CASCADE"), primary_key=True
    )
    coverage_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("coverage_records.id", ondelete="RESTRICT"), primary_key=True
    )


class NumericObservation(TimestampMixin, Base):
    __tablename__ = "numeric_observations"
    __table_args__ = (
        CheckConstraint(
            "source_quality IN ('native_literal','ocr_verified','lecturer_confirmed','unverified')",
            name="ck_numeric_observations_quality",
        ),
        CheckConstraint(
            "(element_check_id IS NOT NULL)::integer + (review_version_id IS NOT NULL)::integer = 1",
            name="ck_numeric_observations_one_parent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    element_check_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("element_checks.id", ondelete="RESTRICT")
    )
    review_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_versions.id", ondelete="RESTRICT")
    )
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT"), nullable=False
    )
    literal: Mapped[str] = mapped_column(String(240), nullable=False)
    canonical_decimal_text: Mapped[str | None] = mapped_column(String(240))
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_quality: Mapped[str] = mapped_column(String(24), nullable=False)
    verification_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )


class NumericCheck(TimestampMixin, Base):
    __tablename__ = "numeric_checks"
    __table_args__ = (
        CheckConstraint(
            "comparability_status IN ('comparable','unresolved','not_comparable')",
            name="ck_numeric_checks_comparability",
        ),
        CheckConstraint(
            "operation IN ('pairwise','sum_against_reported','ratio_against_reported')",
            name="ck_numeric_checks_operation",
        ),
        CheckConstraint(
            "(element_check_id IS NOT NULL)::integer + (review_version_id IS NOT NULL)::integer = 1",
            name="ck_numeric_checks_one_parent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    element_check_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("element_checks.id", ondelete="RESTRICT")
    )
    review_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_versions.id", ondelete="RESTRICT")
    )
    numeric_policy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("numeric_policy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    comparability_status: Mapped[str] = mapped_column(String(24), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_context_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    operands_snapshot_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    result_decimal_text: Mapped[str | None] = mapped_column(String(240))
    difference_text: Mapped[str | None] = mapped_column(String(240))
    allowed_difference_text: Mapped[str | None] = mapped_column(String(240))
    inconsistent: Mapped[bool | None] = mapped_column(Boolean)
    rule_manifest_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("method_manifests.id"))


class ReviewVersion(RevisionMixin, Base):
    __tablename__ = "review_versions"
    __table_args__ = (
        UniqueConstraint("submission_version_id", "version_no", name="uq_review_versions_number"),
        CheckConstraint("version_no >= 1 AND revision >= 1", name="ck_review_versions_revision"),
        CheckConstraint("state IN ('draft','complete')", name="ck_review_versions_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    submission_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_versions.id", ondelete="RESTRICT"), nullable=False
    )
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="RESTRICT")
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lecturer_notes: Mapped[str] = mapped_column(String(20000), nullable=False, default="")
    limitations_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewElementDecision(RevisionMixin, Base):
    __tablename__ = "review_element_decisions"
    __table_args__ = (
        UniqueConstraint(
            "review_version_id", "check_element_id", name="uq_review_element_decision"
        ),
        CheckConstraint("review_status IN ('pending','reviewed')", name="ck_review_element_status"),
        CheckConstraint(
            "resolution IS NULL OR resolution IN ('observed_no_issue','confirmed_issue','not_applicable','unable_to_conclude')",
            name="ck_review_element_resolution",
        ),
        CheckConstraint(
            "applicability_override IS NULL OR applicability_override IN ('applicable','not_applicable','unresolved')",
            name="ck_review_element_applicability",
        ),
        CheckConstraint("revision >= 1", name="ck_review_element_revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    review_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_versions.id", ondelete="CASCADE"), nullable=False
    )
    check_element_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("check_elements.id", ondelete="RESTRICT"), nullable=False
    )
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    resolution: Mapped[str | None] = mapped_column(String(32))
    applicability_override: Mapped[str | None] = mapped_column(String(24))
    reason: Mapped[str | None] = mapped_column(String(2000))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewElementDecisionEvidence(TimestampMixin, Base):
    __tablename__ = "review_element_decision_evidence"
    review_element_decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_element_decisions.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT"), primary_key=True
    )
    annotation_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_annotation_revisions.id", ondelete="RESTRICT")
    )


class ReviewElementDecisionEvent(TimestampMixin, Base):
    __tablename__ = "review_element_decision_events"
    __table_args__ = (
        UniqueConstraint(
            "review_version_id",
            "check_element_id",
            "revision_no",
            name="uq_review_element_decision_event",
        ),
        CheckConstraint("revision_no >= 1", name="ck_review_element_event_revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    review_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_versions.id", ondelete="RESTRICT"), nullable=False
    )
    check_element_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("check_elements.id", ondelete="RESTRICT"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class ReviewFinding(RevisionMixin, Base):
    __tablename__ = "review_findings"
    __table_args__ = (
        CheckConstraint("origin IN ('ai_review','lecturer')", name="ck_review_findings_origin"),
        CheckConstraint(
            "action IN ('pending','accept','edit','reject','defer')",
            name="ck_review_findings_action",
        ),
        CheckConstraint(
            "primary_category IN ('missing_requirement','rubric_coverage_gap','numerical_inconsistency')",
            name="ck_review_findings_category",
        ),
        UniqueConstraint("review_version_id", "candidate_id", name="uq_review_findings_candidate"),
        CheckConstraint("revision >= 1", name="ck_review_findings_revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    review_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_versions.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("finding_candidates.id", ondelete="RESTRICT")
    )
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    primary_category: Mapped[str] = mapped_column(String(40), nullable=False)
    primary_criterion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("criteria.id", ondelete="RESTRICT"), nullable=False
    )
    claim: Mapped[str] = mapped_column(String(4000), nullable=False)
    coverage_attestation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    numeric_check_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("numeric_checks.id", ondelete="RESTRICT")
    )
    reason: Mapped[str | None] = mapped_column(String(2000))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewFindingTarget(TimestampMixin, Base):
    __tablename__ = "review_finding_targets"
    __table_args__ = (
        CheckConstraint(
            "(check_element_id IS NOT NULL)::integer + (requirement_id IS NOT NULL)::integer + (criterion_id IS NOT NULL)::integer = 1",
            name="ck_review_finding_target_one_ref",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    review_finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_findings.id", ondelete="CASCADE"), nullable=False
    )
    check_element_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("check_elements.id", ondelete="RESTRICT")
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="RESTRICT")
    )
    criterion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("criteria.id", ondelete="RESTRICT")
    )


class ReviewFindingDecision(TimestampMixin, Base):
    __tablename__ = "review_finding_decisions"
    __table_args__ = (
        UniqueConstraint(
            "review_finding_id", "revision_no", name="uq_review_finding_decision_revision"
        ),
        CheckConstraint("revision_no >= 1", name="ck_review_finding_decision_revision"),
        CheckConstraint(
            "action IN ('pending','accept','edit','reject','defer')",
            name="ck_review_finding_decision_action",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    review_finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_findings.id", ondelete="RESTRICT"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class EvidenceLink(TimestampMixin, Base):
    __tablename__ = "evidence_links"
    __table_args__ = (
        CheckConstraint(
            "(finding_candidate_id IS NOT NULL)::integer + (review_finding_id IS NOT NULL)::integer + (feedback_draft_id IS NOT NULL)::integer + (publication_id IS NOT NULL)::integer = 1",
            name="ck_evidence_links_one_subject",
        ),
        UniqueConstraint(
            "evidence_item_id",
            "review_finding_id",
            "annotation_revision_id",
            name="uq_evidence_link_review_finding_revision",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT"), nullable=False
    )
    finding_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("finding_candidates.id", ondelete="RESTRICT")
    )
    review_finding_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_findings.id", ondelete="RESTRICT")
    )
    feedback_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("feedback_drafts.id", ondelete="RESTRICT")
    )
    publication_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("publications.id", ondelete="RESTRICT")
    )
    annotation_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_annotation_revisions.id", ondelete="RESTRICT")
    )


class ManualEvidence(TimestampMixin, Base):
    __tablename__ = "manual_evidence"
    __table_args__ = (
        CheckConstraint(
            "origin IN ('lecturer_selected','lecturer_transcription')",
            name="ck_manual_evidence_origin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    review_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_versions.id", ondelete="RESTRICT"), nullable=False
    )
    annotation_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_annotation_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    source_anchor_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    transcription: Mapped[str | None] = mapped_column(String(20000))
    reason: Mapped[str | None] = mapped_column(String(2000))


class ReviewAnnotation(RevisionMixin, Base):
    __tablename__ = "review_annotations"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('span_selection','transcription','evidence_binding')",
            name="ck_review_annotation_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    review_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_versions.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "review_annotation_revisions.id",
            use_alter=True,
            name="fk_review_annotation_current_revision",
        )
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class ReviewAnnotationRevision(TimestampMixin, Base):
    __tablename__ = "review_annotation_revisions"
    __table_args__ = (
        UniqueConstraint("annotation_id", "revision_no", name="uq_review_annotation_revision_no"),
        CheckConstraint("revision_no >= 1", name="ck_review_annotation_revision_no"),
        CheckConstraint("operation IN ('upsert','remove')", name="ck_review_annotation_operation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    annotation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_annotations.id", ondelete="RESTRICT"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_annotation_revisions.id", ondelete="RESTRICT")
    )
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    file_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("file_assets.id", ondelete="RESTRICT"), nullable=False
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    anchor_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    original_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT")
    )
    corrected_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT")
    )
    transcription: Mapped[str | None] = mapped_column(String(20000))
    reason: Mapped[str | None] = mapped_column(String(2000))
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class ReviewAnnotationTarget(TimestampMixin, Base):
    __tablename__ = "review_annotation_targets"
    __table_args__ = (
        CheckConstraint(
            "relationship_code IS NULL OR length(relationship_code) <= 120",
            name="ck_review_annotation_relationship_code",
        ),
        CheckConstraint("role IS NULL OR length(role) <= 120", name="ck_review_annotation_role"),
        CheckConstraint(
            "check_element_id IS NOT NULL OR review_finding_id IS NOT NULL",
            name="ck_review_annotation_target_present",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    annotation_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_annotation_revisions.id", ondelete="CASCADE"), nullable=False
    )
    check_element_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("check_elements.id", ondelete="RESTRICT")
    )
    review_finding_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_findings.id", ondelete="RESTRICT")
    )
    relationship_code: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str | None] = mapped_column(String(120))


class FeedbackDraft(RevisionMixin, Base):
    __tablename__ = "feedback_drafts"
    __table_args__ = (
        UniqueConstraint("review_version_id", "sequence_no", name="uq_feedback_drafts_sequence"),
        CheckConstraint(
            "generation_mode IN ('model','template','manual')", name="ck_feedback_draft_mode"
        ),
        CheckConstraint("state IN ('draft','approved')", name="ck_feedback_draft_state"),
        CheckConstraint(
            "generation_status IN ('not_requested','queued','running','completed','fallback')",
            name="ck_feedback_generation_status",
        ),
        CheckConstraint("sequence_no >= 1 AND revision >= 1", name="ck_feedback_draft_revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    review_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_versions.id", ondelete="RESTRICT"), nullable=False
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    source_review_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    blocks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    body_markdown: Mapped[str] = mapped_column(String(20000), nullable=False)
    included_review_finding_ids_json: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    exclusion_reasons_json: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_manifest_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("method_manifests.id"))
    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"))
    generation_status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_requested")
    generation_failure_code: Mapped[str | None] = mapped_column(String(80))


class Publication(TimestampMixin, Base):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("submission_id", "sequence_no", name="uq_publications_sequence"),
        CheckConstraint("sequence_no >= 1", name="ck_publications_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="RESTRICT"), nullable=False
    )
    submission_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submission_versions.id", ondelete="RESTRICT"), nullable=False
    )
    review_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_versions.id", ondelete="RESTRICT"), nullable=False
    )
    feedback_draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feedback_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicationEvent(TimestampMixin, Base):
    __tablename__ = "publication_events"
    __table_args__ = (
        CheckConstraint("event IN ('released','withdrawn')", name="ck_publication_event_type"),
        Index(
            "uq_publication_released_event",
            "publication_id",
            unique=True,
            postgresql_where=text("event = 'released'"),
        ),
        Index(
            "uq_publication_withdrawn_event",
            "publication_id",
            unique=True,
            postgresql_where=text("event = 'withdrawn'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publications.id", ondelete="RESTRICT"), nullable=False
    )
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(2000))
    event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
