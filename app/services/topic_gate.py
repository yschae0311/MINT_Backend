"""Load active edition topic terms + curated keywords for ingest/display gates."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.edition import Edition
from app.models.enums import KeywordScope, KeywordStatus
from app.models.personalization import Keyword


def load_topic_terms(db: Session, organization_id: UUID) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    editions = db.scalars(
        select(Edition).where(
            Edition.organization_id == organization_id,
            Edition.is_active.is_(True),
        )
    ).all()
    for edition in editions:
        _extend_terms(terms, seen, edition.topic_terms or [])
    keywords = db.scalars(
        select(Keyword).where(
            Keyword.organization_id == organization_id,
            Keyword.scope == KeywordScope.organization,
            Keyword.is_curated.is_(True),
            Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
        )
    ).all()
    for keyword in keywords:
        _extend_terms(terms, seen, [keyword.name, *(keyword.aliases or [])])
    return terms


def load_edition_topic_terms(db: Session, organization_id: UUID, edition_id: UUID) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    edition = db.get(Edition, edition_id)
    if edition is None or edition.organization_id != organization_id:
        return terms
    _extend_terms(terms, seen, edition.topic_terms or [])
    keywords = db.scalars(
        select(Keyword).where(
            Keyword.organization_id == organization_id,
            Keyword.edition_id == edition_id,
            Keyword.scope == KeywordScope.organization,
            Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
        )
    ).all()
    for keyword in keywords:
        _extend_terms(terms, seen, [keyword.name, *(keyword.aliases or [])])
    return terms


def _extend_terms(terms: list[str], seen: set[str], values: list[str]) -> None:
    for term in values:
        needle = (term or "").strip()
        key = needle.lower()
        if len(needle) >= 2 and key not in seen:
            seen.add(key)
            terms.append(needle)
