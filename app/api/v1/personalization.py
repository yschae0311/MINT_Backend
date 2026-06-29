from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_admin
from app.core.security import get_current_user
from app.models.enums import Importance, JobType, KeywordMatchMethod, KeywordStatus, ReviewQueueStatus
from app.models.user import User
from app.schemas.job import JobRead
from app.schemas.personalization import (
    CategoryRead,
    CategoryWrite,
    KeywordCreate,
    KeywordRead,
    KeywordMergeRequest,
    KeywordSuggestResponse,
    KeywordUpdate,
    KeywordSubscriptionUpdate,
    NewsPage,
    PersonalReportRead,
    PersonalReportViewUpdate,
    ReclassifyResponse,
    ReviewQueueKeywordsApply,
    ReviewQueueKeywordsApplyResponse,
    ReviewQueueRead,
    ReviewQueueResolve,
)
from app.schemas.report import ReportGenerateRequest
from app.services.personalization_service import (
    ClassificationService,
    PersonalReportService,
    PersonalizedNewsService,
    ReviewQueueService,
    TaxonomyService,
)
from app.models.personalization import NewsCategory
from app.models.personalization import Keyword, PostKeyword, UserKeywordSubscription
from app.models.post import Post
from app.models.source import Source
from app.core.exceptions import BadRequestError, NotFoundError

router = APIRouter()


def _keyword_read(row, selected: set[UUID]) -> KeywordRead:
    data = KeywordRead.model_validate(row)
    data.selected = row.id in selected
    return data


