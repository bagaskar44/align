from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from align_api.config import Settings, get_settings
from align_api.lti import (
    FLOW_TTL,
    LTI_CLAIM,
    _local_role,
    _parse_login_fields,
    _valid_target_uri,
    _validate_deployment_hint,
    _verify_lti_token,
    lti_tool_jwks,
)
from align_api.main import create_app


def _setup() -> tuple[Any, Any, dict[str, Any], Any, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    now = int(time.time())
    nonce = "single-use-nonce"
    target = "https://align-b9.localhost:8443/api/v1/lti/launch"
    claims: dict[str, Any] = {
        "iss": "https://moodle-b9.localhost:8443",
        "sub": "moodle-subject-71",
        "aud": "align-client",
        "iat": now,
        "exp": now + 180,
        "nonce": nonce,
        f"{LTI_CLAIM}deployment_id": "moodle-deployment",
        f"{LTI_CLAIM}message_type": "LtiResourceLinkRequest",
        f"{LTI_CLAIM}version": "1.3.0",
        f"{LTI_CLAIM}target_link_uri": target,
        f"{LTI_CLAIM}context": {"id": "course-54"},
        f"{LTI_CLAIM}resource_link": {"id": "activity-20"},
        f"{LTI_CLAIM}roles": ["http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"],
    }
    platform = SimpleNamespace(
        issuer="https://moodle-b9.localhost:8443",
        client_id="align-client",
        deployment_id="moodle-deployment",
    )
    flow = SimpleNamespace(
        created_at=datetime.now(UTC) - timedelta(seconds=5),
        nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
        target_link_uri=target,
    )
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    return private_key, platform, {"keys": [public_jwk]}, flow, token


def test_lti_login_flow_ttl_is_five_minutes() -> None:
    assert FLOW_TTL == timedelta(minutes=5)


def test_lti_launch_requires_registered_rsa_claims_and_signed_context() -> None:
    _private_key, platform, jwks, flow, token = _setup()
    verified = _verify_lti_token(token, platform, flow, jwks)
    assert verified["sub"] == "moodle-subject-71"
    assert verified[f"{LTI_CLAIM}context"]["id"] == "course-54"


@pytest.mark.parametrize(
    "change",
    [
        lambda claims: claims.update({"iss": "https://unregistered.example"}),
        lambda claims: claims.update({"aud": "another-client"}),
        lambda claims: claims.update({"azp": "another-client"}),
        lambda claims: claims.update({"nonce": "replayed-or-mismatched"}),
        lambda claims: claims.update({f"{LTI_CLAIM}deployment_id": "other-deployment"}),
        lambda claims: claims.update({f"{LTI_CLAIM}version": "1.0.0"}),
        lambda claims: claims.update({f"{LTI_CLAIM}target_link_uri": "https://evil.example/"}),
        lambda claims: claims.update({f"{LTI_CLAIM}message_type": "LtiDeepLinkingRequest"}),
    ],
)
def test_lti_launch_rejects_mismatched_registered_claims(change: Any) -> None:
    private_key, platform, jwks, flow, _token = _setup()
    claims = dict(jwt.decode(_token, options={"verify_signature": False}))
    change(claims)
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    with pytest.raises(HTTPException) as error:
        _verify_lti_token(token, platform, flow, jwks)
    assert error.value.status_code == 401


def test_lti_launch_rejects_unknown_key_and_non_rs256_algorithm() -> None:
    private_key, platform, jwks, flow, _token = _setup()
    claims = dict(jwt.decode(_token, options={"verify_signature": False}))
    unknown_kid = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "unknown"})
    with pytest.raises(HTTPException):
        _verify_lti_token(unknown_kid, platform, flow, jwks)
    with pytest.raises(HTTPException):
        _verify_lti_token(
            jwt.encode(claims, "test-hmac-key-that-is-not-a-credential-1234", algorithm="HS256"),
            platform,
            flow,
            jwks,
        )


