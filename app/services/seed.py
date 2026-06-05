from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.organization import Organization
from app.models.user import User

settings = get_settings()


def seed_defaults(db: Session) -> None:
    org = db.scalar(select(Organization).limit(1))
    if not org:
        org = Organization(name="MotrexEV", industry="EV Charging")
        db.add(org)
        db.flush()

    existing = db.scalar(select(User).where(User.email == settings.seed_admin_email))
    if not existing:
        user = User(
            organization_id=org.id,
            email=settings.seed_admin_email,
            password_hash=hash_password(settings.seed_admin_password),
            name=settings.seed_admin_name,
            role=UserRole.admin,
            is_active=True,
        )
        db.add(user)
    db.commit()
