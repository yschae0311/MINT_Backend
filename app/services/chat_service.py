import re
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.ai_output import AIOutput
from app.models.enums import PostStatus
from app.models.post import Post
from app.schemas.chat import ChatAskResponse, ChatCitation
from app.services.llm_client import get_llm_client

_REFUSAL_REPLY = (
    "MINT AI는 EV·충전 인프라·CSMS와 MINT에 수집된 자료에 관한 질문만 답변합니다.\n\n"
    "예시:\n"
    "· 최근 충전 인프라 이슈 요약해줘\n"
    "· OCPP 2.0.1 관련 최근 동향은?\n"
    "· 중요 게시판 최근 정책 변화 알려줘"
)

_CONFIRM_GENERAL_REPLY = (
    "MINT에 수집된 자료에서는 이 질문과 직접 관련된 게시글을 찾지 못했습니다.\n\n"
    "Gemini의 EV·충전 일반 지식으로 답변해 드릴까요?\n"
    "(MINT 게시글 출처가 아닌 참고 답변입니다)"
)

# Cheap pre-filter before LLM guard (saves tokens)
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
    "번역해줘",
    "시 써",
    "소설 써",
    "주식 추천",
    "비트코인",
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
    "전기",
    "충전",
    "ocpp",
    "csms",
    "cpo",
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
        blocked = self._check_guard(question)
        if blocked:
            return ChatAskResponse(reply=blocked, citations=[])

        client = get_llm_client()

        if allow_general:
            reply = client.answer_question_general(question)
            return ChatAskResponse(reply=reply, citations=[], source="general")

        matched = self._search_matched_posts(organization_id, question)
        if not matched and not allow_general:
            if self._is_meta_question(question.lower()):
                reply = client.answer_question_general(question)
                return ChatAskResponse(reply=reply, citations=[], source="general")
            return ChatAskResponse(
                reply=_CONFIRM_GENERAL_REPLY,
                citations=[],
                needs_general_confirm=True,
            )

        context, citations = self._build_context(matched)
        reply = client.answer_question(question, context)
        return ChatAskResponse(reply=reply, citations=citations, source="mint")

    def _check_guard(self, question: str) -> str | None:
        blob = question.lower()

        if self._is_meta_question(blob):
            return None

        if any(h in question for h in _OFF_TOPIC_HINTS):
            return _REFUSAL_REPLY

        if any(h in blob for h in _EV_HINTS):
            return None

        try:
            result = get_llm_client().classify_chat_question(question)
            if result.get("is_allowed", False):
                return None
        except Exception:
            return _REFUSAL_REPLY

        return _REFUSAL_REPLY

    def _is_meta_question(self, blob: str) -> bool:
        return any(h in blob for h in _META_HINTS)

    def _search_matched_posts(
        self, organization_id: UUID, question: str, limit: int = 8
    ) -> list[Post]:
        tokens = [t for t in re.split(r"[\s,?.!·]+", question) if len(t) >= 2][:5]
        if not tokens:
            return []

        seen: set[UUID] = set()
        posts: list[Post] = []

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

        return posts[:limit]

    def _build_context(self, posts: list[Post]) -> tuple[str, list[ChatCitation]]:
        blocks: list[str] = []
        citations: list[ChatCitation] = []

        for post in posts:
            summary = None
            if post.ai_outputs:
                latest = max(post.ai_outputs, key=lambda o: o.created_at)
                summary = latest.summary
            blocks.append(
                f"- 제목: {post.title}\n"
                f"  URL: {post.original_url or '(없음)'}\n"
                f"  AI요약: {summary or '(없음)'}\n"
                f"  중요도: {post.importance.value}"
            )
            citations.append(
                ChatCitation(
                    post_id=post.id,
                    title=post.title,
                    url=post.original_url,
                    summary=summary,
                )
            )

        return "\n\n".join(blocks), citations
