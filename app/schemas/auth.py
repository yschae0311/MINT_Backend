from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import AccountApprovalStatus, UserRole
from app.schemas.common import ORMBase


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    id_token: str | None = Field(
        default=None,
        description="Keycloak ID token for RP-initiated logout. Not used as an API credential.",
    )


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class RegisterResponse(BaseModel):
    message: str
    status: str = "pending"


class OidcLoginRequest(BaseModel):
    access_token: str | None = None
    code: str | None = None
    redirect_uri: str | None = None
    code_verifier: str | None = None
    username: str | None = None
    password: str | None = None


class OidcConfigResponse(BaseModel):
    configured: bool
    issuer: str | None = None
    client_id: str | None = None
    authorization_endpoint: str | None = None
    end_session_endpoint: str | None = None


class UserEditionMembership(BaseModel):
    id: UUID
    name: str
    slug: str
    is_editor: bool = False


class UserRead(ORMBase):
    id: UUID
    organization_id: UUID
    email: str
    name: str
    role: UserRole
    approval_status: AccountApprovalStatus
    is_active: bool
    editions: list[UserEditionMembership] = Field(default_factory=list)
