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


def test_auth_body_limit_counts_actual_chunked_bytes_before_login() -> None:
    client = TestClient(create_app())
    response = client.post('/api/v1/auth/login', content=iter([b' ' * 4096, b' ' * 4097]))
    assert response.status_code == 413
    assert response.json()['error']['code'] == 'PAYLOAD_TOO_LARGE'
    response = client.put('/api/v1/me/password', headers={'Content-Length': '9000'}, content=b'{}')
    assert response.status_code == 413
