import json
import re
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import BadRequestError
from app.services.ev_relevance import is_obvious_junk, is_weak_topic_only, passes_keyword_gate

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise BadRequestError("LLM returned empty response")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError as exc:
                raise BadRequestError(f"LLM returned invalid JSON: {text[:200]}") from exc
        raise BadRequestError(f"LLM returned invalid JSON: {text[:200]}")


class LLMClient(ABC):
    @abstractmethod
    def summarize_post(self, title: str, content: str) -> dict:
        ...

    @abstractmethod
    def evaluate_discovery_candidate(self, title: str, content: str, url: str) -> dict:
        ...

    @abstractmethod
    def generate_daily_report(self, posts: list[dict], report_date: date) -> dict:
        ...

    @abstractmethod
    def answer_question(self, question: str, context: str) -> str:
        ...

    @abstractmethod
    def classify_chat_question(self, question: str) -> dict:
        ...

    @abstractmethod
    def answer_question_general(self, question: str) -> str:
        ...


class GeminiClient(LLMClient):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise BadRequestError("GEMINI_API_KEY is not configured")
        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)
        self._genai = genai
        self.summary_model = settings.gemini_summary_model
        self.report_model = settings.gemini_report_model

    def _generate(self, model_name: str, system: str, user: str, *, json_mode: bool = False) -> str:
        model = self._genai.GenerativeModel(model_name, system_instruction=system)
        kwargs: dict = {}
        if json_mode:
            kwargs["generation_config"] = self._genai.GenerationConfig(
                response_mime_type="application/json"
            )
        response = model.generate_content(user, **kwargs)
        text = response.text or ""
        if not text:
            finish = ""
            if getattr(response, "candidates", None):
                finish = str(getattr(response.candidates[0], "finish_reason", ""))
            raise BadRequestError(f"Gemini empty response (finish_reason={finish})")
        return text

    def summarize_post(self, title: str, content: str) -> dict:
        system = _load_prompt("post_summary_v1.md")
        user = f"Title: {title}\n\nContent:\n{content[:12000]}"
        return _parse_json(self._generate(self.summary_model, system, user, json_mode=True))

    def evaluate_discovery_candidate(self, title: str, content: str, url: str) -> dict:
        if is_obvious_junk(title, content, url) or not passes_keyword_gate(title, content, url):
            reason = (
                "일반 에너지·친환경 키워드만 있음"
                if is_weak_topic_only(title, content, url)
                else "EV/충전 직접 관련 신호 없음"
            )
            return {
                "is_relevant": False,
                "relevance_reason": reason,
                "summary": "",
                "impact": "",
                "action_items": [],
                "importance": "low",
                "confidence": 0.9,
            }
        system = _load_prompt("discovery_evaluate_v1.md")
        user = f"URL: {url}\nTitle: {title}\n\nContent:\n{content[:12000]}"
        return _parse_json(self._generate(self.summary_model, system, user, json_mode=True))

    def generate_daily_report(self, posts: list[dict], report_date: date) -> dict:
        system = _load_prompt("daily_report_v1.md")
        user = json.dumps(
            {"report_date": report_date.isoformat(), "posts": posts},
            ensure_ascii=False,
        )
        return _parse_json(self._generate(self.report_model, system, user, json_mode=True))

    def answer_question(self, question: str, context: str) -> str:
        system = _load_prompt("chat_assistant_v1.md")
        user = f"[참고 자료]\n{context[:14000]}\n\n[질문]\n{question}"
        return self._generate(self.summary_model, system, user)

    def classify_chat_question(self, question: str) -> dict:
        system = _load_prompt("chat_guard_v1.md")
        user = f"질문: {question[:1000]}"
        return _parse_json(self._generate(self.summary_model, system, user, json_mode=True))

    def answer_question_general(self, question: str) -> str:
        system = _load_prompt("chat_general_v1.md")
        user = f"[질문]\n{question}"
        return self._generate(self.summary_model, system, user)


