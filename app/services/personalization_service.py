from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import BadRequestError, NotFoundError
from app.core.config import get_settings
from app.models.ai_output import AIOutput
from app.models.enums import (
    Importance,
    KeywordMatchMethod,
    KeywordScope,
    KeywordStatus,
    PostStatus,
    ReviewQueueReason,
    ReviewQueueStatus,
)
from app.models.personalization import (
    Keyword,
    NewsCategory,
    PersonalReport,
    PersonalReportItem,
    PersonalReportView,
    PostKeyword,
    ReviewQueueItem,
    UserKeywordSubscription,
)
from app.models.post import Post
from app.models.user import User
from app.schemas.personalization import (
    MatchedKeyword,
    NewsItem,
    NewsPage,
    PersonalReportItemRead,
    PersonalReportRead,
    ReviewQueueRead,
)
from app.search.post_content import PostContent, get_post_content, mget_post_contents, legacy_pg_content_enabled, sync_post_metadata
from app.search.post_search_query import PostSearchFilters, load_posts_ordered, search_posts
from app.services.llm_client import get_llm_client

KST = ZoneInfo("Asia/Seoul")
_KEYWORD_AUTO_ACTIVE_MIN = 0.6
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


def normalize_keyword(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip().casefold()
    return re.sub(r"\s+", " ", value)


def keyword_status_for_confidence(confidence: float) -> KeywordStatus:
    return (
        KeywordStatus.active
        if confidence >= _KEYWORD_AUTO_ACTIVE_MIN
        else KeywordStatus.candidate
    )


class TaxonomyService:
    def __init__(self, db: Session):
        self.db = db

    def ensure_defaults(self, organization_id: UUID) -> None:
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
                )
                self.db.add(row)
                self.db.flush()
                categories[normalized] = row

        existing = {
            row.normalized_name
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
                if normalized in existing:
                    continue
                self.db.add(
                    Keyword(
                        organization_id=organization_id,
                        category_id=category.id,
                        name=name,
                        normalized_name=normalized,
                        aliases=[],
                        scope=KeywordScope.organization,
                        status=KeywordStatus.active,
                    )
                )
                existing.add(normalized)
        self.db.flush()

    def list_categories(self, organization_id: UUID) -> list[NewsCategory]:
        return list(
            self.db.scalars(
                select(NewsCategory)
                .where(
                    NewsCategory.organization_id == organization_id,
                    NewsCategory.is_active.is_(True),
                )
                .order_by(NewsCategory.sort_order, NewsCategory.name)
            ).all()
        )

    def list_keywords(self, user: User) -> list[Keyword]:
        return list(
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
                    Keyword.status,
                    Keyword.name,
                )
            ).all()
        )

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
        for keyword in keywords:
            category_name = category_names.get(keyword.category_id) or "미분류"
            grouped[category_name].append(keyword.name)
        lines = [
            "## 조직 키워드 참고 (가능하면 아래 용어를 우선 매칭, 없으면 신규 제안)"
        ]
        seen: set[str] = set()
        for category in categories:
            names = grouped.get(category.name)
            if not names:
                continue
            seen.add(category.name)
            lines.append(f"- {category.name}: {', '.join(names[:40])}")
        for category_name in sorted(grouped):
            if category_name in seen:
                continue
            lines.append(
                f"- {category_name}: {', '.join(grouped[category_name][:40])}"
            )
        return "\n".join(lines)

    def selected_ids(self, user_id: UUID) -> set[UUID]:
        return set(
            self.db.scalars(
                select(UserKeywordSubscription.keyword_id).where(
                    UserKeywordSubscription.user_id == user_id
                )
            ).all()
        )

    def set_subscriptions(self, user: User, keyword_ids: list[UUID]) -> list[Keyword]:
        unique_ids = list(dict.fromkeys(keyword_ids))
        if len(unique_ids) < 3:
            raise BadRequestError("관심 키워드는 최소 3개를 선택해야 합니다.")
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
        self.db.execute(
            delete(UserKeywordSubscription).where(
                UserKeywordSubscription.user_id == user.id
            )
        )
        for keyword in allowed:
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
        posts = self.db.scalars(
            select(Post)
            .options(joinedload(Post.ai_outputs))
            .where(
                Post.organization_id == user.organization_id,
                Post.status.not_in([PostStatus.deleted, PostStatus.hidden]),
            )
        ).unique().all()
        terms = [keyword.name, *(keyword.aliases or [])]
        for post in posts:
            blob = normalize_keyword(
                f"{post.title} {post.raw_content} "
                + " ".join(output.summary for output in post.ai_outputs if output.summary)
            )
            if not any(normalize_keyword(term) in blob for term in terms if normalize_keyword(term)):
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
        row = Keyword(
            organization_id=organization_id,
            category_id=category_id,
            name=name.strip(),
            normalized_name=normalized,
            aliases=[a.strip() for a in aliases or [] if a.strip()],
            scope=KeywordScope.organization,
            status=status,
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
        category_by_name = {normalize_keyword(c.name): c for c in categories}
        category_name = (merged.get("category") or post.category or "기타").strip()
        category = category_by_name.get(normalize_keyword(category_name))
        if not category:
            category = category_by_name.get(normalize_keyword("기타"))
        post.category = category.name if category else "기타"

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
        seen: set[str] = set()
        for name, confidence, matched_keyword in sorted(
            candidates,
            key=lambda item: item[1],
            reverse=True,
        ):
            normalized = normalize_keyword(name)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            keyword = matched_keyword or next(
                (k for k in organization_keywords if k.normalized_name == normalized), None
            )
            if not keyword:
                status = keyword_status_for_confidence(confidence)
                keyword = Keyword(
                    organization_id=post.organization_id,
                    category_id=category.id if category else None,
                    name=name.strip()[:128],
                    normalized_name=normalized[:128],
                    aliases=[],
                    scope=KeywordScope.organization,
                    status=status,
                )
                self.db.add(keyword)
                self.db.flush()
                all_keywords.append(keyword)
                organization_keywords.append(keyword)
                if status == KeywordStatus.candidate:
                    review_reasons.append(ReviewQueueReason.new_keyword)
            else:
                keyword.usage_count = int(keyword.usage_count or 0) + 1
            self.db.add(
                PostKeyword(
                    post_id=post.id,
                    keyword_id=keyword.id,
                    confidence=max(0.0, min(confidence, 1.0)),
                    matched_by=KeywordMatchMethod.ai if keywords_from_ai else KeywordMatchMethod.alias,
                )
            )
            linked_names.append(keyword.name)
            if len(linked_names) >= 5:
                break

        confidence = float(merged.get("confidence") or self._latest_confidence(post) or 0)
        if not linked_names:
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
        query: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        page: int = 1,
        size: int = 20,
    ) -> NewsPage:
        selected = set(keyword_ids or [])
        if personalized and not selected:
            selected = TaxonomyService(self.db).selected_ids(user.id)
        if personalized and not selected:
            return NewsPage(items=[], total=0, page=page, size=size, pages=1)

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
        if selected:
            q = q.join(PostKeyword, PostKeyword.post_id == Post.id).where(
                PostKeyword.keyword_id.in_(selected)
            )
        if category:
            q = q.where(Post.category == category)
        if importance:
            q = q.where(Post.importance == importance)
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
        items = [
            self._to_item(post, selected, user.id, contents.get(post.id))
            for post in posts
        ]
        if personalized:
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
        items = [
            self._to_item(
                post,
                selected,
                user.id,
                contents.get(post.id),
                hit=hit_by_id.get(post.id),
            )
            for post in posts
        ]
        if personalized:
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
        return NewsItem(
            id=post.id,
            title=post.title,
            source_name=post.source.name if post.source else None,
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


class PersonalReportService:
    def __init__(self, db: Session):
        self.db = db
        self.news = PersonalizedNewsService(db)

    def generate_for_user(self, user: User, target: date | None = None) -> PersonalReport | None:
        target = target or datetime.now(KST).date()
        selected = TaxonomyService(self.db).selected_ids(user.id)
        if len(selected) < 3:
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

    def pending_count(self, organization_id: UUID) -> int:
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

    def list(self, organization_id: UUID, status: ReviewQueueStatus) -> list[ReviewQueueRead]:
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
        ]

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
