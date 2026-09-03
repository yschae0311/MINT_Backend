import json
import re
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import BadRequestError
from app.services.korean_output import KOREAN_RETRY_NOTE, KOREAN_USER_SUFFIX, result_needs_korean_retry, text_needs_korean

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
    def classify_post_content(
        self, title: str, content: str, *, keyword_catalog: str | None = None
    ) -> dict:
        ...

    @abstractmethod
    def translate_title(self, title: str) -> str:
        ...

    @abstractmethod
    def summarize_post(self, title: str, content: str) -> dict:
        ...

    @abstractmethod
    def evaluate_discovery_candidate(
        self, title: str, content: str, url: str, *, community: bool = False
    ) -> dict:
        ...

    @abstractmethod
    def generate_daily_report(
        self, posts: list[dict], report_date: date, *, edition: dict | None = None
    ) -> dict:
        ...

    @abstractmethod
    def generate_report_illustration_scene(
        self, summary: str, highlights: list[dict], report_date: date
    ) -> str:
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


class BedrockClient(LLMClient):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.bedrock_text_ready:
            raise BadRequestError(
                "Bedrock is not configured. Set AWS_REGION, BEDROCK_SUMMARY_MODEL, "
                "and BEDROCK_REPORT_MODEL (plus BEDROCK_API_KEY or AWS credentials)."
            )
        from app.services.bedrock_runtime import create_bedrock_runtime_client

        self._client = create_bedrock_runtime_client(settings)
        self.summary_model = settings.bedrock_summary_model.strip()
        self.report_model = settings.bedrock_report_model.strip()

    def _extract_text(self, response: dict) -> str:
        output = (response or {}).get("output") or {}
        message = output.get("message") or {}
        chunks: list[str] = []
        for part in message.get("content") or []:
            text = (part or {}).get("text")
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()

    def _generate(
        self,
        model_id: str,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int = 4096,
    ) -> str:
        from botocore.exceptions import BotoCoreError, ClientError

        user_text = user
        if json_mode:
            user_text = (
                f"{user.rstrip()}\n\n"
                "Respond with a single valid JSON object only. No markdown fences."
            )
        kwargs: dict = {
            "modelId": model_id,
            "messages": [{"role": "user", "content": [{"text": user_text}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.2},
        }
        if system.strip():
            kwargs["system"] = [{"text": system}]
        try:
            response = self._client.converse(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise BadRequestError(f"Bedrock request failed: {exc}") from exc
        text = self._extract_text(response)
        if not text:
            raise BadRequestError("Bedrock returned empty response")
        return text

    def _generate_json_korean(
        self,
        model_name: str,
        system: str,
        user: str,
        *,
        string_fields: tuple[str, ...],
        list_fields: tuple[str, ...] = (),
        max_tokens: int = 4096,
    ) -> dict:
        payload_user = f"{user.rstrip()}{KOREAN_USER_SUFFIX}"
        result = _parse_json(
            self._generate(model_name, system, payload_user, json_mode=True, max_tokens=max_tokens)
        )
        if not result_needs_korean_retry(result, string_fields, list_fields):
            return result
        retry_user = f"{payload_user}{KOREAN_RETRY_NOTE}"
        return _parse_json(
            self._generate(model_name, system, retry_user, json_mode=True, max_tokens=max_tokens)
        )

    def translate_title(self, title: str) -> str:
        system = _load_prompt("title_translate_v1.md")
        user = f"제목: {title[:500]}"
        result = _parse_json(self._generate(self.summary_model, system, user, json_mode=True))
        translated = (result.get("title") or "").strip()
        if not translated:
            raise BadRequestError("Bedrock returned empty title translation")
        return translated[:512]

    def classify_post_content(
        self, title: str, content: str, *, keyword_catalog: str | None = None
    ) -> dict:
        system = _load_prompt("post_classify_v1.md")
        user = f"제목: {title}\n\n본문:\n{content[:12000]}"
        if keyword_catalog:
            user = f"{user.rstrip()}\n\n{keyword_catalog.strip()}"
        return self._generate_json_korean(
            self.summary_model,
            system,
            user,
            string_fields=("category",),
            list_fields=(),
        )

    def summarize_post(self, title: str, content: str) -> dict:
        system = _load_prompt("post_summary_v1.md")
        user = f"제목: {title}\n\n본문:\n{content[:12000]}"
        return self._generate_json_korean(
            self.summary_model,
            system,
            user,
            string_fields=("summary", "impact"),
            list_fields=("action_items",),
        )

    def evaluate_discovery_candidate(
        self, title: str, content: str, url: str, *, community: bool = False
    ) -> dict:
        prompt_name = (
            "discovery_evaluate_community_v1.md" if community else "discovery_evaluate_v1.md"
        )
        system = _load_prompt(prompt_name)
        user = f"URL: {url}\n제목: {title}\n\n본문:\n{content[:12000]}"
        return self._generate_json_korean(
            self.summary_model,
            system,
            user,
            string_fields=("summary", "impact", "category", "relevance_reason"),
            list_fields=("action_items",),
        )

    def generate_daily_report(
        self, posts: list[dict], report_date: date, *, edition: dict | None = None
    ) -> dict:
        system = _load_prompt("daily_report_v1.md")
        payload: dict = {"report_date": report_date.isoformat(), "posts": posts}
        if edition:
            payload["edition"] = {
                "name": edition.get("name") or "",
                "slug": edition.get("slug") or "",
                "topics": edition.get("topics") or [],
            }
        user = json.dumps(payload, ensure_ascii=False)
        return self._generate_json_korean(
            self.report_model,
            system,
            user,
            string_fields=("summary",),
            list_fields=(),
            max_tokens=6144,
        )

    def generate_report_illustration_scene(
        self, summary: str, highlights: list[dict], report_date: date
    ) -> str:
        system = _load_prompt("report_illustration_v1.md")
        user = json.dumps(
            {
                "report_date": report_date.isoformat(),
                "summary": summary[:400],
                "highlights": [
                    {
                        "title": (h.get("title") or "")[:80],
                        "description": (h.get("description") or h.get("why_read") or "")[:120],
                    }
                    for h in highlights[:4]
                ],
            },
            ensure_ascii=False,
        )
        result = _parse_json(self._generate(self.report_model, system, user, json_mode=True))
        scene = (result.get("scene") or "").strip()
        if not scene:
            raise BadRequestError("Bedrock returned empty illustration scene")
        return scene[:500]

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

    def _generate_json_korean(
        self,
        model_name: str,
        system: str,
        user: str,
        *,
        string_fields: tuple[str, ...],
        list_fields: tuple[str, ...] = (),
    ) -> dict:
        payload_user = f"{user.rstrip()}{KOREAN_USER_SUFFIX}"
        result = _parse_json(self._generate(model_name, system, payload_user, json_mode=True))
        if not result_needs_korean_retry(result, string_fields, list_fields):
            return result
        retry_user = f"{payload_user}{KOREAN_RETRY_NOTE}"
        return _parse_json(self._generate(model_name, system, retry_user, json_mode=True))

    def translate_title(self, title: str) -> str:
        system = _load_prompt("title_translate_v1.md")
        user = f"제목: {title[:500]}"
        result = _parse_json(self._generate(self.summary_model, system, user, json_mode=True))
        translated = (result.get("title") or "").strip()
        if not translated:
            raise BadRequestError("Gemini returned empty title translation")
        return translated[:512]

    def classify_post_content(
        self, title: str, content: str, *, keyword_catalog: str | None = None
    ) -> dict:
        system = _load_prompt("post_classify_v1.md")
        user = f"제목: {title}\n\n본문:\n{content[:12000]}"
        if keyword_catalog:
            user = f"{user.rstrip()}\n\n{keyword_catalog.strip()}"
        return self._generate_json_korean(
            self.summary_model,
            system,
            user,
            string_fields=("category",),
            list_fields=(),
        )

    def summarize_post(self, title: str, content: str) -> dict:
        system = _load_prompt("post_summary_v1.md")
        user = f"제목: {title}\n\n본문:\n{content[:12000]}"
        return self._generate_json_korean(
            self.summary_model,
            system,
            user,
            string_fields=("summary", "impact"),
            list_fields=("action_items",),
        )

    def evaluate_discovery_candidate(
        self, title: str, content: str, url: str, *, community: bool = False
    ) -> dict:
        prompt_name = (
            "discovery_evaluate_community_v1.md" if community else "discovery_evaluate_v1.md"
        )
        system = _load_prompt(prompt_name)
        user = f"URL: {url}\n제목: {title}\n\n본문:\n{content[:12000]}"
        return self._generate_json_korean(
            self.summary_model,
            system,
            user,
            string_fields=("summary", "impact", "category", "relevance_reason"),
            list_fields=("action_items",),
        )

    def generate_daily_report(
        self, posts: list[dict], report_date: date, *, edition: dict | None = None
    ) -> dict:
        system = _load_prompt("daily_report_v1.md")
        payload: dict = {"report_date": report_date.isoformat(), "posts": posts}
        if edition:
            payload["edition"] = {
                "name": edition.get("name") or "",
                "slug": edition.get("slug") or "",
                "topics": edition.get("topics") or [],
            }
        user = json.dumps(payload, ensure_ascii=False)
        return self._generate_json_korean(
            self.report_model,
            system,
            user,
            string_fields=("summary",),
            list_fields=(),
        )

    def generate_report_illustration_scene(
        self, summary: str, highlights: list[dict], report_date: date
    ) -> str:
        system = _load_prompt("report_illustration_v1.md")
        user = json.dumps(
            {
                "report_date": report_date.isoformat(),
                "summary": summary[:400],
                "highlights": [
                    {
                        "title": (h.get("title") or "")[:80],
                        "description": (h.get("description") or h.get("why_read") or "")[:120],
                    }
                    for h in highlights[:4]
                ],
            },
            ensure_ascii=False,
        )
        result = _parse_json(self._generate(self.report_model, system, user, json_mode=True))
        scene = (result.get("scene") or "").strip()
        if not scene:
            raise BadRequestError("Gemini returned empty illustration scene")
        return scene[:500]

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
    def translate_title(self, title: str) -> str:
        text = (title or "").strip()
        if not text_needs_korean(text):
            return text
        return f"{text} (번역)"

    def classify_post_content(
        self, title: str, content: str, *, keyword_catalog: str | None = None
    ) -> dict:
        blob = f"{title} {content}".lower()
        category = "커뮤니티/현장" if "커뮤니티" in blob or "reddit" in blob else "충전 인프라"
        if any(h in blob for h in ("정책", "보조금", "규제", "regulation")):
            category = "정책/규제"
        elif any(h in blob for h in ("ocpp", "csms")):
            category = "CSMS/OCPP"
        keywords = self._mock_keywords(title, content)
        return {
            "category": category,
            "confidence": 0.78,
            "keywords": keywords,
        }

    def summarize_post(self, title: str, content: str) -> dict:
        return {
            "summary": f"{title[:120]}에 대한 요약입니다.",
            "impact": "EV 충전 인프라 및 CSMS 운영에 참고할 만한 변화가 있을 수 있습니다.",
            "action_items": ["원문 확인", "관련 정책 모니터링"],
            "importance": "medium",
            "confidence": 0.75,
            "category": "충전 인프라",
            "keywords": self._mock_keywords(title, content),
        }

    def _mock_keywords(self, title: str, content: str) -> list[dict]:
        blob = f"{title} {content}".lower()
        candidates = (
            ("OCPP", ("ocpp",)),
            ("CSMS", ("csms",)),
            ("충전 인프라", ("충전", "charger", "charging")),
            ("배터리", ("배터리", "battery")),
            ("전기차 정책", ("정책", "보조금", "regulation")),
            ("V2G", ("v2g",)),
            ("로보택시", ("로보택시", "robotaxi")),
            ("자율주행", ("자율주행", "autonomous", "self-driving")),
            ("ADAS", ("adas",)),
            ("웨이모", ("waymo", "웨이모")),
        )
        matched = [
            {"name": name, "confidence": 0.82}
            for name, hints in candidates
            if any(hint in blob for hint in hints)
        ]
        return matched[:5] or [{"name": "충전 인프라", "confidence": 0.65}]

    def evaluate_discovery_candidate(
        self, title: str, content: str, url: str, *, community: bool = False
    ) -> dict:
        from app.services.ev_relevance import passes_keyword_gate

        relevant = passes_keyword_gate(title, content, url)
        classified = self.classify_post_content(title, content)
        prefix = "커뮤니티 의견·미검증 — " if community else ""
        if not relevant:
            return {
                "is_relevant": False,
                "relevance_reason": "전기차·충전 또는 자율주행 직접 관련 신호 없음",
                "summary": "",
                "impact": "",
                "action_items": [],
                "importance": "low",
                "confidence": 0.35,
                "category": "",
                "keywords": [],
            }
        return {
            "is_relevant": True,
            "relevance_reason": "전기차·충전 또는 자율주행 직접 관련 신호 확인",
            "summary": f"{prefix}{title[:120]} — 수집된 {'커뮤니티' if community else '뉴스'} 후보입니다.",
            "impact": "조직 키워드·업무 관점에서 확인이 필요합니다.",
            "action_items": ["원문 링크 확인"],
            "importance": "low" if community else "medium",
            "confidence": classified.get("confidence", 0.7),
            "category": classified.get("category", "기타"),
            "keywords": classified.get("keywords", []),
        }

    def generate_daily_report(
        self, posts: list[dict], report_date: date, *, edition: dict | None = None
    ) -> dict:
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
        desk = ((edition or {}).get("name") or "").strip() or "전기차·충전"
        return {
            "summary": f"{report_date.isoformat()} 기준 {len(posts)}건 수집. {desk} 동향을 짧게 정리했습니다.",
            "recommendations": recommendations or [
                {
                    "title": "주요 이슈 없음",
                    "why_read": "당일 수집 게시글이 없습니다.",
                    "related_post_ids": [],
                    "importance": "low",
                }
            ],
        }

    def generate_report_illustration_scene(
        self, summary: str, highlights: list[dict], report_date: date
    ) -> str:
        topic = highlights[0]["title"] if highlights else summary[:80]
        return (
            f"Electric vehicle charging stations and power grid lines under a calm sky, "
            f"symbolizing industry news on {report_date.isoformat()}: {topic[:60]}"
        )

    def classify_chat_question(self, question: str) -> dict:
        blob = question.lower()
        if any(h in blob for h in self._EV_HINTS):
            return {"route": "ev", "reason": "EV/충전 관련 키워드"}
        meta = ("mint", "뭐 할", "도움", "사용법", "기능", "게시판", "리포트", "챗봇")
        if any(h in blob for h in meta):
            return {"route": "meta", "reason": "MINT 메타 질문"}
        off_topic = ("날씨", "레시피", "연예인", "로또", "운세", "연애 상담")
        if any(h in question for h in off_topic):
            return {"route": "off_topic", "reason": "MINT 범위 밖 주제"}
        return {"route": "general", "reason": "일반 질문"}

    def answer_question_general(self, question: str) -> str:
        return (
            f"(Mock 일반 지식) '{question[:80]}'에 대한 EV·충전 분야 참고 답변입니다.\n\n"
            "※ MINT 수집 자료가 아닌 일반 참고 답변입니다."
        )

    def answer_question(self, question: str, context: str) -> str:
        if "수집된 게시글이 없습니다" in context:
            return "MINT에 수집된 자료에서 찾지 못했습니다. 먼저 소스를 크롤링해 주세요."
        titles = [line.split("제목: ", 1)[1] for line in context.splitlines() if line.startswith("- 제목: ")]
        return (
            f"(Mock) '{question[:80]}'에 대한 답변입니다. "
            f"MINT 수집 자료 {len(titles)}건을 바탕으로 EV·충전 관련 동향을 정리했습니다."
        )


def get_llm_client() -> LLMClient:
    settings = get_settings()
    provider = settings.llm_provider.lower().strip()
    if provider == "bedrock" and settings.bedrock_text_ready:
        return BedrockClient()
    if provider == "gemini" and settings.gemini_api_key:
        return GeminiClient()
    return MockLLMClient()
