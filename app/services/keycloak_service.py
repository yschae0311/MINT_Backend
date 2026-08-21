from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from jose import JWTError, jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ServiceUnavailableError, UnauthorizedError
from app.models.enums import AccountApprovalStatus, UserRole
from app.models.organization import Organization
from app.models.user import User
from app.schemas.auth import OidcConfigResponse, TokenResponse
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

_JWKS_TTL_SEC = 300
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _issuer() -> str:
    return get_settings().keycloak_issuer.strip().rstrip("/")


def _client_id() -> str:
    return get_settings().keycloak_client_id.strip()


def _require_configured() -> None:
    if not get_settings().keycloak_configured:
        raise ServiceUnavailableError("Keycloak SSO가 설정되지 않았습니다.")


def oidc_config() -> OidcConfigResponse:
    settings = get_settings()
    if not settings.keycloak_configured:
        return OidcConfigResponse(configured=False)
    issuer = _issuer()
    return OidcConfigResponse(
        configured=True,
        issuer=issuer,
        client_id=_client_id(),
        authorization_endpoint=f"{issuer}/protocol/openid-connect/auth",
        end_session_endpoint=f"{issuer}/protocol/openid-connect/logout",
    )


def _fetch_jwks(issuer: str) -> dict[str, Any]:
    now = time.monotonic()
    cached = _jwks_cache.get(issuer)
    if cached and now - cached[0] < _JWKS_TTL_SEC:
        return cached[1]
    url = f"{issuer}/protocol/openid-connect/certs"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch Keycloak JWKS: %s", exc)
        if cached:
            return cached[1]
        raise ServiceUnavailableError("Keycloak 인증서 조회에 실패했습니다.") from exc
    _jwks_cache[issuer] = (now, payload)
    return payload


def _jwk_for_token(token: str, jwks: dict[str, Any]) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise UnauthorizedError("Invalid access token") from exc
    kid = header.get("kid")
    keys = jwks.get("keys") or []
    if kid:
        for key in keys:
            if key.get("kid") == kid:
                return key
    if len(keys) == 1:
        return keys[0]
    raise UnauthorizedError("Invalid access token")


def _audience_ok(claims: dict[str, Any], client_id: str) -> bool:
    aud = claims.get("aud")
    if isinstance(aud, str) and aud == client_id:
        return True
    if isinstance(aud, list) and client_id in aud:
        return True
    return claims.get("azp") == client_id


def verify_access_token(access_token: str) -> dict[str, Any]:
    _require_configured()
    issuer = _issuer()
    client_id = _client_id()
    jwks = _fetch_jwks(issuer)
    key = _jwk_for_token(access_token, jwks)
    try:
        claims = jwt.decode(
            access_token,
            key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384"],
            issuer=issuer,
            options={"verify_aud": False},
        )
    except JWTError as exc:
        raise UnauthorizedError("Invalid access token") from exc
    if not _audience_ok(claims, client_id):
        raise UnauthorizedError("Invalid access token")
    return claims


def fetch_userinfo(access_token: str) -> dict[str, Any]:
    issuer = _issuer()
    url = f"{issuer}/protocol/openid-connect/userinfo"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, headers={"Authorization": f"Bearer {access_token}"})
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        logger.warning("Keycloak UserInfo failed: %s", exc)
        raise UnauthorizedError("UserInfo lookup failed") from exc


def exchange_code(
    code: str, redirect_uri: str, code_verifier: str | None = None
) -> tuple[str, str | None]:
    _require_configured()
    verifier = (code_verifier or "").strip()
    if not verifier:
        raise BadRequestError("public 클라이언트는 PKCE code_verifier가 필요합니다.")
    issuer = _issuer()
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": _client_id(),
        "code_verifier": verifier,
    }
    url = f"{issuer}/protocol/openid-connect/token"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, data=payload)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Keycloak token exchange failed: %s", exc)
        raise UnauthorizedError("Authorization code exchange failed") from exc
    access_token = body.get("access_token")
    if not access_token:
        raise UnauthorizedError("Authorization code exchange failed")
    id_token = body.get("id_token")
    return access_token, str(id_token) if id_token else None


def _token_error(response: httpx.Response, fallback: str) -> None:
    err = ""
    try:
        err = str((response.json() or {}).get("error") or "")
    except Exception:
        err = ""
    if err == "invalid_grant":
        raise UnauthorizedError("아이디 또는 비밀번호가 올바르지 않습니다.")
    if err == "unauthorized_client":
        raise ServiceUnavailableError(
            "Keycloak 클라이언트에서 Direct access grants를 활성화해 주세요."
        )
    logger.warning("Keycloak token request failed: %s %s", response.status_code, err)
    raise UnauthorizedError(fallback)


