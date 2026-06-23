from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.enums import AccountApprovalStatus, UserRole
from app.models.organization import Organization
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, RegisterResponse, TokenResponse, UserRead
from app.core.security import create_access_token, hash_password, verify_password


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
        if not user or not verify_password(data.password, user.password_hash):
            raise BadRequestError("Invalid email or password")
        if user.approval_status == AccountApprovalStatus.pending:
            raise BadRequestError("Account pending approval")
        if user.approval_status == AccountApprovalStatus.rejected:
            raise BadRequestError("Account registration rejected")
        if not user.is_active:
            raise BadRequestError("Account is inactive")
        token = create_access_token(str(user.id), {"role": user.role.value})
        return TokenResponse(access_token=token)

    def me(self, user: User) -> UserRead:
        return UserRead.model_validate(user)
