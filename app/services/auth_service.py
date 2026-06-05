from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError
from app.core.security import create_access_token, hash_password, verify_password
from app.models.enums import UserRole
from app.models.organization import Organization
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserRead


class AuthService:
    def __init__(self, db: Session):
        self.db = db

    def register(self, data: RegisterRequest) -> TokenResponse:
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
            role=UserRole.member,
            is_active=True,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        token = create_access_token(str(user.id), {"role": user.role.value})
        return TokenResponse(access_token=token)

    def login(self, data: LoginRequest) -> TokenResponse:
        user = self.db.scalar(select(User).where(User.email == data.email))
        if not user or not verify_password(data.password, user.password_hash):
            raise BadRequestError("Invalid email or password")
        if not user.is_active:
            raise BadRequestError("Account is inactive")
        token = create_access_token(str(user.id), {"role": user.role.value})
        return TokenResponse(access_token=token)

    def me(self, user: User) -> UserRead:
        return UserRead.model_validate(user)
