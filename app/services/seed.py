from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.enums import SourceType, TrustLevel, UserRole
from app.models.organization import Organization
from app.models.source import Source
from app.models.user import User

settings = get_settings()

COMMUNITY_SOURCE_SEEDS = (
    {
        "name": "Reddit r/electricvehicles",
        "url": "https://www.reddit.com/r/electricvehicles/",
        "source_type": SourceType.reddit,
        "category": "커뮤니티/현장",
    },
    {
        "name": "Reddit r/evcharging",
        "url": "https://www.reddit.com/r/evcharging/",
        "source_type": SourceType.reddit,
        "category": "커뮤니티/현장",
    },
    {
        "name": "Reddit r/OCPP",
        "url": "https://www.reddit.com/r/OCPP/",
        "source_type": SourceType.reddit,
        "category": "커뮤니티/현장",
    },
)


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

    for seed in COMMUNITY_SOURCE_SEEDS:
        exists = db.scalar(
            select(Source).where(
                Source.organization_id == org.id,
                Source.url == seed["url"],
            )
        )
        if exists:
            continue
        db.add(
            Source(
                organization_id=org.id,
                name=seed["name"],
                url=seed["url"],
                source_type=seed["source_type"],
                category=seed["category"],
                trust_level=TrustLevel.low,
                reliability_score=45,
                auto_publish=False,
                is_active=True,
            )
        )

    db.commit()