class MockLLMClient(LLMClient):
    _EV_HINTS = (
        "ev",
        "전기차",
        "전기",
        "충전",
        "ocpp",
        "csms",
        "cpo",
        "무공해",
        "charging",
        "charger",
    )

    def _looks_ev_related(self, title: str, content: str, url: str) -> bool:
        return passes_keyword_gate(title, content, url)

    def summarize_post(self, title: str, content: str) -> dict:
        return {
            "summary": f"{title[:120]}에 대한 요약입니다.",
            "impact": "EV 충전 인프라 및 CSMS 운영에 참고할 만한 변화가 있을 수 있습니다.",
            "action_items": ["원문 확인", "관련 정책 모니터링"],
            "importance": "medium",
            "confidence": 0.75,
        }

    def evaluate_discovery_candidate(self, title: str, content: str, url: str) -> dict:
        relevant = self._looks_ev_related(title, content, url)
        if not relevant:
            return {
                "is_relevant": False,
                "relevance_reason": "EV/충전 관련 키워드가 없습니다.",
                "summary": "",
                "impact": "",
                "action_items": [],
                "importance": "low",
                "confidence": 0.4,
            }
        return {
            "is_relevant": True,
            "relevance_reason": "EV/충전 관련 키워드가 포함되어 있습니다.",
            "summary": f"{title[:120]} — EV·충전 관련 후보 기사입니다.",
            "impact": "충전 인프라·CSMS 운영 관점에서 확인이 필요합니다.",
            "action_items": ["원문 링크 확인", "관련 정책·표준 모니터링"],
            "importance": "medium",
            "confidence": 0.7,
        }

    def generate_daily_report(self, posts: list[dict], report_date: date) -> dict:
        recommendations = []
        for p in posts[:5]:
            board = p.get("board", "trusted")
            hint = "신규 발굴" if board == "discovery" else "핵심"
            recommendations.append(
                {
                    "title": p["title"][:60],
                    "why_read": f"{hint} 이슈로 내용 확인이 필요합니다.",
                    "related_post_ids": [p["id"]],
                    "importance": p.get("importance", "medium"),
                }
            )
        return {
            "summary": f"{report_date.isoformat()} 기준 {len(posts)}건 수집. EV·충전 동향을 짧게 정리했습니다.",
            "recommendations": recommendations or [
                {
                    "title": "주요 이슈 없음",
                    "why_read": "당일 수집 게시글이 없습니다.",
                    "related_post_ids": [],
                    "importance": "low",
                }
            ],
        }

    def classify_chat_question(self, question: str) -> dict:
        blob = question.lower()
        if any(h in blob for h in self._EV_HINTS):
            return {"is_allowed": True, "reason": "EV/충전 관련 키워드"}
        meta = ("mint", "뭐 할", "도움", "사용법", "기능", "게시판", "리포트")
        if any(h in blob for h in meta):
            return {"is_allowed": True, "reason": "MINT 메타 질문"}
        return {"is_allowed": False, "reason": "EV/충전 범위 밖 질문"}

    def answer_question_general(self, question: str) -> str:
        return (
            f"(Mock 일반 지식) '{question[:80]}'에 대한 EV·충전 분야 참고 답변입니다.\n\n"
            "※ MINT 수집 자료가 아닌 일반 참고 답변입니다."
        )

    def answer_question(self, question: str, context: str) -> str:
        if "수집된 게시글이 없습니다" in context:
            return "MINT에 수집된 자료에서 찾지 못했습니다. 먼저 소스를 크롤링해 주세요."
        titles = [line.split("제목: ", 1)[1] for line in context.splitlines() if line.startswith("- 제목: ")]
        refs = ", ".join(titles[:3]) if titles else "참고 자료"
        return (
            f"(Mock) '{question[:80]}'에 대한 답변입니다. "
            f"참고 자료 {len(titles)}건을 바탕으로 EV·충전 관련 동향을 확인해 보세요.\n\n"
            f"참고: {refs}"
        )


def get_llm_client() -> LLMClient:
    settings = get_settings()
    if settings.gemini_api_key:
        return GeminiClient()
    return MockLLMClient()
