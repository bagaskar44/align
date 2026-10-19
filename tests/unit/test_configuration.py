from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import SecretStr, ValidationError

from align_api.config import Settings


def baseline_settings(**updates: object) -> Settings:
    return Settings(_env_file=None, **updates)


def test_b0_configuration_keeps_vector_dimension_and_budget_exact() -> None:
    settings = baseline_settings()
    assert settings.embedding_dimensions == 1536
    assert settings.budget_confirmed_usd == Decimal("25.00")
    assert settings.budget_reserve_usd == Decimal("5.00")
    assert settings.budget_planned_topup_usd == Decimal("0.00")
    assert settings.batch_baseline_elements == 1


def test_incompatible_vector_and_paid_provider_without_key_are_rejected() -> None:
    with pytest.raises(ValidationError, match="1536"):
        baseline_settings(embedding_dimensions=768)
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        baseline_settings(provider_mode="live")
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        baseline_settings(provider_mode="live", openai_api_key="   ")
    assert baseline_settings(openai_api_key="").openai_api_key is None


def test_production_requires_https_and_explicit_demo_label_for_mock() -> None:
    production_secrets = {
        "csrf_server_secret": SecretStr("a" * 64),
        "s3_secret_access_key": SecretStr("b" * 64),
    }
    with pytest.raises(ValidationError, match="HTTPS"):
        baseline_settings(app_env="production", app_origin="http://localhost:3000", **production_secrets)
    with pytest.raises(ValidationError, match="synthetic demo label"):
        baseline_settings(
            app_env="production",
            app_origin="https://align.example.edu",
            provider_mode="mock",
            app_demo_mode=False,
            **production_secrets,
        )
    explicit_demo = baseline_settings(
        app_env="production",
        app_origin="https://demo.example.edu",
        provider_mode="mock",
        app_demo_mode=True,
        data_mode="synthetic",
        align_seed_password=None,
        **production_secrets,
    )
    assert explicit_demo.app_demo_mode


def test_monetary_configuration_does_not_coerce_float_values() -> None:
    with pytest.raises(ValidationError):
        baseline_settings(budget_confirmed_usd=25.0)

