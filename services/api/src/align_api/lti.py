from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import UUID

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session as DbSession

from align_api.auth import issue_session
from align_api.config import Settings, get_settings
from align_api.db import get_db
from align_api.models import (
    Assignment,
    AssignmentVersion,
    AuditEvent,
    Enrollment,
    LmsCourseAssignmentMapping,
    LtiCoursePermission,
    LtiIdentityBinding,
    LtiLoginFlow,
    LtiPlatform,
    LtiSessionBinding,
    Publication,
    Submission,
    SubmissionVersionMember,
    User,
)

router = APIRouter(tags=["lti"])
LTI_CLAIM = "https://purl.imsglobal.org/spec/lti/claim/"
CORRELATION_COOKIE = "align_lti_correlation"
FLOW_TTL = timedelta(minutes=5)
_LOGIN_FIELDS = {
    "iss",
    "client_id",
    "login_hint",
    "target_link_uri",
    "lti_message_hint",
    "lti_deployment_id",
}


@router.get("/integrations/lti/jwks")
def lti_tool_jwks(settings: Annotated[Settings, Depends(get_settings)]) -> Response:
    """Publish only the configured public signing key for Moodle tool registration."""
    if not settings.lms_adapter_enabled or settings.lti_tool_jwks_file is None:
        raise HTTPException(status_code=404, detail="LMS integration is disabled")
    try:
        document = json.loads(settings.lti_tool_jwks_file.read_text(encoding="utf-8"))
        keys = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(keys, list) or not keys:
            raise ValueError("missing public keys")
        for key in keys:
            required_fields = {"kty", "use", "alg", "kid", "n", "e"}
            if (
                not isinstance(key, dict)
                or not required_fields <= set(key)
                or not set(key) <= required_fields | {"key_ops"}
                or key["kty"] != "RSA"
                or key["use"] != "sig"
                or key["alg"] != "RS256"
                or key.get("key_ops", ["verify"]) != ["verify"]
                or not isinstance(key["kid"], str)
                or not key["kid"]
                or not isinstance(key["n"], str)
                or not isinstance(key["e"], str)
            ):
                raise ValueError("invalid public key")
            public_key = jwt.PyJWK.from_dict(key, algorithm="RS256").key
            if public_key.key_size < 2048:
                raise ValueError("RSA key is too small")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, jwt.PyJWTError) as exc:
        raise HTTPException(status_code=503, detail="LTI tool keyset unavailable") from exc
    return JSONResponse(
        content={"keys": keys},
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("HTTPS URL required")
    port = f":{parsed.port}" if parsed.port else ""
    return f"https://{parsed.hostname.lower()}{port}"


def _validate_platform_urls(platform: LtiPlatform, settings: Settings) -> None:
    try:
        registered_origin = _origin(platform.moodle_origin)
        if registered_origin not in settings.lti_allowed_origin_set:
            raise ValueError
        for endpoint in (platform.authorization_url, platform.token_url, platform.jwks_url):
            if _origin(endpoint) != registered_origin:
                raise ValueError
        callback_origin = _origin(settings.lti_callback_url)
        if callback_origin != _origin(settings.app_origin):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="LTI platform is not allowlisted") from exc


def _valid_target_uri(uri: str, settings: Settings) -> bool:
    try:
        if _origin(uri) != _origin(settings.app_origin):
            return False
    except ValueError:
        return False
    parsed = urlsplit(uri)
    callback = urlsplit(settings.lti_callback_url)
    if parsed.path == callback.path:
        return True
    parts = parsed.path.strip("/").split("/")
    if len(parts) == 2 and parts[0] == "courses":
        try:
            UUID(parts[1])
            return True
        except ValueError:
            return False
    return False


def _parse_login_fields(
    values: dict[str, str], settings: Settings
) -> tuple[str, str, str, str, str | None]:
    if set(values) - _LOGIN_FIELDS:
        raise HTTPException(status_code=422, detail="unexpected LTI login field")
    issuer = values.get("iss", "")
    client_id = values.get("client_id", "")
    login_hint = values.get("login_hint", "")
    target_link_uri = values.get("target_link_uri", "")
    message_hint = values.get("lti_message_hint")
    if not issuer or len(issuer) > 500 or not client_id or len(client_id) > 240:
        raise HTTPException(status_code=400, detail="invalid LTI login request")
    if (
        not login_hint
        or len(login_hint) > 4096
        or (message_hint is not None and len(message_hint) > 4096)
        or ("lti_deployment_id" in values and not values["lti_deployment_id"])
        or len(values.get("lti_deployment_id", "")) > 240
    ):
        raise HTTPException(status_code=400, detail="invalid LTI login request")
    if not _valid_target_uri(target_link_uri, settings):
        raise HTTPException(status_code=400, detail="unregistered LTI target")
    return issuer, client_id, login_hint, target_link_uri, message_hint