def test_lti_launch_rejects_invalid_signature_and_expired_token() -> None:
    _private_key, platform, jwks, flow, original = _setup()
    claims = dict(jwt.decode(original, options={"verify_signature": False}))
    unrelated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bad_signature = jwt.encode(claims, unrelated_key, algorithm="RS256", headers={"kid": "test-key"})
    with pytest.raises(HTTPException) as invalid_signature:
        _verify_lti_token(bad_signature, platform, flow, jwks)
    assert invalid_signature.value.status_code == 401

    claims["exp"] = int(time.time()) - 60
    expired = jwt.encode(claims, _private_key, algorithm="RS256", headers={"kid": "test-key"})
    with pytest.raises(HTTPException) as expired_token:
        _verify_lti_token(expired, platform, flow, jwks)
    assert expired_token.value.status_code == 401


def test_lti_roles_are_only_a_scoped_hint_and_unknown_role_is_denied() -> None:
    assert (
        _local_role(["http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"]) == "lecturer"
    )
    assert _local_role(["http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"]) == "student"
    assert _local_role(["http://purl.imsglobal.org/vocab/lis/v2/membership#Administrator"]) is None
    assert (
        _local_role(
            [
                "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
                "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner",
            ]
        )
        is None
    )


def test_lti_target_uri_is_limited_to_align_resource_paths() -> None:
    settings = Settings(app_origin="https://align-b9.localhost:8443")
    assert _valid_target_uri("https://align-b9.localhost:8443/api/v1/lti/launch", settings)
    assert _valid_target_uri(
        "https://align-b9.localhost:8443/courses/2d0cc5fb-c27c-45a6-b04e-26770fbebf80", settings
    )
    assert not _valid_target_uri(
        "https://evil.example/courses/2d0cc5fb-c27c-45a6-b04e-26770fbebf80", settings
    )
    assert not _valid_target_uri("https://align-b9.localhost:8443/https://evil.example", settings)


def test_moodle_login_deployment_hint_is_accepted_but_pinned() -> None:
    settings = Settings(app_origin="https://align-b9.localhost:8443")
    values = {
        "iss": "https://moodle-b9.localhost:8443",
        "client_id": "align-client",
        "login_hint": "7",
        "target_link_uri": "https://align-b9.localhost:8443/api/v1/lti/launch",
        "lti_deployment_id": "1",
    }
    assert _parse_login_fields(values, settings)[0:2] == (
        "https://moodle-b9.localhost:8443",
        "align-client",
    )

    with pytest.raises(HTTPException) as error:
        _validate_deployment_hint({**values, "lti_deployment_id": "other"}, "1")
    assert error.value.status_code == 400


def test_lti_tool_jwks_publishes_public_key_only(tmp_path: Path) -> None:
    _private_key, _platform, jwks, _flow, _token = _setup()
    path = tmp_path / "jwks.json"
    path.write_text(json.dumps(jwks), encoding="utf-8")

    response = lti_tool_jwks(Settings(lms_adapter_enabled=True, lti_tool_jwks_file=path))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300"
    assert json.loads(response.body) == jwks


def test_lti_tool_jwks_is_hidden_when_disabled_and_rejects_private_fields(tmp_path: Path) -> None:
    _private_key, _platform, jwks, _flow, _token = _setup()
    path = tmp_path / "jwks.json"
    path.write_text(json.dumps(jwks), encoding="utf-8")
    with pytest.raises(HTTPException) as disabled:
        lti_tool_jwks(Settings(lms_adapter_enabled=False, lti_tool_jwks_file=path))
    assert disabled.value.status_code == 404

    jwks["keys"][0]["d"] = "private-material"
    path.write_text(json.dumps(jwks), encoding="utf-8")
    with pytest.raises(HTTPException) as invalid:
        lti_tool_jwks(Settings(lms_adapter_enabled=True, lti_tool_jwks_file=path))
    assert invalid.value.status_code == 503


def test_lti_routes_are_inert_when_feature_flag_is_off() -> None:
    settings = Settings(lms_adapter_enabled=False)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        assert client.get("/api/v1/integrations/lti/login").status_code == 404
        assert (
            client.post(
                "/api/v1/lti/launch",
                data={"state": "untrusted", "id_token": "untrusted"},
            ).status_code
            == 404
        )
