from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.organization import Organization


class OrgSettingsService:
    def __init__(self, db: Session):
        self.db = db

    def _org(self, org_id: UUID) -> Organization:
        org = self.db.get(Organization, org_id)
        if not org:
            raise NotFoundError("Organization not found")
        return org

    @staticmethod
    def default_discovery_retention_days() -> int:
        return get_settings().discovery_pending_retention_days

    def discovery_pending_retention_days(self, org_id: UUID) -> int:
        org = self._org(org_id)
        if org.discovery_pending_retention_days is not None:
            return org.discovery_pending_retention_days
        return self.default_discovery_retention_days()

    def get_collection_settings(self, org_id: UUID) -> dict:
        default_days = self.default_discovery_retention_days()
        effective_days = self.discovery_pending_retention_days(org_id)
        org = self._org(org_id)
        return {
            "discovery_pending_retention_days": effective_days,
            "default_retention_days": default_days,
            "is_custom": org.discovery_pending_retention_days is not None,
        }

    def update_collection_settings(self, org_id: UUID, *, discovery_pending_retention_days: int) -> dict:
        if discovery_pending_retention_days < 0 or discovery_pending_retention_days > 365:
            raise BadRequestError("삭제 기한은 0~365일 사이로 설정할 수 있습니다.")
        org = self._org(org_id)
        org.discovery_pending_retention_days = discovery_pending_retention_days
        self.db.commit()
        self.db.refresh(org)
        return self.get_collection_settings(org_id)
