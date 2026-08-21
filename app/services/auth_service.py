from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token_value,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.enums import AccountApprovalStatus, UserRole
from app.models.organization import Organization
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserRead,
)

settings = get_settings()


class AuthService:
    def __init__(self, db: Session):
        self.db = db

    def register(self, data: RegisterRequest) -> RegisterResponse:
        existing = self.db.scalar(select(User).where(User.email == data.email))
        if existing:
            raise BadRequestError("Email already registered")

        org = self.db.scalar(select(Organization).limit(1))
        if not org:
            org = Organization(name="MotrexEV", industry="EV Charging")
            self.db.add(org)
            self.db.flush()

        user = User(
            organization_id=org.id,
            email=data.email,
            password_hash=hash_password(data.password),
            name=data.name,
            role=UserRole.viewer,
            approval_status=AccountApprovalStatus.pending,
            is_active=False,
        )
        self.db.add(user)
        self.db.commit()

        return RegisterResponse(
            message="가입 신청이 접수되었습니다. 편집장 승인 후 로그인할 수 있습니다.",
            status="pending",
        )

    def login(self, data: LoginRequest) -> TokenResponse:
        user = self.db.scalar(select(User).where(User.email == data.email))
        if not user or not user.password_hash or not verify_password(data.password, user.password_hash):
            raise BadRequestError("Invalid email or password")
        if user.approval_status == AccountApprovalStatus.pending:
            raise BadRequestError("Account pending approval")
        if user.approval_status == AccountApprovalStatus.rejected:
            raise BadRequestError("Account registration rejected")
        if not user.is_active:
            raise BadRequestError("Account is inactive")
        return self._issue_tokens(user)

    def refresh(self, raw_refresh_token: str) -> TokenResponse:
        token_row = self._get_active_refresh_token(raw_refresh_token)
        user = token_row.user
        if not user or not user.is_active or user.approval_status != AccountApprovalStatus.approved:
            raise UnauthorizedError("Invalid refresh token")

        # Rotate: revoke current token, issue a new pair.
        now = datetime.now(timezone.utc)
        token_row.revoked_at = now
        self.db.flush()
        response = self._issue_tokens(user, replaced_from=token_row)
        return response

    def logout(self, raw_refresh_token: str | None) -> None:
        if not raw_refresh_token:
            return
        token_hash = hash_refresh_token(raw_refresh_token)
        token_row = self.db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        if token_row and token_row.revoked_at is None:
            token_row.revoked_at = datetime.now(timezone.utc)
            self.db.commit()

    def me(self, user: User) -> UserRead:
        return UserRead.model_validate(user)

    def _issue_tokens(self, user: User, replaced_from: RefreshToken | None = None) -> TokenResponse:
        access = create_access_token(str(user.id), {"role": user.role.value})
        raw_refresh, expires_at = create_refresh_token_value()
        refresh_row = RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh),
            expires_at=expires_at,
        )
        self.db.add(refresh_row)
        self.db.flush()
        if replaced_from is not None:
            replaced_from.replaced_by_id = refresh_row.id
        self.db.commit()
        return TokenResponse(
            access_token=access,
            refresh_token=raw_refresh,
            expires_in=settings.jwt_access_token_expire_minutes * 60,
        )

    def _get_active_refresh_token(self, raw_refresh_token: str) -> RefreshToken:
        token_hash = hash_refresh_token(raw_refresh_token)
        token_row = self.db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        if not token_row:
            raise UnauthorizedError("Invalid refresh token")
        if token_row.revoked_at is not None:
            # Reuse of a revoked token — revoke the whole user chain defensively.
            self._revoke_user_tokens(token_row.user_id)
            raise UnauthorizedError("Invalid refresh token")
        now = datetime.now(timezone.utc)
        expires = token_row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= now:
            token_row.revoked_at = now
            self.db.commit()
            raise UnauthorizedError("Refresh token expired")
        return token_row

    def _revoke_user_tokens(self, user_id) -> None:
        now = datetime.now(timezone.utc)
        rows = self.db.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
        ).all()
        for row in rows:
            row.revoked_at = now
        self.db.commit()
