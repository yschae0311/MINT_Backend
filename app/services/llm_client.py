import json
import re
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import BadRequestError

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise BadRequestError(f"LLM returned invalid JSON: {text[:200]}") from exc


class LLMClient(ABC):
    @abstractmethod
    def summarize_post(self, title: str, content: str) -> dict:
        ...

    @abstractmethod
    def evaluate_discovery_candidate(self, title: str, content: str, url: str) -> dict:
        ...

    @abstractmethod
    def generate_daily_report(self, posts: list[dict]) -> dict:
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

    def _generate(self, model_name: str, system: str, user: str) -> str:
        model = self._genai.GenerativeModel(model_name, system_instruction=system)
        response = model.generate_content(user)
        return response.text or ""

    def summarize_post(self, title: str, content: str) -> dict:
        system = _load_prompt("post_summary_v1.md")
        user = f"Title: {title}\n\nContent:\n{content[:12000]}"
        return _parse_json(self._generate(self.summary_model, system, user))

    def evaluate_discovery_candidate(self, title: str, content: str, url: str) -> dict:
        system = _load_prompt("discovery_evaluate_v1.md")
        user = f"URL: {url}\nTitle: {title}\n\nContent:\n{content[:12000]}"
        return _parse_json(self._generate(self.summary_model, system, user))

    def generate_daily_report(self, posts: list[dict]) -> dict:
        system = _load_prompt("daily_report_v1.md")
        user = json.dumps({"posts": posts}, ensure_ascii=False)
        return _parse_json(self._generate(self.report_model, system, user))


class MockLLMClient(LLMClient):
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
    )

    def _looks_ev_related(self, title: str, content: str, url: str) -> bool:
        blob = f"{title} {content} {url}".lower()
        return any(h in blob for h in self._EV_HINTS)

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

    def generate_daily_report(self, posts: list[dict]) -> dict:
        from datetime import date

        return {
            "title": f"EV 충전 데일리 브리핑 - {date.today().isoformat()}",
            "summary": f"총 {len(posts)}건의 게시글을 반영한 브리핑입니다.",
            "key_changes": [
                {
                    "title": posts[0]["title"] if posts else "주요 이슈 없음",
                    "description": "Mock 리포트 항목",
                    "related_post_ids": [posts[0]["id"]] if posts else [],
                    "importance": "medium",
                }
            ],
            "risks": ["외부 API 미연결 시 Mock 데이터 사용"],
            "action_items": ["Gemini API 키 설정 후 재생성"],
        }


def get_llm_client() -> LLMClient:
    settings = get_settings()
    if settings.gemini_api_key:
        return GeminiClient()
    return MockLLMClient()
