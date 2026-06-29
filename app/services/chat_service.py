import re
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.models.ai_output import AIOutput
from app.models.enums import PostStatus
from app.models.post import Post
from app.schemas.chat import ChatAskResponse, ChatCitation
from app.search.post_content import mget_post_contents
from app.search.post_search import search_post_ids
from app.services.llm_client import get_llm_client

_REFUSAL_REPLY = (
    "그 질문은 MINT가 다루기 어려운 주제예요.\n\n"
    "EV·충전 인프라, CSMS, MINT에 수집된 기사·게시판 관련 질문을 해 보시면 "
    "수집 자료를 바탕으로 답변해 드릴게요."
)

_CONFIRM_GENERAL_REPLY = (
    "MINT 수집 자료에서 이 질문과 직접 맞는 게시글은 찾지 못했어요.\n\n"
    "일반 지식으로 답변해 드릴까요?\n"
    "(MINT 게시글 출처가 아닌 참고 답변입니다)"
)

_CONFIRM_EV_GENERAL_REPLY = (
    "MINT 수집 기사에서 바로 맞는 글은 적지만, EV·충전 일반 지식으로 "
    "답변해 드릴까요?\n"
    "(수집 자료 인용이 아닌 참고 답변입니다)"
)

# 명백한 무관 주제만 사전 차단 (LLM 호출 전)
_OFF_TOPIC_HINTS = (
    "날씨",
    "레시피",
    "요리",
    "연예인",
    "아이돌",
    "로또",
    "운세",
    "다이어트",
    "게임 공략",
    "연애 상담",
)

_META_HINTS = (
    "mint",
    "뭐 할",
    "무엇을 할",
    "도움",
    "사용법",
    "기능",
    "게시판",
    "리포트",
    "챗봇",
)

_EV_HINTS = (
    "ev",
    "전기차",
    "전기",
    "충전",
    "ocpp",
    "csms",
    "cpo",
    "emsp",
    "무공해",
    "배터리",
    "charging",
    "charger",
    "v2g",
    "플릿",
    "fleet",
    "정책",
    "규제",
    "인프라",
    "모빌리티",
    "e-mobility",
    "iso 15118",
    "로밍",
    "충전소",
    "충전기",
    "신재생",
    "전력",
    "수소",
    "택시",
    "버스",
    "트럭",
)

_BROAD_EV_QUERIES = (
    "최근",
    "동향",
    "요약",
    "이슈",
    "뉴스",
    "변화",
    "정리",
    "알려",
    "트렌드",
    "현황",
    "overview",
    "summary",
)


