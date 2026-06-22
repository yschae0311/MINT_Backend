from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.enums import SourceType, TrustLevel
from app.models.source import Source
from app.schemas.source import SourceCreate, SourceRead, SourceUpdate
from app.services.community_sources import is_community_source_type


def _apply_community_defaults(data: dict) -> dict:
    source_type = data.get("source_type")
    if source_type and is_community_source_type(source_type):
        data["trust_level"] = TrustLevel.low
        data["reliability_score"] = 45
        data["auto_publish"] = False
        if not data.get("category") or data.get("category") == "general":
            data["category"] = "커뮤니티/현장"
    return data


class SourceService:
    def __init__(self, db: Session):
        self.db = db

    def list_sources(self, organization_id: UUID) -> list[SourceRead]:
        sources = self.db.scalars(
            select(Source)
            .where(Source.organization_id == organization_id)
            .where(Source.name != "__community_submit__")
            .order_by(Source.name)
        ).all()
        return [SourceRead.model_validate(s) for s in sources]

    def get_source(self, source_id: UUID, organization_id: UUID) -> SourceRead:
        source = self._get_or_404(source_id, organization_id)
        return SourceRead.model_validate(source)

    def create_source(self, organization_id: UUID, data: SourceCreate) -> SourceRead:
        payload = data.model_dump()
        if is_community_source_type(payload.get("source_type", SourceType.rss)):
            payload = _apply_community_defaults(payload)
        source = Source(organization_id=organization_id, **payload)
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)
        return SourceRead.model_validate(source)

    def update_source(self, source_id: UUID, organization_id: UUID, data: SourceUpdate) -> SourceRead:
        source = self._get_or_404(source_id, organization_id)
        updates = data.model_dump(exclude_unset=True)

        if "source_type" in updates:
            was_community = is_community_source_type(source.source_type)
            will_be_community = is_community_source_type(updates["source_type"])
            if was_community != will_be_community:
                raise BadRequestError("소스 유형은 공식↔커뮤니티 간 변경할 수 없습니다.")

        effective_type = updates.get("source_type", source.source_type)
        if is_community_source_type(effective_type):
            updates = {
                **updates,
                **_apply_community_defaults(
                    {
                        "source_type": effective_type,
                        "category": updates.get("category", source.category),
                    }
                ),
            }

        for field, value in updates.items():
            setattr(source, field, value)
        self.db.commit()
        self.db.refresh(source)
        return SourceRead.model_validate(source)

    def delete_source(self, source_id: UUID, organization_id: UUID) -> None:
        source = self._get_or_404(source_id, organization_id)
        self.db.delete(source)
        self.db.commit()

    def _get_or_404(self, source_id: UUID, organization_id: UUID) -> Source:
        source = self.db.get(Source, source_id)
        if not source or source.organization_id != organization_id:
            raise NotFoundError("Source not found")
        return source
