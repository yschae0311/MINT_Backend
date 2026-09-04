from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.edition import Edition, SourceEdition
from app.models.enums import KeywordScope, KeywordStatus
from app.models.personalization import Keyword
from app.models.source import Source
from app.schemas.edition import EditionCreate, EditionRead, EditionUpdate

EV_SLUG = "ev"
AUTONOMOUS_SLUG = "autonomous"

EV_TOPIC_TERMS = [
    "전기차",
    "충전",
    "OCPP",
    "CSMS",
    "충전기",
    "충전소",
    "e-mobility",
    "electric vehicle",
]
AUTONOMOUS_TOPIC_TERMS = [
    "자율주행",
    "ADAS",
    "로보택시",
    "autonomous driving",
    "robotaxi",
    "라이다",
    "lidar",
    "autonomous vehicle",
    "웨이모",
    "레벨4",
    "운행 허가",
]

DEFAULT_EDITIONS = (
    (EV_SLUG, "전기차·충전", 0, EV_TOPIC_TERMS),
    (AUTONOMOUS_SLUG, "자율주행", 1, AUTONOMOUS_TOPIC_TERMS),
)


def slugify_edition(value: str) -> str:
    parts: list[str] = []
    for ch in (value or "").strip().lower():
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            parts.append(ch)
        else:
            parts.append("-")
    cleaned = "".join(parts)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return (cleaned.strip("-") or "desk")[:64]