class ChatService:
    def __init__(self, db: Session):
        self.db = db

    def ask(
        self,
        organization_id: UUID,
        message: str,
        *,
        allow_general: bool = False,
    ) -> ChatAskResponse:
        question = message.strip()
        route = self._route_question(question)

        if route == "off_topic":
            return ChatAskResponse(reply=_REFUSAL_REPLY, citations=[])

        client = get_llm_client()

        if allow_general:
            reply = client.answer_question_general(question)
            return ChatAskResponse(reply=reply, citations=[], source="general")

        if route == "meta":
            reply = client.answer_question_general(question)
            return ChatAskResponse(reply=reply, citations=[], source="general")

        matched = self._search_matched_posts(organization_id, question, route=route)
        if matched:
            context, citations = self._build_context(matched)
            reply = client.answer_question(question, context)
            return ChatAskResponse(reply=reply, citations=citations, source="mint")

        confirm_reply = (
            _CONFIRM_EV_GENERAL_REPLY if route == "ev" else _CONFIRM_GENERAL_REPLY
        )
        return ChatAskResponse(
            reply=confirm_reply,
            citations=[],
            needs_general_confirm=True,
        )

    def _route_question(self, question: str) -> str:
        blob = question.lower()

        if self._is_meta_question(blob):
            return "meta"

        if any(h in question for h in _OFF_TOPIC_HINTS):
            return "off_topic"

        if self._looks_ev_related(blob):
            return "ev"

        try:
            result = get_llm_client().classify_chat_question(question)
            route = result.get("route")
            if route in ("ev", "meta", "general", "off_topic"):
                return route
            # legacy is_allowed fallback
            if result.get("is_allowed", False):
                return "ev"
            return "general"
        except Exception:
            return "ev"

    def _looks_ev_related(self, blob: str) -> bool:
        return any(h in blob for h in _EV_HINTS)

    def _is_meta_question(self, blob: str) -> bool:
        return any(h in blob for h in _META_HINTS)

    def _is_broad_ev_query(self, question: str) -> bool:
        blob = question.lower()
        return any(h in blob for h in _BROAD_EV_QUERIES)

    def _search_matched_posts(
        self,
        organization_id: UUID,
        question: str,
        *,
        route: str = "ev",
        limit: int = 8,
    ) -> list[Post]:
        tokens = [t for t in re.split(r"[\s,?.!·]+", question) if len(t) >= 2][:5]
        seen: set[UUID] = set()
        posts: list[Post] = []

        if get_settings().search_uses_elasticsearch:
            matched_ids = search_post_ids(organization_id, question, limit=limit, min_token_len=2)
            if matched_ids:
                found = list(
                    self.db.scalars(
                        select(Post)
                        .options(joinedload(Post.source), joinedload(Post.ai_outputs))
                        .where(
                            Post.organization_id == organization_id,
                            Post.status != PostStatus.deleted,
                            Post.id.in_(matched_ids),
                        )
                    ).unique().all()
                )
                order = {pid: index for index, pid in enumerate(matched_ids)}
                found.sort(key=lambda post: order.get(post.id, 9999))
                for post in found:
                    if post.id not in seen:
                        seen.add(post.id)
                        posts.append(post)
        elif tokens:
            base = (
                select(Post)
                .options(joinedload(Post.source), joinedload(Post.ai_outputs))
                .outerjoin(AIOutput)
                .where(Post.organization_id == organization_id, Post.status != PostStatus.deleted)
            )

            for token in tokens:
                like = f"%{token}%"
                found = self.db.scalars(
                    base.where(
                        or_(
                            Post.title.ilike(like),
                            Post.raw_content.ilike(like),
                            AIOutput.summary.ilike(like),
                        )
                    )
                    .order_by(Post.collected_at.desc())
                    .limit(5)
                ).unique().all()
                for post in found:
                    if post.id not in seen:
                        seen.add(post.id)
                        posts.append(post)

        if route == "ev" and len(posts) < 3 and self._is_broad_ev_query(question):
            for post in self._fetch_recent_posts(organization_id, limit=limit):
                if post.id not in seen:
                    seen.add(post.id)
                    posts.append(post)

        return posts[:limit]

    def _fetch_recent_posts(self, organization_id: UUID, limit: int = 8) -> list[Post]:
        return list(
            self.db.scalars(
                select(Post)
                .options(joinedload(Post.source), joinedload(Post.ai_outputs))
                .where(
                    Post.organization_id == organization_id,
                    Post.status != PostStatus.deleted,
                )
                .order_by(Post.collected_at.desc())
                .limit(limit)
            ).unique().all()
        )

    def _build_context(self, posts: list[Post]) -> tuple[str, list[ChatCitation]]:
        blocks: list[str] = []
        citations: list[ChatCitation] = []
        contents = mget_post_contents(self.db, [post.id for post in posts])

        for post in posts:
            content = contents.get(post.id)
            summary = content.summary if content else None
            if not summary and post.ai_outputs:
                latest = max(post.ai_outputs, key=lambda o: o.created_at)
                if (latest.summary or "").strip() not in ("", " "):
                    summary = latest.summary
            original_url = content.original_url if content else post.original_url
            blocks.append(
                f"- 제목: {post.title}\n"
                f"  URL: {original_url or '(없음)'}\n"
                f"  AI요약: {summary or '(없음)'}\n"
                f"  중요도: {post.importance.value}"
            )
            citations.append(
                ChatCitation(
                    post_id=post.id,
                    title=post.title,
                    url=original_url,
                    summary=summary,
                )
            )

        return "\n\n".join(blocks), citations
