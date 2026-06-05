from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.report import DailyReportDetail, DailyReportRead, ReportGenerateRequest
from app.schemas.slack import SlackTestResponse
from app.services.report_service import ReportService
from app.services.slack_service import SlackService

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


@router.post("/generate", response_model=DailyReportDetail)
def generate_report(
    data: ReportGenerateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 수동 생성: 날짜 미지정 시 오늘(KST) 기준. 스케줄러는 prefer_yesterday=True.
    return ReportService(db).generate(
        user.organization_id,
        data.report_date,
        prefer_yesterday=False,
    )


@router.post("/{report_id}/send-slack", response_model=SlackTestResponse)
def send_report_slack(
    report_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return SlackService(db).send_report(report_id, user.organization_id)
