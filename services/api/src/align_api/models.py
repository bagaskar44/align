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
    String,
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
            "(purpose = 'context' AND course_id IS NOT NULL AND assignment_id IS NULL AND submission_id IS NULL) OR (purpose = 'rubric' AND assignment_id IS NOT NULL AND course_id IS NOT NULL AND submission_id IS NULL) OR (purpose = 'submission' AND submission_id IS NOT NULL AND course_id IS NOT NULL)",
            name="ck_file_assets_scope",
        ),
        Index("ix_file_assets_course_purpose", "course_id", "purpose", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    submission_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
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
    submission_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
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
            "type IN ('paragraph','table','diagram','image_text','numeric_observation')",
            name="ck_evidence_items_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_units.id", ondelete="CASCADE"), nullable=False
    )
    submission_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
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
