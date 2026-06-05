from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.source import Source
from app.schemas.source import SourceCreate, SourceRead, SourceUpdate


class SourceService:
    def __init__(self, db: Session):
        self.db = db

    def list_sources(self, organization_id: UUID) -> list[SourceRead]:
        sources = self.db.scalars(
            select(Source).where(Source.organization_id == organization_id).order_by(Source.name)
        ).all()
        return [SourceRead.model_validate(s) for s in sources]

    def get_source(self, source_id: UUID, organization_id: UUID) -> SourceRead:
        source = self._get_or_404(source_id, organization_id)
        return SourceRead.model_validate(source)

    def create_source(self, organization_id: UUID, data: SourceCreate) -> SourceRead:
        source = Source(organization_id=organization_id, **data.model_dump())
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)
        return SourceRead.model_validate(source)

    def update_source(self, source_id: UUID, organization_id: UUID, data: SourceUpdate) -> SourceRead:
        source = self._get_or_404(source_id, organization_id)
        for field, value in data.model_dump(exclude_unset=True).items():
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
