from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import BadRequestError, NotFoundError
from app.core.config import get_settings
from app.models.ai_output import AIOutput
from app.models.enums import (
    BoardType,
    Importance,
    KeywordMatchMethod,
    KeywordScope,
    KeywordStatus,
    PostStatus,
    ReviewQueueReason,
    ReviewQueueStatus,
    SourceType,
)
from app.models.personalization import (
    Keyword,
    NewsCategory,
    PersonalReport,
    PersonalReportItem,
    PersonalReportView,
    PostKeyword,
    ReviewQueueItem,
    UserCategorySubscription,
    UserKeywordSubscription,
)
from app.models.post import Post
from app.models.source import Source
from app.models.user import User
from app.schemas.personalization import (
    KeywordRead,
    MatchedKeyword,
    NewsItem,
    NewsPage,
    PersonalReportItemRead,
    PersonalReportRead,
    ReviewQueueRead,
    TopicHubRead,
)
from app.search.post_content import PostContent, get_post_content, mget_post_contents, legacy_pg_content_enabled, sync_post_metadata
from app.search.post_search_query import PostSearchFilters, load_posts_ordered, search_posts
from app.services.community_sources import COMMUNITY_SOURCE_TYPES, is_community_source_type
from app.services.llm_client import get_llm_client

KST = ZoneInfo("Asia/Seoul")
_KEYWORD_AUTO_ACTIVE_MIN = 0.6
_MIN_NEW_KEYWORD_CONFIDENCE = 0.72
_MAX_KEYWORDS_PER_POST = 5
_CUSTOM_KEYWORD_SCAN_DAYS = 90
DEFAULT_CATEGORIES = (
    "정책/규제",
    "충전 인프라",
    "CSMS/OCPP",
    "배터리/에너지",
    "시장/기업",
    "기술",
    "커뮤니티/현장",
    "기타",
)
DEFAULT_KEYWORDS = {
    "정책/규제": ("보조금", "환경부", "전기차 정책", "충전 정책"),
    "충전 인프라": ("충전 인프라", "급속 충전", "완속 충전", "충전소"),
    "CSMS/OCPP": ("OCPP", "CSMS", "CPO", "eMSP", "Plug & Charge", "ISO 15118"),
    "배터리/에너지": ("배터리", "ESS", "V2G", "전력망"),
    "시장/기업": ("충전 사업자", "완성차", "시장 동향"),
    "기술": ("충전 기술", "로밍", "결제"),
}
DEFAULT_AV_CATEGORIES = (
    "자율주행 정책",
    "자율주행 기술",
    "자율주행 시장",
    "자율주행 안전",
)
DEFAULT_AV_KEYWORDS = {
    "자율주행 정책": ("자율주행 규제", "운행 허가", "레벨4"),
    "자율주행 기술": ("자율주행", "ADAS", "라이다", "로보택시"),
    "자율주행 시장": ("웨이모", "자율주행 스타트업"),
    "자율주행 안전": ("자율주행 사고", "운전자 모니터링"),
}
_DISCOVERED_CATEGORY_SORT_BASE = 1000


