from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.enums import AccountApprovalStatus, SourceType, TrustLevel, UserRole
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
        # 잡담판 — EV 게이트만으로는 노이즈가 큼. 기본 비활성(관리자가 필요 시 켜기).
        "is_active": False,
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


# 보조금·사업자 선정·고시 등 공식 정책 소스 (중요 파이프라인 / high trust)
# 기후에너지환경부 RSS: https://www.mcee.go.kr/home/web/index.do?menuId=447
MCEE_RSS_BASE = "https://www.mcee.go.kr"
TRUSTED_POLICY_SOURCE_SEEDS = (
    {
        "name": "정책브리핑 정책뉴스",
        "url": "https://www.korea.kr/rss/policy.xml",
        "source_type": SourceType.rss,
        "category": "정책/규제",
        "reliability_score": 95,
        # 범정부 정책 RSS — EV 외 잡글이 많음. 기본 비활성.
        "is_active": False,
    },
    {
        "name": "정책브리핑 부처 브리핑",
        "url": "https://www.korea.kr/rss/ebriefing.xml",
        "source_type": SourceType.rss,
        "category": "정책/규제",
        "reliability_score": 96,
        "is_active": False,
    },
    {
        "name": "기후에너지환경부 전기차·충전 공지",
        "url": f"{MCEE_RSS_BASE}/home/web/board/list.do?boardMasterId=29",
        "source_type": SourceType.notice_page,
        "category": "정책/규제",
        "reliability_score": 98,
    },
    {
        "name": "기후에너지환경부 공지·공고",
        "url": f"{MCEE_RSS_BASE}/home/web/board/rss.do?menuId=290&boardMasterId=39",
        "source_type": SourceType.rss,
        "category": "정책/규제",
        "reliability_score": 94,
    },
    {
        "name": "기후에너지환경부 보도·해명자료",
        "url": f"{MCEE_RSS_BASE}/home/web/board/rss.do?menuId=286&boardMasterId=1",
        "source_type": SourceType.rss,
        "category": "정책/규제",
        "reliability_score": 93,
    },
    {
        "name": "기후에너지환경부 환경정책",
        "url": f"{MCEE_RSS_BASE}/home/web/policy_data/rss.do?menuId=92",
        "source_type": SourceType.rss,
        "category": "정책/규제",
        "reliability_score": 92,
    },
    {
        "name": "기후에너지환경부 고시·훈령·예규",
        "url": f"{MCEE_RSS_BASE}/home/web/law/rss.do?menuId=71&condition.typeCode=admrul",
        "source_type": SourceType.rss,
        "category": "정책/규제",
        "reliability_score": 97,
    },
    {
        "name": "기후에너지환경부 현행법령",
        "url": f"{MCEE_RSS_BASE}/home/web/law/rss.do?menuId=70&condition.typeCode=law",
        "source_type": SourceType.rss,
        "category": "정책/규제",
        "reliability_score": 90,
    },
)

AUTONOMOUS_SOURCE_SEEDS = (
    {
        "name": "국토교통부 보도자료",
        "url": "https://www.molit.go.kr/USR/NEWS/m_71/lst.jsp",
        "source_type": SourceType.news_page,
        "category": "자율주행 정책",
        "reliability_score": 92,
        "edition_slug": "autonomous",
    },
    {
        "name": "Autonomous Vehicle International",
        "url": "https://www.autonomousvehicleinternational.com/feed",
        "source_type": SourceType.rss,
        "category": "자율주행 기술",
        "reliability_score": 80,
        "edition_slug": "autonomous",
    },
)

# 구 환경부(me.go.kr) HTML 소스 → 기후에너지환경부(mcee.go.kr) 마이그레이션
MCEE_SOURCE_MIGRATIONS = (
    {
        "old_url": "https://www.me.go.kr/home/web/board/list.do?boardMasterId=29",
        "name": "기후에너지환경부 전기차·충전 공지",
        "url": f"{MCEE_RSS_BASE}/home/web/board/list.do?boardMasterId=29",
        "source_type": SourceType.notice_page,
    },
    {
        "old_url": "https://www.me.go.kr/home/web/board/list.do?boardMasterId=39",
        "name": "기후에너지환경부 공지·공고",
        "url": f"{MCEE_RSS_BASE}/home/web/board/rss.do?menuId=290&boardMasterId=39",
        "source_type": SourceType.rss,
    },
    {
        "old_url": "https://www.me.go.kr/home/web/board/list.do?boardMasterId=67",
        "name": "기후에너지환경부 고시·훈령·예규",
        "url": f"{MCEE_RSS_BASE}/home/web/law/rss.do?menuId=71&condition.typeCode=admrul",
        "source_type": SourceType.rss,
    },
)