def _validate_deployment_hint(values: dict[str, str], deployment_id: str) -> None:
    hint = values.get("lti_deployment_id")
    if hint is not None and hint != deployment_id:
        raise HTTPException(status_code=400, detail="unregistered LTI deployment")


async def _start_login(
    values: dict[str, str], request: Request, db: DbSession, settings: Settings
) -> RedirectResponse:
    if not settings.lms_adapter_enabled:
        raise HTTPException(status_code=404, detail="LMS integration is disabled")
    issuer, client_id, login_hint, target_uri, message_hint = _parse_login_fields(values, settings)
    platforms = db.scalars(
        select(LtiPlatform).where(
            LtiPlatform.issuer == issuer,
            LtiPlatform.client_id == client_id,
            LtiPlatform.active.is_(True),
        )
    ).all()
    if len(platforms) != 1:
        raise HTTPException(status_code=400, detail="unregistered LTI platform")
    platform = platforms[0]
    _validate_deployment_hint(values, platform.deployment_id)
    _validate_platform_urls(platform, settings)

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    correlation = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    flow = LtiLoginFlow(
        platform_id=platform.id,
        state_hash=_sha256(state),
        nonce_hash=_sha256(nonce),
        correlation_hash=_sha256(correlation),
        target_link_uri=target_uri,
        expires_at=now + FLOW_TTL,
    )
    db.add(flow)
    db.commit()

    parameters = {
        "scope": "openid",
        "response_type": "id_token",
        "response_mode": "form_post",
        "prompt": "none",
        "client_id": platform.client_id,
        "redirect_uri": settings.lti_callback_url,
        "login_hint": login_hint,
        "state": state,
        "nonce": nonce,
        "target_link_uri": target_uri,
    }
    if message_hint is not None:
        parameters["lti_message_hint"] = message_hint
    parsed_auth = urlsplit(platform.authorization_url)
    query = (
        f"{parsed_auth.query}&{urlencode(parameters)}"
        if parsed_auth.query
        else urlencode(parameters)
    )
    redirect = RedirectResponse(urlunsplit(parsed_auth._replace(query=query)), status_code=303)
    redirect.set_cookie(
        CORRELATION_COOKIE,
        correlation,
        max_age=int(FLOW_TTL.total_seconds()),
        httponly=True,
        secure=True,
        samesite="none",
        path=urlsplit(settings.lti_callback_url).path,
    )
    redirect.headers["Cache-Control"] = "no-store"
    return redirect