def password_grant(username: str, password: str) -> tuple[str, str | None]:
    _require_configured()
    user = username.strip()
    if not user or not password:
        raise BadRequestError("아이디와 비밀번호를 입력해 주세요.")
    issuer = _issuer()
    payload = {
        "grant_type": "password",
        "client_id": _client_id(),
        "username": user,
        "password": password,
        "scope": "openid email profile",
    }
    url = f"{issuer}/protocol/openid-connect/token"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, data=payload)
            if response.status_code >= 400:
                _token_error(response, "Keycloak 로그인에 실패했습니다.")
            body = response.json()
    except (UnauthorizedError, ServiceUnavailableError, BadRequestError):
        raise
    except httpx.HTTPError as exc:
        logger.warning("Keycloak password grant failed: %s", exc)
        raise UnauthorizedError("Keycloak 로그인에 실패했습니다.") from exc
    access_token = body.get("access_token")
    if not access_token:
        raise UnauthorizedError("Keycloak 로그인에 실패했습니다.")
    id_token = body.get("id_token")
    return access_token, str(id_token) if id_token else None


def extract_roles(claims: dict[str, Any]) -> set[str]:
    roles: set[str] = set()
    realm = claims.get("realm_access") or {}
    roles.update(realm.get("roles") or [])
    resource = claims.get("resource_access") or {}
    for entry in resource.values():
        if isinstance(entry, dict):
            roles.update(entry.get("roles") or [])
    return {str(role) for role in roles}


def _display_name(claims: dict[str, Any]) -> str:
    name = (claims.get("name") or "").strip()
    if name:
        return name[:128]
    given = (claims.get("given_name") or "").strip()
    family = (claims.get("family_name") or "").strip()
    combined = f"{family} {given}".strip() or (claims.get("preferred_username") or "").strip()
    return (combined or "User")[:128]


def _email_from(claims: dict[str, Any]) -> str:
    return (claims.get("email") or "").strip().lower()


class KeycloakAuthService:
    def __init__(self, db: Session):
        self.db = db

    def login(
        self,
        *,
        access_token: str | None = None,
        code: str | None = None,
        redirect_uri: str | None = None,
        code_verifier: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> TokenResponse:
        _require_configured()
        token = (access_token or "").strip()
        id_token: str | None = None
        if not token:
            if (username or "").strip() and password:
                token, id_token = password_grant(username, password)
            elif (code or "").strip() and (redirect_uri or "").strip():
                token, id_token = exchange_code(code.strip(), redirect_uri.strip(), code_verifier)
            else:
                raise BadRequestError("아이디와 비밀번호를 입력해 주세요.")
        claims = verify_access_token(token)
        sub = str(claims.get("sub") or "").strip()
        if not sub:
            raise UnauthorizedError("Invalid access token")

        user = self.db.scalar(select(User).where(User.keycloak_sub == sub))
        email = _email_from(claims)
        name = _display_name(claims)
        if user is None:
            info = fetch_userinfo(token)
            email = _email_from(info) or email
            name = _display_name(info) if _display_name(info) != "User" else name
            if info.get("sub") and str(info.get("sub")) != sub:
                raise UnauthorizedError("Invalid access token")
        elif not email:
            info = fetch_userinfo(token)
            email = _email_from(info) or email
            if info.get("name"):
                name = _display_name(info)

        if not email:
            raise BadRequestError("이메일 정보가 없어 로그인할 수 없습니다.")

        roles = extract_roles(claims)
        is_superadmin = get_settings().keycloak_admin_role in roles
        role = UserRole.admin if is_superadmin else UserRole.viewer

        if user is None:
            user = self.db.scalar(select(User).where(func.lower(User.email) == email))
            if user is not None:
                if user.keycloak_sub and user.keycloak_sub != sub:
                    raise BadRequestError("이미 다른 계정에 연결된 이메일입니다.")
                user.keycloak_sub = sub

        if user is None:
            org = self.db.scalar(select(Organization).limit(1))
            if not org:
                org = Organization(name="MotrexEV", industry="EV Charging")
                self.db.add(org)
                self.db.flush()
            user = User(
                organization_id=org.id,
                email=email,
                password_hash=None,
                name=name,
                keycloak_sub=sub,
                role=role,
                approval_status=AccountApprovalStatus.approved,
                is_active=True,
            )
            self.db.add(user)
            self.db.flush()
        else:
            user.email = email
            user.name = name
            user.keycloak_sub = sub
            user.role = role
            user.approval_status = AccountApprovalStatus.approved

        if not user.is_active:
            raise BadRequestError("Account is inactive")
        issued = AuthService(self.db)._issue_tokens(user)
        issued.id_token = id_token
        return issued