class EditionService:
    def __init__(self, db: Session):
        self.db = db

    def ensure_defaults(self, organization_id: UUID) -> list[Edition]:
        existing = {
            row.slug: row
            for row in self.db.scalars(
                select(Edition).where(Edition.organization_id == organization_id)
            ).all()
        }
        created = False
        for slug, name, order, terms in DEFAULT_EDITIONS:
            row = existing.get(slug)
            if row is None:
                row = Edition(
                    organization_id=organization_id,
                    slug=slug,
                    name=name,
                    sort_order=order,
                    is_active=True,
                    topic_terms=list(terms),
                )
                self.db.add(row)
                self.db.flush()
                existing[slug] = row
                created = True
            elif not row.topic_terms:
                row.topic_terms = list(terms)
        if created:
            self.db.flush()
        return self.list_editions(organization_id, active_only=False)

    def list_editions(self, organization_id: UUID, *, active_only: bool = True) -> list[Edition]:
        q = select(Edition).where(Edition.organization_id == organization_id)
        if active_only:
            q = q.where(Edition.is_active.is_(True))
        return list(
            self.db.scalars(q.order_by(Edition.sort_order, Edition.name)).all()
        )

    def get(self, edition_id: UUID, organization_id: UUID) -> Edition:
        row = self.db.get(Edition, edition_id)
        if not row or row.organization_id != organization_id:
            raise NotFoundError("Edition not found")
        return row

    def ev_edition(self, organization_id: UUID) -> Edition | None:
        self.ensure_defaults(organization_id)
        return self.db.scalar(
            select(Edition).where(
                Edition.organization_id == organization_id,
                Edition.slug == EV_SLUG,
            )
        )

    def allocate_slug(self, organization_id: UUID, name: str, requested: str | None) -> str:
        requested = (requested or "").strip().lower()
        if requested:
            exists = self.db.scalar(
                select(Edition.id).where(
                    Edition.organization_id == organization_id,
                    Edition.slug == requested,
                )
            )
            if exists:
                raise BadRequestError("같은 슬러그의 분야가 이미 있습니다.")
            return requested
        base = slugify_edition(name)
        candidate = base
        n = 2
        while self.db.scalar(
            select(Edition.id).where(
                Edition.organization_id == organization_id,
                Edition.slug == candidate,
            )
        ):
            suffix = f"-{n}"
            candidate = f"{base[: 64 - len(suffix)]}{suffix}"
            n += 1
            if n > 80:
                raise BadRequestError("분야 식별자를 만들지 못했습니다. 이름을 바꿔 주세요.")
        return candidate

    def create(self, organization_id: UUID, data: EditionCreate) -> Edition:
        slug = self.allocate_slug(organization_id, data.name, data.slug)
        max_order = self.db.scalar(
            select(func.max(Edition.sort_order)).where(Edition.organization_id == organization_id)
        )
        terms = [term.strip() for term in data.topic_terms if term.strip()][:40]
        row = Edition(
            organization_id=organization_id,
            slug=slug,
            name=data.name.strip(),
            sort_order=data.sort_order if data.sort_order is not None else int(max_order or -1) + 1,
            is_active=data.is_active,
            topic_terms=terms,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(self, edition_id: UUID, organization_id: UUID, data: EditionUpdate) -> Edition:
        row = self.get(edition_id, organization_id)
        updates = data.model_dump(exclude_unset=True)
        if "topic_terms" in updates and updates["topic_terms"] is not None:
            updates["topic_terms"] = [term.strip() for term in updates["topic_terms"] if term.strip()][:40]
        if "name" in updates and updates["name"]:
            updates["name"] = updates["name"].strip()
        for field, value in updates.items():
            setattr(row, field, value)
        self.db.commit()
        self.db.refresh(row)
        return row

    def tagged_source_counts(self, organization_id: UUID) -> dict[UUID, int]:
        rows = self.db.execute(
            select(SourceEdition.edition_id, func.count())
            .join(Source, Source.id == SourceEdition.source_id)
            .where(Source.organization_id == organization_id)
            .group_by(SourceEdition.edition_id)
        ).all()
        return {edition_id: int(count) for edition_id, count in rows}

    def untagged_active_source_count(self, organization_id: UUID) -> int:
        tagged = (
            select(SourceEdition.source_id)
            .join(Source, Source.id == SourceEdition.source_id)
            .where(Source.organization_id == organization_id)
        )
        return (
            self.db.scalar(
                select(func.count())
                .select_from(Source)
                .where(
                    Source.organization_id == organization_id,
                    Source.is_active.is_(True),
                    Source.name != "__community_submit__",
                    Source.id.not_in(tagged),
                )
            )
            or 0
        )

    def featured_keyword_counts(self, organization_id: UUID) -> dict[UUID, int]:
        rows = self.db.execute(
            select(Keyword.edition_id, func.count())
            .where(
                Keyword.organization_id == organization_id,
                Keyword.edition_id.is_not(None),
                Keyword.is_featured.is_(True),
                Keyword.scope == KeywordScope.organization,
                Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
            )
            .group_by(Keyword.edition_id)
        ).all()
        return {edition_id: int(count) for edition_id, count in rows if edition_id}

    def to_read(self, row: Edition, *, tagged_sources: int = 0, featured_keywords: int = 0, untagged_sources: int = 0) -> EditionRead:
        missing = tagged_sources == 0 and untagged_sources == 0
        return EditionRead(
            id=row.id,
            organization_id=row.organization_id,
            slug=row.slug,
            name=row.name,
            sort_order=row.sort_order,
            is_active=row.is_active,
            topic_terms=list(row.topic_terms or []),
            tagged_source_count=tagged_sources,
            featured_keyword_count=featured_keywords,
            missing_sources=missing,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_reads(self, organization_id: UUID, *, active_only: bool = True) -> list[EditionRead]:
        self.ensure_defaults(organization_id)
        rows = self.list_editions(organization_id, active_only=active_only)
        tagged = self.tagged_source_counts(organization_id)
        featured = self.featured_keyword_counts(organization_id)
        untagged = self.untagged_active_source_count(organization_id)
        return [
            self.to_read(
                row,
                tagged_sources=tagged.get(row.id, 0),
                featured_keywords=featured.get(row.id, 0),
                untagged_sources=untagged,
            )
            for row in rows
        ]

    def featured_keyword_ids(self, organization_id: UUID, edition_id: UUID) -> list[UUID]:
        return list(
            self.db.scalars(
                select(Keyword.id).where(
                    Keyword.organization_id == organization_id,
                    Keyword.edition_id == edition_id,
                    Keyword.is_featured.is_(True),
                    Keyword.scope == KeywordScope.organization,
                    Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
                )
            ).all()
        )

    def set_featured_keywords(
        self, organization_id: UUID, edition_id: UUID, keyword_ids: list[UUID]
    ) -> list[Keyword]:
        self.get(edition_id, organization_id)
        unique_ids = list(dict.fromkeys(keyword_ids))
        allowed = list(
            self.db.scalars(
                select(Keyword).where(
                    Keyword.organization_id == organization_id,
                    Keyword.id.in_(unique_ids) if unique_ids else Keyword.id.is_(None),
                    Keyword.scope == KeywordScope.organization,
                    Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
                )
            ).all()
        ) if unique_ids else []
        if unique_ids and len(allowed) != len(unique_ids):
            raise BadRequestError("선택할 수 없는 키워드가 포함되어 있습니다.")
        edition_keywords = list(
            self.db.scalars(
                select(Keyword).where(
                    Keyword.organization_id == organization_id,
                    Keyword.edition_id == edition_id,
                )
            ).all()
        )
        featured_ids = {row.id for row in allowed}
        for row in edition_keywords:
            row.is_featured = row.id in featured_ids
        for row in allowed:
            row.edition_id = edition_id
            row.is_featured = True
        self.db.commit()
        return list(
            self.db.scalars(
                select(Keyword)
                .where(
                    Keyword.organization_id == organization_id,
                    Keyword.edition_id == edition_id,
                    Keyword.is_featured.is_(True),
                )
                .order_by(Keyword.name)
            ).all()
        )

    def set_source_editions(self, source: Source, edition_ids: list[UUID] | None) -> None:
        self.db.execute(delete(SourceEdition).where(SourceEdition.source_id == source.id))
        if not edition_ids:
            return
        unique_ids = list(dict.fromkeys(edition_ids))
        allowed = list(
            self.db.scalars(
                select(Edition).where(
                    Edition.organization_id == source.organization_id,
                    Edition.id.in_(unique_ids),
                )
            ).all()
        )
        if len(allowed) != len(unique_ids):
            raise BadRequestError("선택할 수 없는 분야가 포함되어 있습니다.")
        for edition in allowed:
            self.db.add(SourceEdition(source_id=source.id, edition_id=edition.id))

    def edition_ids_for_source(self, source_id: UUID) -> list[UUID]:
        return list(
            self.db.scalars(
                select(SourceEdition.edition_id).where(SourceEdition.source_id == source_id)
            ).all()
        )