def _migrate_mcee_sources(db: Session, organization_id) -> None:
    """me.go.kr HTML 목록 소스를 mcee.go.kr RSS/목록 URL로 갱신."""
    for migration in MCEE_SOURCE_MIGRATIONS:
        row = db.scalar(
            select(Source).where(
                Source.organization_id == organization_id,
                Source.url == migration["old_url"],
            )
        )
        if not row:
            continue
        conflict = db.scalar(
            select(Source).where(
                Source.organization_id == organization_id,
                Source.url == migration["url"],
                Source.id != row.id,
            )
        )
        if conflict:
            row.is_active = False
            continue
        row.name = migration["name"]
        row.url = migration["url"]
        row.source_type = migration["source_type"]


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


def _deactivate_broken_ev_portal_source(db: Session, organization_id) -> None:
    """ev.or.kr 공지 목록은 JS 렌더링이라 서버 크롤이 불가 — 비활성화."""
    row = db.scalar(
        select(Source).where(
            Source.organization_id == organization_id,
            Source.url == "https://ev.or.kr/nportal/partcptn/initNoticeAction.do",
            Source.is_active.is_(True),
        )
    )
    if row:
        row.is_active = False


_BROAD_NOISE_SOURCE_URLS = (
    "https://www.korea.kr/rss/policy.xml",
    "https://www.korea.kr/rss/ebriefing.xml",
    "https://www.clien.net/service/board/park",
)


def _deactivate_broad_noise_sources(db: Session, organization_id) -> None:
    """범정부 RSS·잡담판 등 EV 초점을 흐리는 소스는 기본 비활성."""
    rows = db.scalars(
        select(Source).where(
            Source.organization_id == organization_id,
            Source.url.in_(_BROAD_NOISE_SOURCE_URLS),
            Source.is_active.is_(True),
        )
    ).all()
    for row in rows:
        row.is_active = False


def _seed_sources(db: Session, organization_id, seeds: tuple, *, low_trust: bool) -> None:
    for seed in seeds:
        exists = db.scalar(
            select(Source).where(
                Source.organization_id == organization_id,
                Source.url == seed["url"],
            )
        )
        if exists:
            continue
        db.add(
            Source(
                organization_id=organization_id,
                name=seed["name"],
                url=seed["url"],
                source_type=seed["source_type"],
                category=seed["category"],
                trust_level=TrustLevel.low if low_trust else TrustLevel.high,
                reliability_score=seed.get("reliability_score", 45 if low_trust else 85),
                auto_publish=not low_trust,
                is_active=seed.get("is_active", True),
            )
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
            approval_status=AccountApprovalStatus.approved,
            is_active=True,
        )
        db.add(user)

    _deactivate_reddit_sources(db, org.id)
    _deactivate_broken_ev_portal_source(db, org.id)
    _deactivate_broad_noise_sources(db, org.id)
    _migrate_mcee_sources(db, org.id)

    _seed_sources(db, org.id, COMMUNITY_SOURCE_SEEDS, low_trust=True)
    _seed_sources(db, org.id, TRUSTED_POLICY_SOURCE_SEEDS, low_trust=False)
    _seed_sources(db, org.id, AUTONOMOUS_SOURCE_SEEDS, low_trust=False)

    from app.services.personalization_service import TaxonomyService
    from app.services.edition_service import AUTONOMOUS_SLUG, EV_SLUG

    TaxonomyService(db).ensure_defaults(org.id)
    _tag_seed_sources(db, org.id)
    db.commit()


def _tag_seed_sources(db: Session, organization_id) -> None:
    from app.models.edition import Edition, SourceEdition
    from app.services.edition_service import AUTONOMOUS_SLUG, EV_SLUG

    editions = {
        row.slug: row
        for row in db.scalars(select(Edition).where(Edition.organization_id == organization_id)).all()
    }
    ev = editions.get(EV_SLUG)
    av = editions.get(AUTONOMOUS_SLUG)
    av_urls = {seed["url"] for seed in AUTONOMOUS_SOURCE_SEEDS}
    sources = db.scalars(select(Source).where(Source.organization_id == organization_id)).all()
    for source in sources:
        exists = db.scalar(select(SourceEdition).where(SourceEdition.source_id == source.id))
        if exists:
            continue
        target = av if source.url in av_urls else ev
        if not target:
            continue
        db.add(SourceEdition(source_id=source.id, edition_id=target.id))
