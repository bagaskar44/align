from __future__ import annotations

from fastapi.testclient import TestClient

from align_api.main import create_app


def test_liveness_and_internal_provider_health_contract() -> None:
    client = TestClient(create_app())
    assert client.get("/health/live").json() == {"status": "live"}
    provider = client.get("/health/providers")
    assert provider.json() == {
        "mode": "mock",
        "status": "simulated",
        "label": "SIMULASI • data sintetis",
        "paid_calls": "disabled",
    }


def test_login_rejects_cross_origin_with_stable_error_envelope() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://attacker.invalid"},
        json={"email": "dosen@align.local", "password": "synthetic-passphrase"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert response.json()["error"]["message"] == "Aksi tidak diizinkan."
