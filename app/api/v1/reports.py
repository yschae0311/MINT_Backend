from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import ForbiddenError
from app.core.permissions import require_admin, require_edition_editor_any
from app.core.security import get_current_user
from app.models.enums import JobType
from app.models.user import User
from app.schemas.job import JobRead
from app.schemas.report import DailyReportDetail, DailyReportRead, ReportGenerateRequest
from app.schemas.slack import SlackTestResponse
from app.services.job_service import JobService, dispatch_task
from app.services.membership_service import MembershipService, is_org_admin
from app.services.report_service import ReportService
from app.services.slack_service import SlackService
from app.workers.tasks import generate_report_job_task

router = APIRouter()


@router.get("", response_model=list[DailyReportRead])
def list_reports(
    edition_id: UUID | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership = MembershipService(db)
    if edition_id:
        membership.assert_view(user, edition_id)
        return ReportService(db).list_reports(user.organization_id, edition_id=edition_id)
    visible = membership.visible_edition_ids(user)
    return ReportService(db).list_reports(user.organization_id, edition_ids=visible)


@router.get("/latest", response_model=DailyReportRead | None)
def latest_report(
    edition_id: UUID = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    MembershipService(db).assert_view(user, edition_id)
    row = ReportService(db).latest_for_edition(user.organization_id, edition_id)
    if not row:
        return None
    return DailyReportRead.model_validate(row)


@router.get("/{report_id}", response_model=DailyReportDetail)
def get_report(
    report_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    report = ReportService(db).get_report(report_id, user.organization_id)
    if getattr(report, "edition_id", None):
        MembershipService(db).assert_view(user, report.edition_id)
    return report


@router.post("/generate", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def generate_report(
    data: ReportGenerateRequest,
    user: User = Depends(require_edition_editor_any),
    db: Session = Depends(get_db),
):
    if data.edition_id:
        MembershipService(db).assert_editor(user, data.edition_id)
    else:
        editor_ids = MembershipService(db).editor_edition_ids(user)
        if not editor_ids:
            raise ForbiddenError("분야 편집 권한이 없습니다.")
        if not is_org_admin(user) and len(editor_ids) == 1:
            data.edition_id = next(iter(editor_ids))
    label = (
        f"데일리 리포트 생성 · {data.report_date}"
        if data.report_date
        else "데일리 리포트 생성"
    )
    jobs = JobService(db)
    jobs.require_idle(user.organization_id)
    job = jobs.create_job(
        user.organization_id,
        JobType.generate_report,
        label,
        triggered_by=user.id,
        progress_total=1,
    )
    db.commit()
    report_date = data.report_date.isoformat() if data.report_date else None
    dispatch_task(
        generate_report_job_task,
        str(job.id),
        str(user.organization_id),
        report_date,
        str(data.edition_id) if data.edition_id else None,
        db=db,
    )
    return JobRead.model_validate(job)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(
    report_id: UUID,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ReportService(db).delete_report(report_id, user.organization_id)


@router.post("/{report_id}/send-slack", response_model=SlackTestResponse)
def send_report_slack(
    report_id: UUID,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return SlackService(db).send_report(report_id, user.organization_id)