@router.get("/integrations/lti/login")
async def lti_login_get(
    request: Request,
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    values: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key in values:
            raise HTTPException(status_code=422, detail="duplicate LTI login field")
        values[key] = value
    return await _start_login(values, request, db, settings)


@router.post("/integrations/lti/login")
async def lti_login_post(
    request: Request,
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    if request.headers.get("content-type", "").split(";", 1)[0] not in {
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    }:
        raise HTTPException(status_code=415, detail="LTI login form required")
    form = await request.form()
    values: dict[str, str] = {}
    for key, value in form.multi_items():
        if key in values or not isinstance(value, str):
            raise HTTPException(status_code=422, detail="invalid LTI login field")
        values[key] = value
    return await _start_login(values, request, db, settings)


async def _read_jwks(platform: LtiPlatform, settings: Settings) -> dict[str, Any]:
    _validate_platform_urls(platform, settings)
    timeout = httpx.Timeout(5.0, connect=2.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                "GET", platform.jwks_url, headers={"Accept": "application/json"}
            ) as response:
                if response.status_code != 200:
                    raise ValueError("JWKS unavailable")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > settings.moodle_ws_max_response_bytes:
                        raise ValueError("JWKS too large")
                    chunks.append(chunk)
        document = httpx.Response(200, content=b"".join(chunks)).json()
        if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
            raise ValueError("invalid JWKS")
        return document
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="registered LTI keys unavailable") from exc


def _verify_lti_token(
    token: str,
    platform: LtiPlatform,
    flow: LtiLoginFlow,
    jwks: dict[str, Any],
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
        if (
            header.get("alg") != "RS256"
            or not isinstance(header.get("kid"), str)
            or header.get("crit")
        ):
            raise ValueError("unapproved JWT header")
        matches = [
            key for key in jwks["keys"] if isinstance(key, dict) and key.get("kid") == header["kid"]
        ]
        if (
            len(matches) != 1
            or matches[0].get("kty") != "RSA"
            or matches[0].get("use", "sig") != "sig"
        ):
            raise ValueError("unknown signing key")
        key_alg = matches[0].get("alg")
        if key_alg not in (None, "RS256"):
            raise ValueError("unapproved signing key")
        public_key = jwt.PyJWK.from_dict(matches[0], algorithm="RS256").key
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=platform.client_id,
            issuer=platform.issuer,
            leeway=30,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
        )
        if claims.get("azp") is not None and claims["azp"] != platform.client_id:
            raise ValueError("authorized party mismatch")
        aud = claims.get("aud")
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != platform.client_id:
            raise ValueError("authorized party required")
        if claims.get("nonce") is None or not secrets.compare_digest(
            _sha256(str(claims["nonce"])), flow.nonce_hash
        ):
            raise ValueError("nonce mismatch")
        now = int(time.time())
        issued_at = claims.get("iat")
        expires_at = claims.get("exp")
        if type(issued_at) is not int or type(expires_at) is not int:
            raise ValueError("invalid token time")
        if issued_at > now + 30 or issued_at < int(flow.created_at.timestamp()) - 30:
            raise ValueError("token issued outside login flow")
        if now - issued_at > int(FLOW_TTL.total_seconds()) + 30 or expires_at <= now:
            raise ValueError("expired LTI launch")
        if claims.get(f"{LTI_CLAIM}deployment_id") != platform.deployment_id:
            raise ValueError("deployment mismatch")
        if claims.get(f"{LTI_CLAIM}message_type") != "LtiResourceLinkRequest":
            raise ValueError("unexpected LTI message type")
        if claims.get(f"{LTI_CLAIM}version") != "1.3.0":
            raise ValueError("unsupported LTI version")
        if claims.get(f"{LTI_CLAIM}target_link_uri") != flow.target_link_uri:
            raise ValueError("target URI mismatch")
        context = claims.get(f"{LTI_CLAIM}context")
        resource_link = claims.get(f"{LTI_CLAIM}resource_link")
        roles = claims.get(f"{LTI_CLAIM}roles")
        if (
            not isinstance(context, dict)
            or not isinstance(context.get("id"), str)
            or not context["id"].strip()
            or len(context["id"]) > 500
        ):
            raise ValueError("missing LTI context")
        if (
            not isinstance(resource_link, dict)
            or not isinstance(resource_link.get("id"), str)
            or not resource_link["id"].strip()
            or len(resource_link["id"]) > 500
        ):
            raise ValueError("missing LTI resource link")
        if (
            not isinstance(claims.get("sub"), str)
            or not claims["sub"].strip()
            or len(claims["sub"]) > 500
        ):
            raise ValueError("missing or oversized LTI subject")
        if (
            not isinstance(roles, list)
            or len(roles) > 100
            or not all(isinstance(role, str) and 0 < len(role) <= 500 for role in roles)
        ):
            raise ValueError("invalid LTI roles")
        return claims
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="invalid LTI launch") from exc


def _local_role(roles: list[str]) -> str | None:
    role_names = {role.rsplit("#", 1)[-1].rsplit("/", 1)[-1] for role in roles}
    has_instructor = bool(role_names.intersection({"Instructor", "Faculty", "Staff"}))
    has_student = bool(role_names.intersection({"Learner", "Student"}))
    if has_instructor == has_student:
        return None
    return "lecturer" if has_instructor else "student"


def _student_submission(db: DbSession, assignment_id: UUID, user_id: UUID) -> Submission | None:
    member_of_latest_publication = (
        select(SubmissionVersionMember.user_id)
        .join(
            Publication,
            Publication.submission_version_id == SubmissionVersionMember.submission_version_id,
        )
        .where(
            Publication.id == Submission.latest_published_id,
            SubmissionVersionMember.user_id == user_id,
        )
        .exists()
    )
    member_of_active_final = (
        select(SubmissionVersionMember.user_id)
        .where(
            SubmissionVersionMember.submission_version_id == Submission.active_final_version_id,
            SubmissionVersionMember.user_id == user_id,
        )
        .exists()
    )
    return db.scalar(
        select(Submission)
        .where(
            Submission.assignment_id == assignment_id,
            or_(
                Submission.individual_user_id == user_id,
                member_of_latest_publication,
                member_of_active_final,
            ),
        )
        .order_by(Submission.updated_at.desc())
        .limit(1)
    )


