from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.enums import JobType
from app.models.user import User
from app.schemas.job import JobRead
from app.schemas.report import DailyReportDetail, DailyReportRead, ReportGenerateRequest
from app.schemas.slack import SlackTestResponse
from app.services.job_service import JobService, dispatch_task
from app.services.report_service import ReportService
from app.services.slack_service import SlackService
from app.workers.tasks import generate_report_job_task

router = APIRouter()


@router.get("", response_model=list[DailyReportRead])
def list_reports(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ReportService(db).list_reports(user.organization_id)


@router.get("/{report_id}", response_model=DailyReportDetail)
def get_report(
    report_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ReportService(db).get_report(report_id, user.organization_id)


@router.post("/generate", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def generate_report(
    data: ReportGenerateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
        db=db,
    )
    return JobRead.model_validate(job)


@router.post("/{report_id}/send-slack", response_model=SlackTestResponse)
def send_report_slack(
    report_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return SlackService(db).send_report(report_id, user.organization_id)