@router.get("/categories", response_model=list[CategoryRead])
def list_categories(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = TaxonomyService(db)
    service.ensure_defaults(user.organization_id)
    db.commit()
    return service.list_categories(user.organization_id)


@router.post("/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(
    data: CategoryWrite,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.services.personalization_service import normalize_keyword

    row = NewsCategory(
        organization_id=user.organization_id,
        name=data.name.strip(),
        normalized_name=normalize_keyword(data.name),
        sort_order=data.sort_order,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/keywords", response_model=list[KeywordRead])
def list_keywords(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = TaxonomyService(db)
    service.ensure_defaults(user.organization_id)
    db.commit()
    selected = service.selected_ids(user.id)
    return [_keyword_read(row, selected) for row in service.list_keywords(user)]


@router.post("/keywords", response_model=KeywordRead, status_code=status.HTTP_201_CREATED)
def create_standard_keyword(
    data: KeywordCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = TaxonomyService(db).create_standard_keyword(
        user.organization_id,
        data.name,
        category_id=data.category_id,
        aliases=data.aliases,
    )
    return _keyword_read(row, set())


@router.post("/keywords/{keyword_id}/promote", response_model=KeywordRead)
def promote_keyword(
    keyword_id: UUID,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = TaxonomyService(db).promote_keyword(keyword_id, user.organization_id)
    return _keyword_read(row, set())


@router.patch("/keywords/{keyword_id}", response_model=KeywordRead)
def update_keyword(
    keyword_id: UUID,
    data: KeywordUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.services.personalization_service import normalize_keyword

    row = db.get(Keyword, keyword_id)
    if not row or row.organization_id != user.organization_id:
        raise NotFoundError("Keyword not found")
    values = data.model_dump(exclude_unset=True)
    if "name" in values:
        row.name = values["name"].strip()
        row.normalized_name = normalize_keyword(row.name)
    for field in ("category_id", "aliases", "status"):
        if field in values:
            setattr(row, field, values[field])
    db.commit()
    db.refresh(row)
    return _keyword_read(row, TaxonomyService(db).selected_ids(user.id))


@router.post("/keywords/{target_keyword_id}/merge", response_model=KeywordRead)
def merge_keyword(
    target_keyword_id: UUID,
    data: KeywordMergeRequest,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(Keyword, target_keyword_id)
    source = db.get(Keyword, data.source_keyword_id)
    if (
        not target
        or not source
        or target.organization_id != user.organization_id
        or source.organization_id != user.organization_id
        or target.id == source.id
    ):
        raise NotFoundError("Keyword not found")
    post_ids = db.scalars(
        select(PostKeyword.post_id).where(PostKeyword.keyword_id == source.id)
    ).all()
    for post_id in post_ids:
        exists = db.scalar(
            select(PostKeyword).where(
                PostKeyword.post_id == post_id,
                PostKeyword.keyword_id == target.id,
            )
        )
        if not exists:
            db.add(
                PostKeyword(
                    post_id=post_id,
                    keyword_id=target.id,
                    confidence=1.0,
                    matched_by=KeywordMatchMethod.admin,
                )
            )
    user_ids = db.scalars(
        select(UserKeywordSubscription.user_id).where(
            UserKeywordSubscription.keyword_id == source.id
        )
    ).all()
    for user_id in user_ids:
        exists = db.scalar(
            select(UserKeywordSubscription).where(
                UserKeywordSubscription.user_id == user_id,
                UserKeywordSubscription.keyword_id == target.id,
            )
        )
        if not exists:
            db.add(UserKeywordSubscription(user_id=user_id, keyword_id=target.id))
    db.query(PostKeyword).filter(PostKeyword.keyword_id == source.id).delete()
    db.query(UserKeywordSubscription).filter(
        UserKeywordSubscription.keyword_id == source.id
    ).delete()
    source.status = KeywordStatus.archived
    target.aliases = list(dict.fromkeys([*(target.aliases or []), source.name, *(source.aliases or [])]))
    db.commit()
    return _keyword_read(target, TaxonomyService(db).selected_ids(user.id))


@router.get("/users/me/keywords", response_model=list[KeywordRead])
def my_keywords(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = TaxonomyService(db)
    selected = service.selected_ids(user.id)
    return [
        _keyword_read(row, selected)
        for row in service.list_keywords(user)
        if row.id in selected
    ]


@router.put("/users/me/keywords", response_model=list[KeywordRead])
def update_my_keywords(
    data: KeywordSubscriptionUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = TaxonomyService(db).set_subscriptions(user, data.keyword_ids)
    selected = {row.id for row in rows}
    return [_keyword_read(row, selected) for row in rows]


@router.post("/users/me/keywords/custom", response_model=KeywordRead, status_code=status.HTTP_201_CREATED)
def create_my_keyword(
    data: KeywordCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = TaxonomyService(db).create_custom_keyword(user, data.name)
    return _keyword_read(row, {row.id})


@router.get("/feed", response_model=NewsPage)
def personal_feed(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return PersonalizedNewsService(db).list_news(
        user,
        personalized=True,
        page=page,
        size=size,
    )


@router.get("/news", response_model=NewsPage)
def all_news(
    keyword_ids: list[UUID] | None = Query(default=None),
    category: str | None = None,
    importance: Importance | None = None,
    q: str | None = Query(default=None, max_length=200),
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return PersonalizedNewsService(db).list_news(
        user,
        personalized=False,
        keyword_ids=keyword_ids,
        category=category,
        importance=importance,
        query=q,
        date_from=date_from,
        date_to=date_to,
        page=page,
        size=size,
    )


@router.get("/personal-reports", response_model=list[PersonalReportRead])
def list_personal_reports(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return PersonalReportService(db).list_reports(user)


@router.get("/personal-reports/latest", response_model=PersonalReportRead | None)
def latest_personal_report(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return PersonalReportService(db).latest(user)


@router.post("/personal-reports/generate", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def generate_personal_report(
    data: ReportGenerateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.job_service import JobService, dispatch_task
    from app.workers.tasks import generate_personal_report_job_task

    selected = TaxonomyService(db).selected_ids(user.id)
    if len(selected) < 3:
        raise BadRequestError("관심 키워드를 3개 이상 선택해야 개인 리포트를 생성할 수 있습니다.")

    label = (
        f"개인 리포트 생성 · {data.report_date}"
        if data.report_date
        else "개인 리포트 생성"
    )
    jobs = JobService(db)
    job = jobs.create_job(
        user.organization_id,
        JobType.generate_personal_reports,
        label,
        triggered_by=user.id,
        progress_total=1,
    )
    db.commit()
    report_date = data.report_date.isoformat() if data.report_date else None
    dispatch_task(
        generate_personal_report_job_task,
        str(job.id),
        str(user.id),
        report_date,
        db=db,
    )
    return JobRead.model_validate(job)


@router.get("/personal-reports/{report_id}", response_model=PersonalReportRead)
def get_personal_report(
    report_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return PersonalReportService(db).get(report_id, user)


@router.post("/personal-reports/{report_id}/view", status_code=status.HTTP_204_NO_CONTENT)
def mark_personal_report_view(
    report_id: UUID,
    data: PersonalReportViewUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    PersonalReportService(db).mark_view(
        report_id,
        user,
        popup_seen=data.popup_seen,
        opened=data.opened,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/review-queue", response_model=list[ReviewQueueRead])
def list_review_queue(
    queue_status: ReviewQueueStatus = Query(ReviewQueueStatus.pending, alias="status"),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return ReviewQueueService(db).list(user.organization_id, queue_status)


@router.post("/review-queue/{item_id}/suggest-keywords", response_model=KeywordSuggestResponse)
def suggest_review_queue_keywords(
    item_id: UUID,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    result = ReviewQueueService(db).suggest_keywords(item_id, user.organization_id)
    return KeywordSuggestResponse(
        post_id=result["post_id"],
        category=result.get("category"),
        suggestions=result.get("suggestions") or [],
    )


@router.put("/review-queue/{item_id}/keywords", response_model=ReviewQueueKeywordsApplyResponse)
def apply_review_queue_keywords(
    item_id: UUID,
    data: ReviewQueueKeywordsApply,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models.personalization import ReviewQueueItem

    item = db.get(ReviewQueueItem, item_id)
    if not item or item.organization_id != user.organization_id:
        raise NotFoundError("Review item not found")
    linked, resolved_ids = ReviewQueueService(db).apply_keywords(
        item_id,
        user.organization_id,
        user.id,
        keyword_ids=data.keyword_ids,
        new_keyword_names=data.new_keyword_names,
        category=data.category,
    )
    return ReviewQueueKeywordsApplyResponse(
        post_id=item.post_id,
        linked_keywords=linked,
        resolved_queue_item_ids=resolved_ids,
    )


@router.post("/review-queue/reclassify-all", response_model=JobRead)
def reclassify_all_posts(
    limit: int = Query(500, ge=1, le=2000),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.services.job_service import JobService, dispatch_task
    from app.workers.tasks import classify_posts_job_task

    jobs = JobService(db)
    jobs.require_idle(user.organization_id)
    job = jobs.create_job(
        user.organization_id,
        JobType.classify_posts,
        "뉴스 전체 재분류",
        triggered_by=user.id,
    )
    db.commit()
    dispatch_task(
        classify_posts_job_task,
        str(job.id),
        str(user.organization_id),
        limit,
        db=db,
    )
    return JobRead.model_validate(job)


@router.post("/review-queue/{item_id}/block-source", status_code=status.HTTP_204_NO_CONTENT)
def block_review_item_source(
    item_id: UUID,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from app.models.personalization import ReviewQueueItem

    item = db.get(ReviewQueueItem, item_id)
    if not item or item.organization_id != user.organization_id:
        raise NotFoundError("Review item not found")
    post = db.get(Post, item.post_id)
    if post and post.source_id:
        source = db.get(Source, post.source_id)
        if source and source.organization_id == user.organization_id:
            source.is_active = False
    ReviewQueueService(db).resolve(
        item_id,
        user.organization_id,
        user.id,
        ReviewQueueStatus.excluded,
        "관리자가 소스를 차단했습니다.",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/review-queue/{item_id}", response_model=ReviewQueueRead)
def resolve_review_queue(
    item_id: UUID,
    data: ReviewQueueResolve,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = ReviewQueueService(db).resolve(
        item_id,
        user.organization_id,
        user.id,
        data.status,
        data.detail,
    )
    post = db.get(Post, row.post_id)
    return ReviewQueueRead(
        id=row.id,
        post_id=row.post_id,
        post_title=post.title if post else "",
        reason=row.reason,
        status=row.status,
        detail=row.detail,
        created_at=row.created_at,
    )


@router.post("/news/{post_id}/reclassify", response_model=ReclassifyResponse)
def reclassify_post(
    post_id: UUID,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    post = db.get(Post, post_id)
    if not post or post.organization_id != user.organization_id:
        raise NotFoundError("Post not found")
    names, reasons = ClassificationService(db).classify_post(post)
    db.commit()
    return ReclassifyResponse(
        post_id=post.id,
        category=post.category,
        keywords=names,
        review_reasons=reasons,
    )