@router.post("/lti/launch")
async def lti_launch(
    request: Request,
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    if not settings.lms_adapter_enabled:
        raise HTTPException(status_code=404, detail="LMS integration is disabled")
    if request.headers.get("content-type", "").split(";", 1)[0] not in {
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    }:
        raise HTTPException(status_code=415, detail="LTI launch form required")
    form = await request.form()
    values: dict[str, str] = {}
    for key, value in form.multi_items():
        if key in values or not isinstance(value, str):
            raise HTTPException(status_code=422, detail="invalid LTI launch form")
        values[key] = value
    if (
        set(values) != {"state", "id_token"}
        or len(values["state"]) > 256
        or len(values["id_token"]) > 32768
    ):
        raise HTTPException(status_code=400, detail="invalid LTI launch form")

    now = datetime.now(UTC)
    flow = db.scalar(
        select(LtiLoginFlow)
        .where(LtiLoginFlow.state_hash == _sha256(values["state"]))
        .with_for_update()
    )
    correlation = request.cookies.get(CORRELATION_COOKIE, "")
    if (
        flow is None
        or not correlation
        or not secrets.compare_digest(_sha256(correlation), flow.correlation_hash)
        or flow.consumed_at is not None
        or flow.expires_at <= now
    ):
        raise HTTPException(status_code=401, detail="invalid or expired LTI flow")
    platform = db.get(LtiPlatform, flow.platform_id)
    if platform is None or not platform.active:
        raise HTTPException(status_code=401, detail="invalid or expired LTI flow")
    _validate_platform_urls(platform, settings)
    flow.consumed_at = now
    db.commit()

    claims = _verify_lti_token(
        values["id_token"], platform, flow, await _read_jwks(platform, settings)
    )
    binding = db.scalar(
        select(LtiIdentityBinding).where(
            LtiIdentityBinding.platform_id == platform.id,
            LtiIdentityBinding.subject == claims["sub"],
            LtiIdentityBinding.active.is_(True),
        )
    )
    if binding is None:
        raise HTTPException(status_code=403, detail="LTI identity is not linked")
    user = db.get(User, binding.user_id)
    if user is None or not user.active:
        raise HTTPException(status_code=403, detail="LTI identity is not available")
    role = _local_role(claims[f"{LTI_CLAIM}roles"])
    if role is None:
        raise HTTPException(status_code=403, detail="LTI role is not supported")
    context_id = claims[f"{LTI_CLAIM}context"]["id"]
    resource_link_id = claims[f"{LTI_CLAIM}resource_link"]["id"]
    mapping = db.scalar(
        select(LmsCourseAssignmentMapping).where(
            LmsCourseAssignmentMapping.platform_id == platform.id,
            LmsCourseAssignmentMapping.lti_context_id == context_id,
            LmsCourseAssignmentMapping.lti_resource_link_id == resource_link_id,
            LmsCourseAssignmentMapping.active.is_(True),
        )
    )
    if mapping is None:
        raise HTTPException(status_code=403, detail="LTI resource is not mapped")
    permission = db.scalar(
        select(LtiCoursePermission.id).where(
            LtiCoursePermission.platform_id == platform.id,
            LtiCoursePermission.course_id == mapping.local_course_id,
            LtiCoursePermission.active.is_(True),
        )
    )
    assignment_version = db.get(AssignmentVersion, mapping.assignment_version_id)
    assignment = (
        db.get(Assignment, assignment_version.assignment_id) if assignment_version else None
    )
    if (
        permission is None
        or assignment_version is None
        or assignment is None
        or assignment.course_id != mapping.local_course_id
        or assignment_version.state != "published"
    ):
        raise HTTPException(status_code=403, detail="LTI resource is not available")
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.course_id == mapping.local_course_id,
            Enrollment.user_id == user.id,
            Enrollment.role == role,
            Enrollment.active.is_(True),
        )
    )
    if enrollment is None:
        raise HTTPException(status_code=403, detail="course membership is not active")

    if role == "lecturer":
        destination = f"/courses/{mapping.local_course_id}"
    else:
        submission = _student_submission(db, assignment.id, user.id)
        if submission is None:
            raise HTTPException(status_code=404, detail="feedback is not available")
        destination = f"/feedback/{submission.id}"

    redirect = RedirectResponse(url=destination, status_code=303)
    session, _csrf = issue_session(
        db, user, settings, redirect, request, audit_action="auth.lti_login", commit=False
    )
    db.add(
        LtiSessionBinding(
            session_id=session.id,
            platform_id=platform.id,
            identity_binding_id=binding.id,
            mapping_id=mapping.id,
            mapping_revision=mapping.revision,
        )
    )
    db.add(
        AuditEvent(
            actor_user_id=user.id,
            action="lti.launch.accepted",
            object_type="lms_mapping",
            object_id=mapping.id,
            new_version_refs_json={"mapping_revision": mapping.revision, "role": role},
            request_id=request.state.request_id,
        )
    )
    db.commit()
    redirect.delete_cookie(
        CORRELATION_COOKIE,
        path=urlsplit(settings.lti_callback_url).path,
        secure=True,
        httponly=True,
        samesite="none",
    )
    redirect.headers["Cache-Control"] = "no-store"
    return redirect
