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
        for term in edition.topic_terms or []:
            needle = (term or "").strip()
            key = needle.lower()
            if len(needle) >= 2 and key not in seen:
                seen.add(key)
                terms.append(needle)
    keywords = db.scalars(
        select(Keyword).where(
            Keyword.organization_id == organization_id,
            Keyword.scope == KeywordScope.organization,
            Keyword.is_curated.is_(True),
            Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
        )
    ).all()
    for keyword in keywords:
        candidates = [keyword.name, *(keyword.aliases or [])]
        for term in candidates:
            needle = (term or "").strip()
            key = needle.lower()
            if len(needle) >= 2 and key not in seen:
                seen.add(key)
                terms.append(needle)
    return terms