def normalize_keyword(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip().casefold()
    return re.sub(r"\s+", " ", value)


_FEATURED_CATEGORY_NAMES = frozenset(normalize_keyword(name) for name in DEFAULT_KEYWORDS)
_DEFAULT_CATEGORY_NORMALIZED = frozenset(normalize_keyword(name) for name in DEFAULT_CATEGORIES)


def keyword_status_for_confidence(confidence: float) -> KeywordStatus:
    return (
        KeywordStatus.active
        if confidence >= _KEYWORD_AUTO_ACTIVE_MIN
        else KeywordStatus.candidate
    )


def _keyword_terms(keyword: Keyword) -> set[str]:
    terms = {keyword.normalized_name}
    for alias in keyword.aliases or []:
        normalized = normalize_keyword(alias)
        if normalized:
            terms.add(normalized)
    return terms


def resolve_existing_keyword(
    name: str,
    organization_keywords: list[Keyword],
    *,
    category_id: UUID | None = None,
) -> Keyword | None:
    normalized = normalize_keyword(name)
    if not normalized:
        return None

    for keyword in organization_keywords:
        if normalized in _keyword_terms(keyword):
            return keyword

    substring_matches: list[tuple[Keyword, int, bool]] = []
    for keyword in organization_keywords:
        if category_id and keyword.category_id != category_id:
            continue
        for term in _keyword_terms(keyword):
            if len(term) < 3:
                continue
            if term in normalized or normalized in term:
                substring_matches.append((keyword, len(term), keyword.is_curated))
    if substring_matches:
        substring_matches.sort(key=lambda item: (not item[2], -item[1]))
        return substring_matches[0][0]

    for keyword in organization_keywords:
        if not keyword.is_curated:
            continue
        for term in _keyword_terms(keyword):
            if len(term) < 4:
                continue
            if term in normalized or normalized in term:
                return keyword

    name_tokens = set(normalized.split())
    best: Keyword | None = None
    best_score = 0.0
    for keyword in organization_keywords:
        for term in _keyword_terms(keyword):
            term_tokens = set(term.split())
            if not term_tokens or not name_tokens:
                continue
            overlap = len(name_tokens & term_tokens) / max(len(name_tokens), len(term_tokens))
            if overlap >= 0.6 and overlap > best_score:
                best_score = overlap
                best = keyword
    return best


def add_keyword_alias(keyword: Keyword, alias: str) -> None:
    normalized = normalize_keyword(alias)
    if not normalized or normalized == keyword.normalized_name:
        return
    aliases = list(keyword.aliases or [])
    existing = {normalize_keyword(item) for item in aliases}
    if normalized in existing:
        return
    aliases.append(alias.strip()[:128])
    keyword.aliases = aliases[:20]


def keyword_merge_priority(keyword: Keyword) -> tuple[int, int, int, float]:
    created = keyword.created_at.timestamp() if keyword.created_at else 0.0
    return (
        1 if keyword.is_curated else 0,
        1 if keyword.status == KeywordStatus.active else 0,
        int(keyword.usage_count or 0),
        created,
    )


def find_duplicate_keyword_pairs(keywords: list[Keyword]) -> list[tuple[Keyword, Keyword]]:
    """Return (source, target) pairs where source should merge into target."""
    active = [
        keyword
        for keyword in keywords
        if keyword.scope == KeywordScope.organization
        and keyword.owner_user_id is None
        and keyword.status != KeywordStatus.archived
    ]
    consumed: set[UUID] = set()
    pairs: list[tuple[Keyword, Keyword]] = []
    for source in sorted(active, key=keyword_merge_priority):
        if source.id in consumed:
            continue
        pool = [keyword for keyword in active if keyword.id not in consumed and keyword.id != source.id]
        target = resolve_existing_keyword(
            source.name,
            pool,
            category_id=source.category_id,
        )
        if not target or target.id == source.id:
            continue
        if keyword_merge_priority(target) < keyword_merge_priority(source):
            source, target = target, source
        pairs.append((source, target))
        consumed.add(source.id)
    return pairs


class TaxonomyService:
    def __init__(self, db: Session):
        self.db = db

    def ensure_defaults(self, organization_id: UUID) -> None:
        from app.services.edition_service import AUTONOMOUS_SLUG, EV_SLUG, EditionService

        edition_svc = EditionService(self.db)
        edition_svc.ensure_defaults(organization_id)
        editions = {
            row.slug: row
            for row in edition_svc.list_editions(organization_id, active_only=False)
        }
        ev_edition = editions.get(EV_SLUG)
        av_edition = editions.get(AUTONOMOUS_SLUG)

        categories = {
            row.normalized_name: row
            for row in self.db.scalars(
                select(NewsCategory).where(NewsCategory.organization_id == organization_id)
            ).all()
        }
        for order, name in enumerate(DEFAULT_CATEGORIES):
            normalized = normalize_keyword(name)
            if normalized not in categories:
                row = NewsCategory(
                    organization_id=organization_id,
                    name=name,
                    normalized_name=normalized,
                    sort_order=order,
                    is_featured=normalized in _FEATURED_CATEGORY_NAMES,
                    edition_id=ev_edition.id if ev_edition else None,
                )
                self.db.add(row)
                self.db.flush()
                categories[normalized] = row
            else:
                row = categories[normalized]
                if normalized in _FEATURED_CATEGORY_NAMES and not row.is_featured:
                    row.is_featured = True
                if ev_edition and row.edition_id is None:
                    row.edition_id = ev_edition.id

        existing = {
            row.normalized_name: row
            for row in self.db.scalars(
                select(Keyword).where(
                    Keyword.organization_id == organization_id,
                    Keyword.scope == KeywordScope.organization,
                )
            ).all()
        }
        for category_name, names in DEFAULT_KEYWORDS.items():
            category = categories[normalize_keyword(category_name)]
            for name in names:
                normalized = normalize_keyword(name)
                row = existing.get(normalized)
                if row:
                    if not row.is_curated:
                        row.is_curated = True
                    if not row.category_id:
                        row.category_id = category.id
                    if ev_edition and row.edition_id is None:
                        row.edition_id = ev_edition.id
                        if row.is_curated:
                            row.is_featured = True
                    continue
                new_row = Keyword(
                    organization_id=organization_id,
                    category_id=category.id,
                    edition_id=ev_edition.id if ev_edition else None,
                    name=name,
                    normalized_name=normalized,
                    aliases=[],
                    scope=KeywordScope.organization,
                    status=KeywordStatus.active,
                    is_curated=True,
                    is_featured=True,
                )
                self.db.add(new_row)
                self.db.flush()
                existing[normalized] = new_row

        if av_edition:
            av_base = len(DEFAULT_CATEGORIES) + 10
            for order, name in enumerate(DEFAULT_AV_CATEGORIES):
                normalized = normalize_keyword(name)
                if normalized not in categories:
                    row = NewsCategory(
                        organization_id=organization_id,
                        name=name,
                        normalized_name=normalized,
                        sort_order=av_base + order,
                        is_featured=False,
                        edition_id=av_edition.id,
                    )
                    self.db.add(row)
                    self.db.flush()
                    categories[normalized] = row
                elif categories[normalized].edition_id is None:
                    categories[normalized].edition_id = av_edition.id
            for category_name, names in DEFAULT_AV_KEYWORDS.items():
                category = categories.get(normalize_keyword(category_name))
                if not category:
                    continue
                for name in names:
                    normalized = normalize_keyword(name)
                    row = existing.get(normalized)
                    if row:
                        if not row.is_curated:
                            row.is_curated = True
                        if not row.category_id:
                            row.category_id = category.id
                        if row.edition_id is None or row.edition_id != av_edition.id:
                            row.edition_id = av_edition.id
                        row.is_featured = True
                        if row.status == KeywordStatus.candidate:
                            row.status = KeywordStatus.active
                        continue
                    new_row = Keyword(
                        organization_id=organization_id,
                        category_id=category.id,
                        edition_id=av_edition.id,
                        name=name,
                        normalized_name=normalized,
                        aliases=[],
                        scope=KeywordScope.organization,
                        status=KeywordStatus.active,
                        is_curated=True,
                        is_featured=True,
                    )
                    self.db.add(new_row)
                    self.db.flush()
                    existing[normalized] = new_row
        self.db.flush()

    def list_categories(
        self, organization_id: UUID, *, edition_ids: set[UUID] | None = None
    ) -> list[NewsCategory]:
        q = (
            select(NewsCategory)
            .where(
                NewsCategory.organization_id == organization_id,
                NewsCategory.is_active.is_(True),
            )
            .order_by(
                NewsCategory.is_featured.desc(),
                NewsCategory.sort_order,
                NewsCategory.name,
            )
        )
        rows = list(self.db.scalars(q).all())
        if edition_ids is None:
            return rows
        if not edition_ids:
            return []
        return [
            row
            for row in rows
            if row.edition_id is None or row.edition_id in edition_ids
        ]

    def sync_discovered_categories(self, organization_id: UUID) -> int:
        """Import distinct post.category values into news_categories."""
        names = list(
            self.db.scalars(
                select(Post.category)
                .where(
                    Post.organization_id == organization_id,
                    Post.category.is_not(None),
                    Post.category != "",
                    Post.status.not_in([PostStatus.deleted, PostStatus.hidden]),
                )
                .distinct()
            ).all()
        )
        existing = {
            row.normalized_name: row
            for row in self.db.scalars(
                select(NewsCategory).where(NewsCategory.organization_id == organization_id)
            ).all()
        }
        max_order = max(
            (row.sort_order for row in existing.values()),
            default=len(DEFAULT_CATEGORIES),
        )
        created = 0
        for name in names:
            label = (name or "").strip()
            if not label:
                continue
            normalized = normalize_keyword(label)
            if normalized in existing:
                continue
            max_order += 1
            row = NewsCategory(
                organization_id=organization_id,
                name=label[:128],
                normalized_name=normalized[:128],
                sort_order=max_order,
                is_featured=False,
            )
            self.db.add(row)
            self.db.flush()
            existing[normalized] = row
            created += 1
        if created:
            self.db.flush()
        return created

    def get_or_create_category(
        self, organization_id: UUID, category_name: str
    ) -> NewsCategory:
        label = (category_name or "기타").strip() or "기타"
        normalized = normalize_keyword(label)
        row = self.db.scalar(
            select(NewsCategory).where(
                NewsCategory.organization_id == organization_id,
                NewsCategory.normalized_name == normalized,
            )
        )
        if row:
            return row
        max_order = self.db.scalar(
            select(func.max(NewsCategory.sort_order)).where(
                NewsCategory.organization_id == organization_id
            )
        )
        row = NewsCategory(
            organization_id=organization_id,
            name=label[:128],
            normalized_name=normalized[:128],
            sort_order=int(max_order or _DISCOVERED_CATEGORY_SORT_BASE) + 1,
            is_featured=False,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def post_counts_by_category(self, organization_id: UUID) -> dict[UUID, int]:
        categories = {
            row.normalized_name: row.id
            for row in self.db.scalars(
                select(NewsCategory).where(NewsCategory.organization_id == organization_id)
            ).all()
        }
        rows = self.db.execute(
            select(Post.category, func.count())
            .where(
                Post.organization_id == organization_id,
                Post.category.is_not(None),
                Post.status.not_in([PostStatus.deleted, PostStatus.hidden]),
            )
            .group_by(Post.category)
        ).all()
        counts: dict[UUID, int] = {}
        for name, count in rows:
            category_id = categories.get(normalize_keyword(name or ""))
            if category_id:
                counts[category_id] = int(count)
        return counts

    def keyword_counts_by_category(self, organization_id: UUID) -> dict[UUID, int]:
        rows = self.db.execute(
            select(Keyword.category_id, func.count())
            .where(
                Keyword.organization_id == organization_id,
                Keyword.category_id.is_not(None),
                Keyword.scope == KeywordScope.organization,
                Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
            )
            .group_by(Keyword.category_id)
        ).all()
        return {category_id: int(count) for category_id, count in rows if category_id}

    def list_featured_categories(self, organization_id: UUID) -> list[NewsCategory]:
        return list(
            self.db.scalars(
                select(NewsCategory)
                .where(
                    NewsCategory.organization_id == organization_id,
                    NewsCategory.is_active.is_(True),
                    NewsCategory.is_featured.is_(True),
                )
                .order_by(NewsCategory.sort_order, NewsCategory.name)
            ).all()
        )

    def curated_keyword_counts(self, organization_id: UUID) -> dict[UUID, int]:
        rows = self.db.execute(
            select(Keyword.category_id, func.count())
            .where(
                Keyword.organization_id == organization_id,
                Keyword.category_id.is_not(None),
                Keyword.is_curated.is_(True),
                Keyword.scope == KeywordScope.organization,
                Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
            )
            .group_by(Keyword.category_id)
        ).all()
        return {category_id: int(count) for category_id, count in rows if category_id}

    def set_featured_categories(
        self, organization_id: UUID, category_ids: list[UUID]
    ) -> list[NewsCategory]:
        unique_ids = list(dict.fromkeys(category_ids))
        allowed = list(
            self.db.scalars(
                select(NewsCategory).where(
                    NewsCategory.organization_id == organization_id,
                    NewsCategory.id.in_(unique_ids),
                    NewsCategory.is_active.is_(True),
                )
            ).all()
        )
        if len(allowed) != len(unique_ids):
            raise BadRequestError("선택할 수 없는 분야가 포함되어 있습니다.")
        featured_ids = {row.id for row in allowed}
        for row in self.list_categories(organization_id):
            row.is_featured = row.id in featured_ids
        self.db.commit()
        return self.list_featured_categories(organization_id)

    def list_keywords(
        self,
        user: User,
        *,
        include_discovered: bool = False,
    ) -> list[Keyword]:
        rows = list(
            self.db.scalars(
                select(Keyword)
                .outerjoin(NewsCategory, Keyword.category_id == NewsCategory.id)
                .where(
                    Keyword.organization_id == user.organization_id,
                    Keyword.status.in_(
                        [KeywordStatus.active, KeywordStatus.candidate]
                    ),
                    or_(
                        Keyword.scope == KeywordScope.organization,
                        Keyword.owner_user_id == user.id,
                    ),
                )
                .order_by(
                    NewsCategory.sort_order.nulls_last(),
                    NewsCategory.name.nulls_last(),
                    Keyword.is_curated.desc(),
                    Keyword.status,
                    Keyword.name,
                )
            ).all()
        )
        if include_discovered:
            scoped = rows
        else:
            selected = self.selected_ids(user.id)
            scoped = [
                row
                for row in rows
                if row.is_curated or row.owner_user_id == user.id or row.id in selected
            ]
        from app.services.membership_service import MembershipService

        visible = MembershipService(self.db).visible_edition_ids(user)
        if visible is None:
            return scoped
        return [
            row
            for row in scoped
            if row.edition_id is None or row.edition_id in visible
        ]

    def keyword_catalog_text(self, organization_id: UUID) -> str:
        categories = self.list_categories(organization_id)
        category_names = {category.id: category.name for category in categories}
        keywords = list(
            self.db.scalars(
                select(Keyword).where(
                    Keyword.organization_id == organization_id,
                    Keyword.scope == KeywordScope.organization,
                    Keyword.status.in_(
                        [KeywordStatus.active, KeywordStatus.candidate]
                    ),
                ).order_by(Keyword.name)
            ).all()
        )
        if not keywords:
            return ""
        grouped: dict[str, list[str]] = defaultdict(list)
        from app.models.edition import Edition

        edition_names = {
            row.id: row.name
            for row in self.db.scalars(
                select(Edition).where(Edition.organization_id == organization_id)
            ).all()
        }
        for keyword in keywords:
            category_name = category_names.get(keyword.category_id) or "미분류"
            edition_name = edition_names.get(keyword.edition_id) if keyword.edition_id else None
            label = f"{edition_name} / {category_name}" if edition_name else category_name
            grouped[label].append(keyword.name)
        lines = [
            "## 조직 키워드 참고 (가능하면 아래 용어를 우선 매칭, 없으면 신규 제안)"
        ]
        for label in sorted(grouped):
            lines.append(f"- {label}: {', '.join(grouped[label][:40])}")
        return "\n".join(lines)

    def selected_ids(self, user_id: UUID) -> set[UUID]:
        return set(
            self.db.scalars(
                select(UserKeywordSubscription.keyword_id).where(
                    UserKeywordSubscription.user_id == user_id
                )
            ).all()
        )

    def selected_category_ids(self, user_id: UUID) -> set[UUID]:
        return set(
            self.db.scalars(
                select(UserCategorySubscription.category_id).where(
                    UserCategorySubscription.user_id == user_id
                )
            ).all()
        )

    def selected_category_names(self, user_id: UUID, organization_id: UUID) -> list[str]:
        category_ids = self.selected_category_ids(user_id)
        if not category_ids:
            return []
        return list(
            self.db.scalars(
                select(NewsCategory.name).where(
                    NewsCategory.organization_id == organization_id,
                    NewsCategory.id.in_(category_ids),
                )
            ).all()
        )

    def list_categories_for_user(self, user: User) -> list[tuple[NewsCategory, bool]]:
        selected = self.selected_category_ids(user.id)
        rows = self.list_categories(user.organization_id)
        return [(row, row.id in selected) for row in rows]

    def is_personalization_ready(self, user: User) -> bool:
        if self.selected_category_ids(user.id):
            return True
        return len(self.selected_ids(user.id)) >= 3

    def keyword_ids_for_categories(
        self, organization_id: UUID, category_ids: list[UUID]
    ) -> list[UUID]:
        if not category_ids:
            return []
        return list(
            self.db.scalars(
                select(Keyword.id).where(
                    Keyword.organization_id == organization_id,
                    Keyword.category_id.in_(category_ids),
                    Keyword.status.in_(
                        [KeywordStatus.active, KeywordStatus.candidate]
                    ),
                    Keyword.scope == KeywordScope.organization,
                )
            ).all()
        )

    def curated_keyword_ids_for_categories(
        self, organization_id: UUID, category_ids: list[UUID]
    ) -> list[UUID]:
        if not category_ids:
            return []
        return list(
            self.db.scalars(
                select(Keyword.id).where(
                    Keyword.organization_id == organization_id,
                    Keyword.category_id.in_(category_ids),
                    Keyword.is_curated.is_(True),
                    Keyword.status.in_(
                        [KeywordStatus.active, KeywordStatus.candidate]
                    ),
                    Keyword.scope == KeywordScope.organization,
                )
            ).all()
        )

    def set_category_subscriptions(
        self, user: User, category_ids: list[UUID]
    ) -> tuple[list[NewsCategory], list[Keyword]]:
        unique_ids = list(dict.fromkeys(category_ids))
        if len(unique_ids) < 1:
            raise BadRequestError("관심 분야를 최소 1개 선택해야 합니다.")
        allowed = list(
            self.db.scalars(
                select(NewsCategory).where(
                    NewsCategory.id.in_(unique_ids),
                    NewsCategory.organization_id == user.organization_id,
                    NewsCategory.is_active.is_(True),
                )
            ).all()
        )
        if len(allowed) != len(unique_ids):
            raise BadRequestError("선택할 수 없는 분야가 포함되어 있습니다.")
        self.db.execute(
            delete(UserCategorySubscription).where(
                UserCategorySubscription.user_id == user.id
            )
        )
        for category in allowed:
            self.db.add(
                UserCategorySubscription(user_id=user.id, category_id=category.id)
            )
        # Category-first: only curated keywords under selected categories
        # (avoid auto-subscribing every AI-discovered candidate).
        curated_ids = self.curated_keyword_ids_for_categories(
            user.organization_id, unique_ids
        )
        personal_ids = list(
            self.db.scalars(
                select(UserKeywordSubscription.keyword_id)
                .join(Keyword, Keyword.id == UserKeywordSubscription.keyword_id)
                .where(
                    UserKeywordSubscription.user_id == user.id,
                    Keyword.owner_user_id == user.id,
                )
            ).all()
        )
        keyword_ids = list(dict.fromkeys([*curated_ids, *personal_ids]))
        if keyword_ids:
            keywords = self.set_subscriptions(
                user,
                keyword_ids,
                minimum=1,
                replace_existing=True,
            )
        else:
            self.db.execute(
                delete(UserKeywordSubscription).where(
                    UserKeywordSubscription.user_id == user.id
                )
            )
            keywords = []
        self.db.commit()
        return allowed, keywords

    def set_subscriptions(
        self,
        user: User,
        keyword_ids: list[UUID],
        *,
        minimum: int = 3,
        replace_existing: bool = True,
    ) -> list[Keyword]:
        unique_ids = list(dict.fromkeys(keyword_ids))
        if len(unique_ids) < minimum:
            raise BadRequestError(
                f"관심 키워드는 최소 {minimum}개를 선택해야 합니다."
            )
        allowed = list(
            self.db.scalars(
                select(Keyword).where(
                    Keyword.id.in_(unique_ids),
                    Keyword.organization_id == user.organization_id,
                    Keyword.status.in_(
                        [KeywordStatus.active, KeywordStatus.candidate]
                    ),
                    or_(
                        Keyword.scope == KeywordScope.organization,
                        Keyword.owner_user_id == user.id,
                    ),
                )
            ).all()
        )
        if len(allowed) != len(unique_ids):
            raise BadRequestError("선택할 수 없는 키워드가 포함되어 있습니다.")
        if replace_existing:
            self.db.execute(
                delete(UserKeywordSubscription).where(
                    UserKeywordSubscription.user_id == user.id
                )
            )
            target_keywords = allowed
        else:
            existing_ids = self.selected_ids(user.id)
            target_keywords = [row for row in allowed if row.id not in existing_ids]
        for keyword in target_keywords:
            self.db.add(UserKeywordSubscription(user_id=user.id, keyword_id=keyword.id))
        self.db.commit()
        return allowed

    def create_custom_keyword(self, user: User, name: str) -> Keyword:
        normalized = normalize_keyword(name)
        if not normalized:
            raise BadRequestError("키워드를 입력해 주세요.")
        existing = self.db.scalar(
            select(Keyword).where(
                Keyword.organization_id == user.organization_id,
                Keyword.normalized_name == normalized,
                or_(Keyword.owner_user_id == user.id, Keyword.owner_user_id.is_(None)),
            )
        )
        if existing:
            keyword = existing
        else:
            keyword = Keyword(
                organization_id=user.organization_id,
                owner_user_id=user.id,
                name=name.strip(),
                normalized_name=normalized,
                aliases=[],
                scope=KeywordScope.personal,
                status=KeywordStatus.active,
            )
            self.db.add(keyword)
            self.db.flush()
        subscribed = self.db.scalar(
            select(UserKeywordSubscription).where(
                UserKeywordSubscription.user_id == user.id,
                UserKeywordSubscription.keyword_id == keyword.id,
            )
        )
        if not subscribed:
            self.db.add(UserKeywordSubscription(user_id=user.id, keyword_id=keyword.id))
        since = datetime.now(timezone.utc) - timedelta(days=_CUSTOM_KEYWORD_SCAN_DAYS)
        posts = self.db.scalars(
            select(Post)
            .where(
                Post.organization_id == user.organization_id,
                Post.status.not_in([PostStatus.deleted, PostStatus.hidden]),
                Post.collected_at >= since,
            )
            .order_by(Post.collected_at.desc())
        ).all()
        contents = mget_post_contents(self.db, [post.id for post in posts])
        terms = [
            normalize_keyword(term)
            for term in [keyword.name, *(keyword.aliases or [])]
            if normalize_keyword(term)
        ]
        for post in posts:
            content = contents.get(post.id)
            body = ""
            summary = ""
            if content is not None:
                body = content.body or ""
                summary = content.summary or ""
            if not body and not summary:
                body = post.raw_content or ""
            blob = normalize_keyword(f"{post.title} {body} {summary}")
            if not any(term in blob for term in terms):
                continue
            exists_link = self.db.scalar(
                select(PostKeyword).where(
                    PostKeyword.post_id == post.id,
                    PostKeyword.keyword_id == keyword.id,
                )
            )
            if not exists_link:
                self.db.add(
                    PostKeyword(
                        post_id=post.id,
                        keyword_id=keyword.id,
                        confidence=0.8,
                        matched_by=KeywordMatchMethod.custom,
                    )
                )
        self.db.commit()
        self.db.refresh(keyword)
        return keyword

    def create_standard_keyword(
        self,
        organization_id: UUID,
        name: str,
        *,
        category_id: UUID | None = None,
        aliases: list[str] | None = None,
        status: KeywordStatus = KeywordStatus.active,
        edition_id: UUID | None = None,
    ) -> Keyword:
        normalized = normalize_keyword(name)
        existing = self.db.scalar(
            select(Keyword).where(
                Keyword.organization_id == organization_id,
                Keyword.owner_user_id.is_(None),
                Keyword.normalized_name == normalized,
            )
        )
        if existing:
            raise BadRequestError("이미 존재하는 표준 키워드입니다.")
        category = self.db.get(NewsCategory, category_id) if category_id else None
        resolved_edition = edition_id or (category.edition_id if category else None)
        row = Keyword(
            organization_id=organization_id,
            category_id=category_id,
            edition_id=resolved_edition,
            name=name.strip(),
            normalized_name=normalized,
            aliases=[a.strip() for a in aliases or [] if a.strip()],
            scope=KeywordScope.organization,
            status=status,
            is_curated=True,
            is_featured=bool(resolved_edition),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def promote_keyword(self, keyword_id: UUID, organization_id: UUID) -> Keyword:
        row = self.db.scalar(
            select(Keyword).where(
                Keyword.id == keyword_id,
                Keyword.organization_id == organization_id,
            )
        )
        if not row:
            raise NotFoundError("Keyword not found")
        standard = self.db.scalar(
            select(Keyword).where(
                Keyword.organization_id == organization_id,
                Keyword.owner_user_id.is_(None),
                Keyword.normalized_name == row.normalized_name,
            )
        )
        if standard and standard.id != row.id:
            user_ids = self.db.scalars(
                select(UserKeywordSubscription.user_id).where(
                    UserKeywordSubscription.keyword_id == row.id
                )
            ).all()
            for user_id in user_ids:
                exists = self.db.scalar(
                    select(UserKeywordSubscription).where(
                        UserKeywordSubscription.user_id == user_id,
                        UserKeywordSubscription.keyword_id == standard.id,
                    )
                )
                if not exists:
                    self.db.add(
                        UserKeywordSubscription(user_id=user_id, keyword_id=standard.id)
                    )
            post_links = self.db.scalars(
                select(PostKeyword).where(PostKeyword.keyword_id == row.id)
            ).all()
            for link in post_links:
                exists = self.db.scalar(
                    select(PostKeyword).where(
                        PostKeyword.post_id == link.post_id,
                        PostKeyword.keyword_id == standard.id,
                    )
                )
                if not exists:
                    self.db.add(
                        PostKeyword(
                            post_id=link.post_id,
                            keyword_id=standard.id,
                            confidence=link.confidence,
                            matched_by=KeywordMatchMethod.admin,
                        )
                    )
            self.db.execute(
                delete(UserKeywordSubscription).where(
                    UserKeywordSubscription.keyword_id == row.id
                )
            )
            self.db.execute(delete(PostKeyword).where(PostKeyword.keyword_id == row.id))
            row.status = KeywordStatus.archived
            self.db.commit()
            return standard
        row.owner_user_id = None
        row.scope = KeywordScope.organization
        row.status = KeywordStatus.active
        self.db.commit()
        self.db.refresh(row)
        return row

    def merge_keywords(
        self,
        source: Keyword,
        target: Keyword,
        *,
        organization_id: UUID,
    ) -> Keyword:
        if (
            not source
            or not target
            or source.id == target.id
            or source.organization_id != organization_id
            or target.organization_id != organization_id
        ):
            raise NotFoundError("Keyword not found")
        post_ids = self.db.scalars(
            select(PostKeyword.post_id).where(PostKeyword.keyword_id == source.id)
        ).all()
        for post_id in post_ids:
            exists = self.db.scalar(
                select(PostKeyword).where(
                    PostKeyword.post_id == post_id,
                    PostKeyword.keyword_id == target.id,
                )
            )
            if not exists:
                link = self.db.scalar(
                    select(PostKeyword).where(
                        PostKeyword.post_id == post_id,
                        PostKeyword.keyword_id == source.id,
                    )
                )
                self.db.add(
                    PostKeyword(
                        post_id=post_id,
                        keyword_id=target.id,
                        confidence=link.confidence if link else 1.0,
                        matched_by=KeywordMatchMethod.admin,
                    )
                )
            post = self.db.get(Post, post_id)
            if post and post.keywords:
                items = list(post.keywords.get("items") or [])
                rewritten: list[str] = []
                changed = False
                for item in items:
                    label = target.name if item == source.name else item
                    if label not in rewritten:
                        rewritten.append(label)
                    if item == source.name:
                        changed = True
                if changed:
                    post.keywords = {**post.keywords, "items": rewritten}
        user_ids = self.db.scalars(
            select(UserKeywordSubscription.user_id).where(
                UserKeywordSubscription.keyword_id == source.id
            )
        ).all()
        for user_id in user_ids:
            exists = self.db.scalar(
                select(UserKeywordSubscription).where(
                    UserKeywordSubscription.user_id == user_id,
                    UserKeywordSubscription.keyword_id == target.id,
                )
            )
            if not exists:
                self.db.add(
                    UserKeywordSubscription(user_id=user_id, keyword_id=target.id)
                )
        self.db.execute(delete(PostKeyword).where(PostKeyword.keyword_id == source.id))
        self.db.execute(
            delete(UserKeywordSubscription).where(
                UserKeywordSubscription.keyword_id == source.id
            )
        )
        source.status = KeywordStatus.archived
        target.usage_count = int(target.usage_count or 0) + int(source.usage_count or 0)
        target.aliases = list(
            dict.fromkeys(
                [
                    *(target.aliases or []),
                    source.name,
                    *(source.aliases or []),
                ]
            )
        )[:20]
        if not target.is_curated and source.is_curated:
            target.is_curated = True
        add_keyword_alias(target, source.name)
        self.db.flush()
        return target


class ClassificationService:
    def __init__(self, db: Session):
        self.db = db

    def classify_post(
        self,
        post: Post,
        result: dict | None = None,
        *,
        content: PostContent | None = None,
        skip_es_sync: bool = False,
    ) -> tuple[list[str], list[ReviewQueueReason]]:
        taxonomy = TaxonomyService(self.db)
        taxonomy.ensure_defaults(post.organization_id)
        merged = dict(result or {})
        review_reasons: list[ReviewQueueReason] = []
        keywords_from_ai = bool(merged.get("keywords"))

        if not merged.get("keywords") or not merged.get("category"):
            extracted = self._extract_classification(post, review_reasons, content=content)
            if extracted.get("keywords"):
                keywords_from_ai = True
            for key, value in extracted.items():
                if value and not merged.get(key):
                    merged[key] = value

        categories = taxonomy.list_categories(post.organization_id)
        category_name = (merged.get("category") or post.category or "기타").strip()
        category = taxonomy.get_or_create_category(post.organization_id, category_name)
        post.category = category.name

        raw_keywords = merged.get("keywords") or []
        candidates: list[tuple[str, float, Keyword | None]] = []
        for raw in raw_keywords:
            if isinstance(raw, str):
                candidates.append((raw, float(merged.get("confidence") or 0.7), None))
            elif isinstance(raw, dict) and raw.get("name"):
                candidates.append((str(raw["name"]), float(raw.get("confidence") or 0.7), None))

        all_keywords = list(
            self.db.scalars(
                select(Keyword).where(
                    Keyword.organization_id == post.organization_id,
                    Keyword.status.in_(
                        [KeywordStatus.active, KeywordStatus.candidate]
                    ),
                )
            ).all()
        )
        organization_keywords = [
            keyword for keyword in all_keywords if keyword.scope == KeywordScope.organization
        ]
        blob = self._post_text_blob(post, content=content)
        for keyword in all_keywords:
            terms = [keyword.name, *(keyword.aliases or [])]
            if any(normalize_keyword(term) in blob for term in terms if normalize_keyword(term)):
                candidates.append((keyword.name, 0.72, keyword))

        self.db.execute(delete(PostKeyword).where(PostKeyword.post_id == post.id))
        linked_names: list[str] = []
        seen_names: set[str] = set()
        seen_keyword_ids: set[UUID] = set()
        deferred_new: list[tuple[str, float]] = []
        for name, confidence, matched_keyword in sorted(
            candidates,
            key=lambda item: item[1],
            reverse=True,
        ):
            normalized = normalize_keyword(name)
            if not normalized or normalized in seen_names:
                continue
            keyword = matched_keyword or resolve_existing_keyword(
                name,
                organization_keywords,
                category_id=category.id if category else None,
            )
            if not keyword:
                if confidence < _MIN_NEW_KEYWORD_CONFIDENCE:
                    deferred_new.append((name, confidence))
                    continue
                keyword = Keyword(
                    organization_id=post.organization_id,
                    category_id=category.id if category else None,
                    name=name.strip()[:128],
                    normalized_name=normalized[:128],
                    aliases=[],
                    scope=KeywordScope.organization,
                    status=KeywordStatus.candidate,
                    is_curated=False,
                )
                self.db.add(keyword)
                self.db.flush()
                all_keywords.append(keyword)
                organization_keywords.append(keyword)
                review_reasons.append(ReviewQueueReason.new_keyword)
            else:
                if normalize_keyword(name) != keyword.normalized_name:
                    add_keyword_alias(keyword, name)
                keyword.usage_count = int(keyword.usage_count or 0) + 1
            if keyword.id in seen_keyword_ids:
                continue
            seen_names.add(normalized)
            seen_keyword_ids.add(keyword.id)
            self.db.add(
                PostKeyword(
                    post_id=post.id,
                    keyword_id=keyword.id,
                    confidence=max(0.0, min(confidence, 1.0)),
                    matched_by=KeywordMatchMethod.ai if keywords_from_ai else KeywordMatchMethod.alias,
                )
            )
            linked_names.append(keyword.name)
            if len(linked_names) >= _MAX_KEYWORDS_PER_POST:
                break

        if not linked_names:
            for name, confidence in deferred_new:
                if len(linked_names) >= _MAX_KEYWORDS_PER_POST:
                    break
                normalized = normalize_keyword(name)
                if not normalized or normalized in seen_names:
                    continue
                keyword = Keyword(
                    organization_id=post.organization_id,
                    category_id=category.id if category else None,
                    name=name.strip()[:128],
                    normalized_name=normalized[:128],
                    aliases=[],
                    scope=KeywordScope.organization,
                    status=KeywordStatus.candidate,
                    is_curated=False,
                )
                self.db.add(keyword)
                self.db.flush()
                organization_keywords.append(keyword)
                seen_names.add(normalized)
                seen_keyword_ids.add(keyword.id)
                self.db.add(
                    PostKeyword(
                        post_id=post.id,
                        keyword_id=keyword.id,
                        confidence=max(0.0, min(confidence, 1.0)),
                        matched_by=KeywordMatchMethod.ai,
                    )
                )
                linked_names.append(keyword.name)

        if not linked_names:
            for name, confidence, keyword in self._forced_curated_keywords(
                post, blob, organization_keywords, category
            ):
                if len(linked_names) >= _MAX_KEYWORDS_PER_POST:
                    break
                if keyword.id in seen_keyword_ids:
                    continue
                seen_names.add(keyword.normalized_name)
                seen_keyword_ids.add(keyword.id)
                keyword.usage_count = int(keyword.usage_count or 0) + 1
                self.db.add(
                    PostKeyword(
                        post_id=post.id,
                        keyword_id=keyword.id,
                        confidence=max(0.0, min(confidence, 1.0)),
                        matched_by=KeywordMatchMethod.alias,
                    )
                )
                linked_names.append(keyword.name)

        if linked_names:
            review_reasons = [
                reason
                for reason in review_reasons
                if reason
                not in (ReviewQueueReason.no_keywords, ReviewQueueReason.extraction_failed)
            ]
        else:
            review_reasons.append(ReviewQueueReason.no_keywords)

        self._sync_review_queue(post, list(dict.fromkeys(review_reasons)))
        post.keywords = {"items": linked_names, "classification_version": "v2"}
        self.db.flush()
        if not skip_es_sync:
            sync_post_metadata(self.db, post)
        return linked_names, list(dict.fromkeys(review_reasons))

    def _post_text_blob(self, post: Post, *, content: PostContent | None = None) -> str:
        if content is None:
            content = get_post_content(self.db, post.id)
        parts = [post.title or "", content.body or "", content.summary or ""]
        return normalize_keyword(" ".join(part for part in parts if part))

    def _forced_curated_keywords(
        self,
        post: Post,
        blob: str,
        organization_keywords: list[Keyword],
        category,
    ) -> list[tuple[str, float, Keyword]]:
        from app.services.ev_relevance import has_strong_av_signal, has_strong_ev_signal

        curated = [
            keyword
            for keyword in organization_keywords
            if keyword.is_curated and keyword.scope == KeywordScope.organization
        ]
        if not curated:
            return []

        by_name = {keyword.normalized_name: keyword for keyword in curated}
        hits: list[tuple[str, float, Keyword]] = []
        seen: set[UUID] = set()

        def add(keyword: Keyword | None, confidence: float) -> None:
            if keyword is None or keyword.id in seen:
                return
            seen.add(keyword.id)
            hits.append((keyword.name, confidence, keyword))

        title = post.title or ""
        url = getattr(post, "original_url", None) or ""
        if has_strong_av_signal(title, blob, url):
            for name in ("자율주행", "로보택시", "ADAS", "레벨4"):
                add(by_name.get(normalize_keyword(name)), 0.52)
        if has_strong_ev_signal(title, blob, url):
            for name in ("충전 인프라", "충전소", "전기차 정책", "OCPP"):
                add(by_name.get(normalize_keyword(name)), 0.52)

        if not hits and category is not None:
            same_edition = [
                keyword
                for keyword in curated
                if category.edition_id and keyword.edition_id == category.edition_id
            ]
            pool = [keyword for keyword in same_edition if keyword.is_featured] or same_edition
            if pool:
                add(pool[0], 0.4)

        if not hits:
            featured = [keyword for keyword in curated if keyword.is_featured]
            add((featured or curated)[0], 0.35)
        return hits

    def _extract_classification(
        self,
        post: Post,
        review_reasons: list[ReviewQueueReason],
        *,
        content: PostContent | None = None,
    ) -> dict:
        if content is None:
            content = get_post_content(self.db, post.id)
        content_blob = "\n\n".join(
            part
            for part in [content.body or "", content.summary or ""]
            if part
        ).strip()
        if len(content_blob) < 20 and len((post.title or "").strip()) < 8:
            return {}
        try:
            catalog = TaxonomyService(self.db).keyword_catalog_text(
                post.organization_id
            )
            return get_llm_client().classify_post_content(
                post.title,
                content_blob or post.title,
                keyword_catalog=catalog or None,
            )
        except Exception:
            review_reasons.append(ReviewQueueReason.extraction_failed)
            return {}

    def suggest_keywords(self, post: Post) -> dict:
        """AI keyword suggestions without persisting."""
        content = get_post_content(self.db, post.id)
        content_blob = "\n\n".join(
            part
            for part in [content.body or "", content.summary or ""]
            if part
        ).strip()
        text = content_blob or (post.title or "")
        try:
            catalog = TaxonomyService(self.db).keyword_catalog_text(
                post.organization_id
            )
            result = get_llm_client().classify_post_content(
                post.title or "",
                text,
                keyword_catalog=catalog or None,
            )
        except Exception:
            return {"category": post.category, "suggestions": []}

        org_keywords = {
            row.normalized_name: row
            for row in self.db.scalars(
                select(Keyword).where(
                    Keyword.organization_id == post.organization_id,
                    Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
                )
            ).all()
        }
        suggestions: list[dict] = []
        seen: set[str] = set()
        for raw in result.get("keywords") or []:
            if isinstance(raw, str):
                name = raw.strip()
                confidence = float(result.get("confidence") or 0.7)
            elif isinstance(raw, dict) and raw.get("name"):
                name = str(raw["name"]).strip()
                confidence = float(raw.get("confidence") or 0.7)
            else:
                continue
            normalized = normalize_keyword(name)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            matched = org_keywords.get(normalized)
            suggestions.append(
                {
                    "name": name[:128],
                    "confidence": max(0.0, min(confidence, 1.0)),
                    "keyword_id": matched.id if matched else None,
                }
            )
            if len(suggestions) >= 8:
                break
        return {
            "category": (result.get("category") or post.category or "").strip() or None,
            "suggestions": suggestions,
        }

    def apply_manual_keywords(
        self,
        post: Post,
        *,
        keyword_ids: list[UUID],
        new_keyword_names: list[str],
        category: str | None = None,
    ) -> list[str]:
        """Link admin-selected keywords; clear review queue when at least one is linked."""
        taxonomy = TaxonomyService(self.db)
        taxonomy.ensure_defaults(post.organization_id)
        categories = taxonomy.list_categories(post.organization_id)
        category_by_name = {normalize_keyword(c.name): c for c in categories}

        if category:
            category_name = category.strip()
            matched = category_by_name.get(normalize_keyword(category_name))
            post.category = matched.name if matched else category_name[:128]

        self.db.execute(delete(PostKeyword).where(PostKeyword.post_id == post.id))
        linked_names: list[str] = []
        seen: set[str] = set()

        if keyword_ids:
            rows = self.db.scalars(
                select(Keyword).where(
                    Keyword.organization_id == post.organization_id,
                    Keyword.id.in_(keyword_ids),
                )
            ).all()
            by_id = {row.id: row for row in rows}
            for kid in keyword_ids:
                if len(linked_names) >= 5:
                    break
                keyword = by_id.get(kid)
                if not keyword or keyword.normalized_name in seen:
                    continue
                seen.add(keyword.normalized_name)
                self.db.add(
                    PostKeyword(
                        post_id=post.id,
                        keyword_id=keyword.id,
                        confidence=1.0,
                        matched_by=KeywordMatchMethod.admin,
                    )
                )
                linked_names.append(keyword.name)

        category_row = category_by_name.get(normalize_keyword(post.category or "기타"))
        if not category_row:
            category_row = category_by_name.get(normalize_keyword("기타"))

        for raw_name in new_keyword_names:
            if len(linked_names) >= 5:
                break
            name = (raw_name or "").strip()[:128]
            normalized = normalize_keyword(name)
            if not normalized or normalized in seen:
                continue
            keyword = self.db.scalar(
                select(Keyword).where(
                    Keyword.organization_id == post.organization_id,
                    Keyword.normalized_name == normalized,
                )
            )
            if not keyword:
                keyword = Keyword(
                    organization_id=post.organization_id,
                    category_id=category_row.id if category_row else None,
                    name=name,
                    normalized_name=normalized[:128],
                    aliases=[],
                    scope=KeywordScope.organization,
                    status=KeywordStatus.active,
                )
                self.db.add(keyword)
                self.db.flush()
            seen.add(normalized)
            self.db.add(
                PostKeyword(
                    post_id=post.id,
                    keyword_id=keyword.id,
                    confidence=1.0,
                    matched_by=KeywordMatchMethod.admin,
                )
            )
            linked_names.append(keyword.name)

        review_reasons: list[ReviewQueueReason] = []
        if not linked_names:
            review_reasons.append(ReviewQueueReason.no_keywords)
        self._sync_review_queue(post, review_reasons)
        post.keywords = {"items": linked_names, "classification_version": "v2"}
        self.db.flush()
        sync_post_metadata(self.db, post)
        return linked_names

    def _latest_confidence(self, post: Post) -> float | None:
        if not post.ai_outputs:
            return None
        return max(post.ai_outputs, key=lambda item: item.created_at).confidence

    def _sync_review_queue(self, post: Post, reasons: list[ReviewQueueReason]) -> None:
        active = set(reasons)
        pending_items = list(
            self.db.scalars(
                select(ReviewQueueItem).where(
                    ReviewQueueItem.post_id == post.id,
                    ReviewQueueItem.status == ReviewQueueStatus.pending,
                )
            ).all()
        )
        now = datetime.now(timezone.utc)
        for item in pending_items:
            if item.reason not in active:
                item.status = ReviewQueueStatus.resolved
                item.resolved_at = now
        for reason in active:
            exists = self.db.scalar(
                select(ReviewQueueItem).where(
                    ReviewQueueItem.post_id == post.id,
                    ReviewQueueItem.reason == reason,
                    ReviewQueueItem.status == ReviewQueueStatus.pending,
                )
            )
            if not exists:
                self.db.add(
                    ReviewQueueItem(
                        organization_id=post.organization_id,
                        post_id=post.id,
                        reason=reason,
                        status=ReviewQueueStatus.pending,
                    )
                )


class PersonalizedNewsService:
    def __init__(self, db: Session):
        self.db = db

    def list_news(
        self,
        user: User,
        *,
        personalized: bool,
        keyword_ids: list[UUID] | None = None,
        category: str | None = None,
        importance: Importance | None = None,
        content_kind: str | None = None,
        query: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        page: int = 1,
        size: int = 20,
        recency: bool = False,
    ) -> NewsPage:
        selected = set(keyword_ids or [])
        taxonomy = TaxonomyService(self.db)
        from app.services.membership_service import MembershipService

        membership = MembershipService(self.db)
        if not personalized:
            selected = membership.restrict_keyword_ids(user, keyword_ids)
            if not selected and membership.visible_edition_ids(user) is not None:
                return NewsPage(items=[], total=0, page=page, size=size, pages=1)
        if personalized and not selected:
            selected = taxonomy.selected_ids(user.id)
        selected_category_names = (
            taxonomy.selected_category_names(user.id, user.organization_id)
            if personalized
            else []
        )
        if personalized and not selected and not selected_category_names:
            return NewsPage(items=[], total=0, page=page, size=size, pages=1)

        if date_from is None:
            days = get_settings().feed_window_days
            if days > 0:
                date_from = datetime.now(KST).date() - timedelta(days=days)

        use_recency = recency or personalized

        if get_settings().search_uses_elasticsearch and (query or "").strip():
            es_page = self._list_news_es(
                user,
                selected=selected,
                personalized=personalized,
                category=category,
                importance=importance,
                query=query,
                date_from=date_from,
                date_to=date_to,
                page=page,
                size=size,
                recency=use_recency,
            )
            if es_page is not None:
                return es_page

        q = (
            select(Post)
            .options(joinedload(Post.source), joinedload(Post.ai_outputs))
            .where(
                Post.organization_id == user.organization_id,
                Post.status.not_in([PostStatus.deleted, PostStatus.hidden]),
            )
        )
        if personalized and selected and selected_category_names:
            q = q.where(
                or_(
                    exists().where(
                        PostKeyword.post_id == Post.id,
                        PostKeyword.keyword_id.in_(selected),
                    ),
                    Post.category.in_(selected_category_names),
                )
            )
        elif selected:
            q = q.join(PostKeyword, PostKeyword.post_id == Post.id).where(
                PostKeyword.keyword_id.in_(selected)
            )
        elif selected_category_names:
            q = q.where(Post.category.in_(selected_category_names))
        if category:
            q = q.where(Post.category == category)
        if importance:
            q = q.where(Post.importance == importance)
        kind = (content_kind or "").strip().lower()
        if kind in {"news", "official", "community", "discovery"}:
            q = q.join(Source, Source.id == Post.source_id)
            if kind in {"news", "official"}:
                q = q.where(Source.source_type.not_in(COMMUNITY_SOURCE_TYPES))
            elif kind == "community":
                q = q.where(Source.source_type.in_(COMMUNITY_SOURCE_TYPES))
            elif kind == "discovery":
                q = q.where(
                    Post.board_type == BoardType.discovery,
                    Source.source_type.not_in(COMMUNITY_SOURCE_TYPES),
                )
        if query:
            like = f"%{query.strip()}%"
            q = q.outerjoin(AIOutput).where(
                or_(Post.title.ilike(like), Post.raw_content.ilike(like), AIOutput.summary.ilike(like))
            )
        if date_from:
            q = q.where(Post.collected_at >= datetime.combine(date_from, datetime.min.time(), tzinfo=KST))
        if date_to:
            q = q.where(
                Post.collected_at
                < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=KST)
            )
        # DISTINCT on full Post rows fails on PostgreSQL (posts.keywords is JSON).
        posts = list(self.db.scalars(q.order_by(Post.collected_at.desc())).unique().all())
        contents = mget_post_contents(self.db, [post.id for post in posts])
        from app.services.ev_display_filter import is_ev_related_post
        from app.services.topic_gate import load_topic_terms

        extra_terms = load_topic_terms(self.db, user.organization_id)
        items = []
        for post in posts:
            content = contents.get(post.id)
            body = ""
            if content is not None:
                body = (content.body or content.summary or "")[:4000]
            if not is_ev_related_post(post, body=body, extra_terms=extra_terms):
                continue
            items.append(self._to_item(post, selected, user.id, content))
        if personalized and not use_recency:
            items = self._diversified(items)
        total = len(items)
        offset = (page - 1) * size
        return NewsPage(
            items=items[offset : offset + size],
            total=total,
            page=page,
            size=size,
            pages=max(1, (total + size - 1) // size),
        )

    def list_editorial(
        self,
        user: User,
        edition_id: UUID,
        *,
        page: int = 1,
        size: int = 24,
    ) -> NewsPage:
        from app.services.edition_service import EditionService
        from app.services.membership_service import MembershipService

        MembershipService(self.db).assert_view(user, edition_id)
        edition_svc = EditionService(self.db)
        edition = edition_svc.get(edition_id, user.organization_id)
        featured_ids = edition_svc.featured_keyword_ids(user.organization_id, edition.id)
        if not featured_ids:
            featured_ids = list(
                self.db.scalars(
                    select(Keyword.id).where(
                        Keyword.organization_id == user.organization_id,
                        Keyword.edition_id == edition.id,
                        Keyword.is_curated.is_(True),
                        Keyword.scope == KeywordScope.organization,
                        Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
                    )
                ).all()
            )
        if not featured_ids:
            return NewsPage(items=[], total=0, page=page, size=size, pages=1)
        return self.list_news(
            user,
            personalized=False,
            keyword_ids=featured_ids,
            page=page,
            size=size,
            recency=True,
        )

    def _list_news_es(
        self,
        user: User,
        *,
        selected: set[UUID],
        personalized: bool,
        category: str | None,
        importance: Importance | None,
        query: str | None,
        date_from: date | None,
        date_to: date | None,
        page: int,
        size: int,
        recency: bool = False,
    ) -> NewsPage | None:
        date_from_dt = None
        date_to_dt = None
        if date_from:
            date_from_dt = datetime.combine(date_from, datetime.min.time(), tzinfo=KST)
        if date_to:
            date_to_dt = datetime.combine(
                date_to + timedelta(days=1), datetime.min.time(), tzinfo=KST
            )

        filters = PostSearchFilters(
            organization_id=user.organization_id,
            query=(query or "").strip() or None,
            exclude_statuses=["deleted", "hidden"],
            category=category,
            importance=importance.value if importance else None,
            keyword_ids=list(selected) if selected else None,
            date_from=date_from_dt,
            date_to=date_to_dt,
        )
        highlight = bool((query or "").strip())
        fetch_page = 1 if personalized else page
        fetch_size = 500 if personalized else size
        result = search_posts(
            filters,
            page=fetch_page,
            size=fetch_size,
            highlight=highlight,
        )
        if result is None:
            return None

        posts = load_posts_ordered(self.db, [hit.post_id for hit in result.hits])
        hit_by_id = {hit.post_id: hit for hit in result.hits}
        contents = mget_post_contents(self.db, [post.id for post in posts])
        from app.services.ev_display_filter import is_ev_related_post
        from app.services.topic_gate import load_topic_terms

        extra_terms = load_topic_terms(self.db, user.organization_id)
        items = []
        for post in posts:
            content = contents.get(post.id)
            body = ""
            if content is not None:
                body = (content.body or content.summary or "")[:4000]
            if not is_ev_related_post(post, body=body, extra_terms=extra_terms):
                continue
            items.append(
                self._to_item(
                    post,
                    selected,
                    user.id,
                    content,
                    hit=hit_by_id.get(post.id),
                )
            )
        if personalized and not recency:
            items = self._diversified(items)
            total = len(items)
            offset = (page - 1) * size
            return NewsPage(
                items=items[offset : offset + size],
                total=total,
                page=page,
                size=size,
                pages=max(1, (total + size - 1) // size),
            )
        if personalized:
            total = len(items)
            offset = (page - 1) * size
            return NewsPage(
                items=items[offset : offset + size],
                total=total,
                page=page,
                size=size,
                pages=max(1, (total + size - 1) // size),
            )

        pages = max(1, (result.total + size - 1) // size)
        return NewsPage(
            items=items,
            total=result.total,
            page=page,
            size=size,
            pages=pages,
        )

    def _to_item(
        self,
        post: Post,
        selected: set[UUID] | None = None,
        user_id: UUID | None = None,
        content=None,
        hit=None,
    ) -> NewsItem:
        rows = self.db.execute(
            select(PostKeyword, Keyword)
            .join(Keyword, Keyword.id == PostKeyword.keyword_id)
            .where(
                PostKeyword.post_id == post.id,
                Keyword.status != KeywordStatus.archived,
                or_(
                    Keyword.scope == KeywordScope.organization,
                    Keyword.owner_user_id == user_id,
                ),
            )
        ).all()
        matched = [
            MatchedKeyword(id=keyword.id, name=keyword.name, confidence=link.confidence)
            for link, keyword in rows
            if not selected or keyword.id in selected
        ]
        latest_ai = max(post.ai_outputs, key=lambda item: item.created_at) if post.ai_outputs else None
        if content is None:
            content = get_post_content(self.db, post.id)
        summary = content.summary
        if (
            not summary
            and legacy_pg_content_enabled()
            and latest_ai
            and (latest_ai.summary or "").strip() not in ("", " ")
        ):
            summary = latest_ai.summary
        age_hours = max(
            0.0,
            (datetime.now(timezone.utc) - self._aware(post.collected_at)).total_seconds() / 3600,
        )
        keyword_score = max((item.confidence for item in matched), default=0) * 4
        importance_score = {
            Importance.high: 3.0,
            Importance.medium: 1.8,
            Importance.low: 0.8,
            Importance.unknown: 0.3,
        }[post.importance]
        source_score = min(max(post.reliability_score, 0), 100) / 50
        freshness = max(0, 2 - age_hours / 36)
        source_type = post.source.source_type if post.source else None
        source_type_value = (
            source_type.value if isinstance(source_type, SourceType) else source_type
        )
        board_value = (
            post.board_type.value
            if isinstance(post.board_type, BoardType)
            else post.board_type
        )
        community = bool(
            source_type is not None and is_community_source_type(source_type)
        )
        return NewsItem(
            id=post.id,
            title=post.title,
            source_name=post.source.name if post.source else None,
            source_type=source_type_value,
            board_type=board_value,
            is_community=community,
            category=post.category,
            collected_at=post.collected_at,
            original_url=content.original_url,
            importance=post.importance,
            summary=summary,
            summary_highlight=hit.highlight_summary if hit else None,
            matched_keywords=matched,
            personalization_score=round(keyword_score + importance_score + source_score + freshness, 4),
        )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    def _diversified(self, items: list[NewsItem]) -> list[NewsItem]:
        remaining = list(items)
        ordered: list[NewsItem] = []
        keyword_counts: defaultdict[str, int] = defaultdict(int)
        source_counts: defaultdict[str, int] = defaultdict(int)
        while remaining:
            def adjusted(item: NewsItem) -> tuple[float, datetime]:
                keyword_penalty = sum(
                    keyword_counts[keyword.name] * 0.35 for keyword in item.matched_keywords
                )
                source_key = item.source_name or ""
                source_penalty = source_counts[source_key] * 0.45 if source_key else 0
                return (
                    item.personalization_score - keyword_penalty - source_penalty,
                    item.collected_at,
                )

            chosen = max(remaining, key=adjusted)
            remaining.remove(chosen)
            ordered.append(chosen)
            for keyword in chosen.matched_keywords:
                keyword_counts[keyword.name] += 1
            if chosen.source_name:
                source_counts[chosen.source_name] += 1
        return ordered

    def get_topic_hub(
        self,
        user: User,
        keyword_id: UUID,
        *,
        exact_size: int = 20,
        related_size: int = 12,
        sparse_threshold: int = 3,
    ) -> TopicHubRead:
        taxonomy = TaxonomyService(self.db)
        keyword = self.db.scalar(
            select(Keyword).where(
                Keyword.id == keyword_id,
                Keyword.organization_id == user.organization_id,
                Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
                or_(
                    Keyword.scope == KeywordScope.organization,
                    Keyword.owner_user_id == user.id,
                ),
            )
        )
        if not keyword:
            raise NotFoundError("키워드를 찾을 수 없습니다.")

        category = None
        if keyword.category_id:
            category = self.db.scalar(
                select(NewsCategory).where(NewsCategory.id == keyword.category_id)
            )

        selected_ids = taxonomy.selected_ids(user.id)
        exact_page = self.list_news(
            user,
            personalized=False,
            keyword_ids=[keyword_id],
            page=1,
            size=exact_size,
        )
        exact_ids = {item.id for item in exact_page.items}
        related_posts: list[NewsItem] = []
        if category and category.name:
            related_page = self.list_news(
                user,
                personalized=False,
                category=category.name,
                page=1,
                size=related_size + len(exact_ids) + 8,
            )
            related_posts = [
                item for item in related_page.items if item.id not in exact_ids
            ][:related_size]

        siblings: list[Keyword] = []
        if keyword.category_id:
            siblings = list(
                self.db.scalars(
                    select(Keyword)
                    .where(
                        Keyword.organization_id == user.organization_id,
                        Keyword.category_id == keyword.category_id,
                        Keyword.id != keyword.id,
                        Keyword.is_curated.is_(True),
                        Keyword.status.in_(
                            [KeywordStatus.active, KeywordStatus.candidate]
                        ),
                        or_(
                            Keyword.scope == KeywordScope.organization,
                            Keyword.owner_user_id == user.id,
                        ),
                    )
                    .order_by(Keyword.usage_count.desc(), Keyword.name.asc())
                    .limit(12)
                ).all()
            )

        def _kw_read(row: Keyword) -> KeywordRead:
            data = KeywordRead.model_validate(row)
            data.selected = row.id in selected_ids
            data.category_name = category.name if category else None
            return data

        return TopicHubRead(
            keyword=_kw_read(keyword),
            category_name=category.name if category else None,
            exact_posts=exact_page.items,
            related_posts=related_posts,
            sibling_keywords=[_kw_read(row) for row in siblings],
            exact_count=exact_page.total,
            exact_is_sparse=exact_page.total < sparse_threshold,
        )


class PersonalReportService:
    def __init__(self, db: Session):
        self.db = db
        self.news = PersonalizedNewsService(db)

    def generate_for_user(self, user: User, target: date | None = None) -> PersonalReport | None:
        target = target or datetime.now(KST).date()
        selected = TaxonomyService(self.db).selected_ids(user.id)
        if not TaxonomyService(self.db).is_personalization_ready(user):
            return None
        page = self.news.list_news(
            user,
            personalized=True,
            date_from=target,
            date_to=target,
            page=1,
            size=100,
        )
        items = page.items[:10]
        existing = self.db.scalar(
            select(PersonalReport).where(
                PersonalReport.user_id == user.id,
                PersonalReport.report_date == target,
            )
        )
        if not items:
            if existing:
                self.db.execute(
                    delete(PersonalReportItem).where(PersonalReportItem.report_id == existing.id)
                )
                self.db.delete(existing)
                self.db.commit()
            return None
        payload = [
            {
                "id": str(item.id),
                "title": item.title,
                "summary": item.summary or item.title,
                "board": "personalized",
                "importance": item.importance.value,
            }
            for item in items
        ]
        client = get_llm_client()
        result = client.generate_daily_report(payload, target)
        summary = (result.get("summary") or "").strip()[:500]
        report = existing or PersonalReport(
            organization_id=user.organization_id,
            user_id=user.id,
            report_date=target,
            title=f"{user.name}님의 MINT 브리핑 · {target.isoformat()}",
            summary=summary,
            item_count=len(items),
            model=getattr(client, "report_model", "mock"),
        )
        report.summary = summary
        report.item_count = len(items)
        if not existing:
            self.db.add(report)
            self.db.flush()
        else:
            self.db.execute(
                delete(PersonalReportItem).where(PersonalReportItem.report_id == report.id)
            )
        for rank, item in enumerate(items, start=1):
            self.db.add(
                PersonalReportItem(
                    report_id=report.id,
                    post_id=item.id,
                    rank=rank,
                    score=item.personalization_score,
                    matched_keyword_names=[keyword.name for keyword in item.matched_keywords],
                )
            )
        self.db.commit()
        return report

    def list_reports(self, user: User) -> list[PersonalReportRead]:
        rows = self.db.scalars(
            select(PersonalReport)
            .where(PersonalReport.user_id == user.id)
            .order_by(PersonalReport.report_date.desc())
        ).all()
        return [self._read(row, user.id, include_items=False) for row in rows]

    def latest(self, user: User) -> PersonalReportRead | None:
        row = self.db.scalar(
            select(PersonalReport)
            .where(PersonalReport.user_id == user.id)
            .order_by(PersonalReport.report_date.desc())
            .limit(1)
        )
        return self._read(row, user.id, include_items=True) if row else None

    def get(self, report_id: UUID, user: User) -> PersonalReportRead:
        row = self.db.scalar(
            select(PersonalReport).where(
                PersonalReport.id == report_id,
                PersonalReport.user_id == user.id,
            )
        )
        if not row:
            raise NotFoundError("Personal report not found")
        return self._read(row, user.id, include_items=True)

    def mark_view(self, report_id: UUID, user: User, *, popup_seen: bool, opened: bool) -> None:
        report = self.db.scalar(
            select(PersonalReport).where(
                PersonalReport.id == report_id,
                PersonalReport.user_id == user.id,
            )
        )
        if not report:
            raise NotFoundError("Personal report not found")
        row = self.db.scalar(
            select(PersonalReportView).where(
                PersonalReportView.report_id == report_id,
                PersonalReportView.user_id == user.id,
            )
        )
        if not row:
            row = PersonalReportView(report_id=report_id, user_id=user.id)
            self.db.add(row)
        now = datetime.now(timezone.utc)
        if popup_seen:
            row.popup_seen_at = row.popup_seen_at or now
        if opened:
            row.opened_at = row.opened_at or now
        self.db.commit()

    def _read(self, report: PersonalReport, user_id: UUID, *, include_items: bool) -> PersonalReportRead:
        view = self.db.scalar(
            select(PersonalReportView).where(
                PersonalReportView.report_id == report.id,
                PersonalReportView.user_id == user_id,
            )
        )
        items: list[PersonalReportItemRead] = []
        if include_items:
            rows = self.db.scalars(
                select(PersonalReportItem)
                .where(PersonalReportItem.report_id == report.id)
                .order_by(PersonalReportItem.rank)
            ).all()
            for row in rows:
                post = self.db.scalar(
                    select(Post)
                    .options(joinedload(Post.source), joinedload(Post.ai_outputs))
                    .where(Post.id == row.post_id)
                )
                if post:
                    items.append(
                        PersonalReportItemRead(
                            post=self.news._to_item(post, user_id=user_id),
                            rank=row.rank,
                            score=row.score,
                            matched_keyword_names=row.matched_keyword_names or [],
                        )
                    )
        return PersonalReportRead(
            id=report.id,
            report_date=report.report_date,
            title=report.title,
            summary=report.summary,
            item_count=report.item_count,
            popup_seen=bool(view and view.popup_seen_at),
            items=items,
        )


class ReviewQueueService:
    def __init__(self, db: Session):
        self.db = db

    def list(
        self,
        organization_id: UUID,
        status: ReviewQueueStatus,
        user: User | None = None,
    ) -> list[ReviewQueueRead]:
        rows = self.db.execute(
            select(ReviewQueueItem, Post)
            .join(Post, Post.id == ReviewQueueItem.post_id)
            .where(
                ReviewQueueItem.organization_id == organization_id,
                ReviewQueueItem.status == status,
                Post.status.not_in([PostStatus.deleted, PostStatus.hidden]),
            )
            .order_by(ReviewQueueItem.created_at.desc())
        ).all()
        membership = None
        if user is not None:
            from app.services.membership_service import MembershipService

            membership = MembershipService(self.db)
        return [
            ReviewQueueRead(
                id=item.id,
                post_id=post.id,
                post_title=post.title,
                reason=item.reason,
                status=item.status,
                detail=item.detail,
                created_at=item.created_at,
            )
            for item, post in rows
            if membership is None or membership.review_item_visible(user, post)
        ]

    def pending_count(self, organization_id: UUID, user: User | None = None) -> int:
        if user is None:
            return (
                self.db.scalar(
                    select(func.count())
                    .select_from(ReviewQueueItem)
                    .join(Post, Post.id == ReviewQueueItem.post_id)
                    .where(
                        ReviewQueueItem.organization_id == organization_id,
                        ReviewQueueItem.status == ReviewQueueStatus.pending,
                        Post.status.not_in([PostStatus.deleted, PostStatus.hidden]),
                    )
                )
                or 0
            )
        return len(self.list(organization_id, ReviewQueueStatus.pending, user=user))

    def resolve(
        self,
        item_id: UUID,
        organization_id: UUID,
        user_id: UUID,
        status: ReviewQueueStatus,
        detail: str | None,
    ) -> ReviewQueueItem:
        row = self.db.scalar(
            select(ReviewQueueItem).where(
                ReviewQueueItem.id == item_id,
                ReviewQueueItem.organization_id == organization_id,
            )
        )
        if not row:
            raise NotFoundError("Review item not found")
        row.status = status
        row.detail = detail
        row.resolved_by = user_id
        row.resolved_at = datetime.now(timezone.utc)
        if status == ReviewQueueStatus.excluded:
            post = self.db.get(Post, row.post_id)
            if post:
                post.status = PostStatus.hidden
        elif status == ReviewQueueStatus.resolved:
            self._promote_keywords_for_post(row.post_id)
        self.db.commit()
        self.db.refresh(row)
        return row

    def suggest_keywords(self, item_id: UUID, organization_id: UUID) -> dict:
        row = self.db.scalar(
            select(ReviewQueueItem).where(
                ReviewQueueItem.id == item_id,
                ReviewQueueItem.organization_id == organization_id,
            )
        )
        if not row:
            raise NotFoundError("Review item not found")
        post = self.db.get(Post, row.post_id)
        if not post or post.organization_id != organization_id:
            raise NotFoundError("Post not found")
        result = ClassificationService(self.db).suggest_keywords(post)
        return {"post_id": post.id, **result}

    def apply_keywords(
        self,
        item_id: UUID,
        organization_id: UUID,
        user_id: UUID,
        *,
        keyword_ids: list[UUID],
        new_keyword_names: list[str],
        category: str | None = None,
    ) -> tuple[list[str], list[UUID]]:
        row = self.db.scalar(
            select(ReviewQueueItem).where(
                ReviewQueueItem.id == item_id,
                ReviewQueueItem.organization_id == organization_id,
            )
        )
        if not row:
            raise NotFoundError("Review item not found")
        post = self.db.get(Post, row.post_id)
        if not post or post.organization_id != organization_id:
            raise NotFoundError("Post not found")
        if not keyword_ids and not new_keyword_names:
            raise BadRequestError("키워드를 하나 이상 선택하거나 입력해 주세요.")

        pending_ids = list(
            self.db.scalars(
                select(ReviewQueueItem.id).where(
                    ReviewQueueItem.post_id == post.id,
                    ReviewQueueItem.status == ReviewQueueStatus.pending,
                    ReviewQueueItem.reason.in_(
                        (ReviewQueueReason.no_keywords, ReviewQueueReason.extraction_failed)
                    ),
                )
            ).all()
        )

        linked = ClassificationService(self.db).apply_manual_keywords(
            post,
            keyword_ids=keyword_ids,
            new_keyword_names=new_keyword_names,
            category=category,
        )
        if linked and pending_ids:
            now = datetime.now(timezone.utc)
            for item_id in pending_ids:
                item = self.db.get(ReviewQueueItem, item_id)
                if not item:
                    continue
                item.status = ReviewQueueStatus.resolved
                item.resolved_by = user_id
                item.resolved_at = now
        self.db.commit()
        return linked, pending_ids if linked else []

    def _promote_keywords_for_post(self, post_id: UUID) -> None:
        """검수 완료 시 해당 기사에 연결된 후보 키워드를 활성화해 뉴스 탐색에 반영."""
        keywords = self.db.scalars(
            select(Keyword)
            .join(PostKeyword, PostKeyword.keyword_id == Keyword.id)
            .where(
                PostKeyword.post_id == post_id,
                Keyword.status == KeywordStatus.candidate,
            )
        ).all()
        for keyword in keywords:
            keyword.status = KeywordStatus.active
