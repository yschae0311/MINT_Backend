from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.enums import SourceType, TrustLevel, UserRole
from app.models.organization import Organization
from app.models.source import Source
from app.models.user import User

settings = get_settings()

# Reddit는 EC2 등 서버 IP에서 차단됨 — 기본 시드는 국내 포럼(HTML 크롤) 위주.
COMMUNITY_SOURCE_SEEDS = (
    {
        "name": "클리앙 모두의공원",
        "url": "https://www.clien.net/service/board/park",
        "source_type": SourceType.community_forum,
        "category": "커뮤니티/현장",
    },
    {
        "name": "클리앙 사용기",
        "url": "https://www.clien.net/service/board/use",
        "source_type": SourceType.community_forum,
        "category": "커뮤니티/현장",
    },
    {
        "name": "보배드림 자동차뉴스",
        "url": "https://www.bobaedream.co.kr/list?code=cnews",
        "source_type": SourceType.community_forum,
        "category": "커뮤니티/현장",
    },
    {
        "name": "보배드림 국산차",
        "url": "https://www.bobaedream.co.kr/list?code=national",
        "source_type": SourceType.community_forum,
        "category": "커뮤니티/현장",
    },
    {
        "name": "보배드림 수입차",
        "url": "https://www.bobaedream.co.kr/list?code=import",
        "source_type": SourceType.community_forum,
        "category": "커뮤니티/현장",
    },
)


def _deactivate_reddit_sources(db: Session, organization_id) -> None:
    """Reddit listing is blocked from most server IPs; keep rows but disable auto-crawl."""
    rows = db.scalars(
        select(Source).where(
            Source.organization_id == organization_id,
            Source.source_type == SourceType.reddit,
            Source.is_active.is_(True),
        )
    ).all()
    for row in rows:
        row.is_active = False


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

    _deactivate_reddit_sources(db, org.id)

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
