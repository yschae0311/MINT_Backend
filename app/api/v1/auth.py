from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.auth import (
    LogoutRequest,
    OidcConfigResponse,
    OidcLoginRequest,
    RefreshRequest,
    TokenResponse,
    UserRead,
)
from app.services.auth_service import AuthService
from app.services.keycloak_service import KeycloakAuthService, oidc_config
from app.services.membership_service import MembershipService

router = APIRouter()


@router.get("/oidc/config", response_model=OidcConfigResponse)
def get_oidc_config():
    return oidc_config()


@router.post("/oidc", response_model=TokenResponse)
def oidc_login(data: OidcLoginRequest, db: Session = Depends(get_db)):
    return KeycloakAuthService(db).login(
        access_token=data.access_token,
        code=data.code,
        redirect_uri=data.redirect_uri,
        code_verifier=data.code_verifier,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(data: RefreshRequest, db: Session = Depends(get_db)):
    return AuthService(db).refresh(data.refresh_token)


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return MembershipService(db).to_user_read(user)


@router.post("/logout")
def logout(
    data: LogoutRequest = Body(default_factory=LogoutRequest),
    db: Session = Depends(get_db),
):
    AuthService(db).logout(data.refresh_token)
    config = oidc_config()
    return {
        "message": "Logged out",
        "end_session_endpoint": config.end_session_endpoint,
        "client_id": config.client_id,
    }
