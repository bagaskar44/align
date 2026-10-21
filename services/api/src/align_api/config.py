from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "production"] = "development"
    app_origin: str = "http://localhost:3000"
    app_debug: bool = False
    app_demo_mode: bool = False
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str = "postgresql+psycopg://align_api:align_api@localhost:5432/align"
    operator_database_url: str | None = None
    worker_database_url: str = "postgresql+psycopg://align_worker:align_worker@localhost:5432/align"
    migration_database_url: str = "postgresql+psycopg://align_migration:align_migration@localhost:5432/align"
    redis_url: str = "redis://localhost:6379/0"
    redis_queue_prefix: str = "align-dev"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "align-private"
    s3_access_key_id: str = "align-app"
    s3_secret_access_key: SecretStr = SecretStr("development-only-secret")
    s3_region: str = "us-east-1"
    s3_use_path_style: bool = True

    csrf_server_secret: SecretStr = SecretStr("development-only-csrf-secret-change-me")
    provider_mode: Literal["mock", "off", "live"] = "mock"
    paid_calls_enabled: bool = False
    openai_api_key: SecretStr | None = None
    model_efficient: str | None = None
    model_complex: str | None = None
    model_vision: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    model_rate_manifest: str = "config/model-rates.unverified.json"
    routing_policy_file: str = "config/routing.baseline.json"
    batching_policy_file: str = "config/batching.baseline.json"
    ocr_engine: str = "tesseract"
    ocr_language: str = "ind+eng"

    data_mode: Literal["synthetic", "institutional"] = "synthetic"
    lms_adapter_enabled: bool = False
    align_seed_password: SecretStr | None = None
    budget_confirmed_usd: Decimal = Decimal("25.00")
    budget_reserve_usd: Decimal = Decimal("5.00")
    budget_planned_topup_usd: Decimal = Decimal("0.00")
    retention_source_days: int = 90
    retention_review_days: int = 365
    backup_retention_days: int = 30

    max_files_per_bundle: int = 5
    max_file_bytes: int = 20_971_520
    max_image_pixels: int = 25_000_000
    max_pdf_image_pages_per_bundle: int = 100
    max_xlsx_sheets: int = 25
    max_xlsx_nonempty_cells: int = 100_000
    max_docx_nodes: int = 20_000
    max_docx_media: int = 1_000
    max_ooxml_entries: int = 10_000
    max_ooxml_expanded_bytes: int = 536_870_912
    job_max_calls: int = 16
    job_max_vision_calls: int = 4
    job_max_text_input_tokens: int = 40_000
    job_max_text_output_tokens: int = 8_000
    job_max_embedding_tokens: int = 80_000
    job_max_active_seconds: int = 600
    analysis_budget_usd: Decimal = Decimal("0.20")
    feedback_budget_usd: Decimal = Decimal("0.03")
    context_draft_budget_usd: Decimal = Decimal("0.05")
    spec_draft_budget_usd: Decimal = Decimal("0.05")

    parser_profile_native: Literal["native_ooxml_v1"] = "native_ooxml_v1"
    parser_profile_document: Literal["docling_native_v1"] = "docling_native_v1"
    parser_native_memory_bytes: int = 2_147_483_648
    parser_document_memory_bytes: int = 8_589_934_592
    parser_light_memory_bytes: int = 4_294_967_296
    parser_node_memory_budget_bytes: int = 8_589_934_592
    parser_node_headroom_bytes: int = 1_073_741_824
    parser_heavy_max_per_node: int = 1
    parser_node_id: str = "align-dev-node"
    parser_page_batch_size: int = 1
    parser_task_soft_seconds: int = 120
    parser_task_hard_seconds: int = 150
    parser_max_attempts: int = Field(default=3, ge=1, le=10)
    stage_soft_seconds: int = 120
    stage_hard_seconds: int = 150
    broker_visibility_seconds: int = 300
    worker_lease_seconds: int = 180
    worker_heartbeat_seconds: int = 30
    reaper_interval_seconds: int = 60
    batch_baseline_elements: int = 1
    batch_max_elements: int = 5

    session_idle_hours: int = 8
    session_absolute_hours: int = 24
    login_account_limit: int = 5
    login_ip_limit: int = 20
    login_window_seconds: int = 900
    max_password_bytes: int = 512

    @field_validator(
        "budget_confirmed_usd",
        "budget_reserve_usd",
        "budget_planned_topup_usd",
        "analysis_budget_usd",
        "feedback_budget_usd",
        "context_draft_budget_usd",
        "spec_draft_budget_usd",
        mode="before",
    )
    @classmethod
    def parse_decimal_from_string(cls, value: object) -> Decimal:
        if isinstance(value, Decimal):
            return value
        if not isinstance(value, str):
            raise ValueError("monetary values must be strings")
        return Decimal(value)

    @field_validator("csrf_server_secret")
    @classmethod
    def require_csrf_secret_bytes(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("CSRF_SERVER_SECRET must contain at least 32 bytes")
        return value

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def empty_api_key_is_unset(cls, value: SecretStr | str | None) -> SecretStr | str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_runtime_configuration(self) -> Settings:
        if self.embedding_dimensions != 1536:
            raise ValueError("EMBEDDING_DIMENSIONS must match the B0 vector schema (1536)")
        if self.stage_hard_seconds <= self.stage_soft_seconds:
            raise ValueError("STAGE_HARD_SECONDS must exceed STAGE_SOFT_SECONDS")
        if self.parser_task_hard_seconds <= self.parser_task_soft_seconds:
            raise ValueError("PARSER_TASK_HARD_SECONDS must exceed PARSER_TASK_SOFT_SECONDS")
        if self.worker_lease_seconds <= self.stage_hard_seconds:
            raise ValueError("WORKER_LEASE_SECONDS must exceed the hard task timeout")
        if self.batch_baseline_elements != 1 or self.batch_max_elements < self.batch_baseline_elements:
            raise ValueError("baseline batching must start at exactly one element")
        if self.budget_planned_topup_usd != Decimal("0.00"):
            raise ValueError("BUDGET_PLANNED_TOPUP_USD is informational and must not be funded")
        if self.budget_confirmed_usd < self.budget_reserve_usd:
            raise ValueError("confirmed budget cannot be below the protected reserve")
        if self.app_env == "production":
            if self.app_debug:
                raise ValueError("debug mode is forbidden in production")
            if not self.app_origin.startswith("https://"):
                raise ValueError("production requires an explicit HTTPS APP_ORIGIN")
            if self.csrf_server_secret.get_secret_value() == "development-only-csrf-secret-change-me":
                raise ValueError("production requires a generated CSRF_SERVER_SECRET")
            if self.s3_secret_access_key.get_secret_value() == "development-only-secret":
                raise ValueError("production requires a storage service credential")
            if self.provider_mode == "mock" and not (
                self.app_demo_mode and self.data_mode == "synthetic"
            ):
                raise ValueError("production mock mode requires an explicit synthetic demo label")
            if self.align_seed_password is not None:
                raise ValueError("synthetic seed passwords are forbidden in production")
        if self.provider_mode == "live" and (
            self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip()
        ):
            raise ValueError("PROVIDER_MODE=live requires OPENAI_API_KEY")
        return self

    @property
    def csrf_secret_bytes(self) -> bytes:
        return self.csrf_server_secret.get_secret_value().encode("utf-8")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

