"""
EV·전기차 충전 관련성 필터.
- is_obvious_junk: 명백한 무관 페이지만 사전 차단
- passes_ai_evaluation: AI 판단을 우선, 과도한 규칙 재검증은 하지 않음
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 명백한 무관 페이지 (제목 기준)
_JUNK_TITLE = re.compile(
    r"^(홈|home|메인|main|welcome|index|더보기|more|login|sign\s*in)$",
    re.I,
)

_JUNK_TITLE_CONTAINS = re.compile(
    r"개인정보\s*처리\s*방침|이용\s*약관|개인정보\s*방침|"
    r"로그인|회원\s*가입|비밀번호\s*찾기|"
    r"사이트\s*맵|site\s*map",
    re.I,
)

# EV/충전 후보 신호 (넓게 — AI가 최종 판단)
_POSITIVE_HINTS = re.compile(
    r"전기차|전기\s*자동차|전기\s*버스|전기\s*택시|전기\s*트럭|"
    r"e-?mobility|electric\s*vehicle|"
    r"(?<![a-z])ev(?![a-z])|bev|phev|fcev|hev|"
    r"충전|charger|charging|charge\s*point|"
    r"ocpp|csms|emsp|cpo|로밍|roaming|"
    r"무공해|v2g|plug\s*and\s*charge|iso\s*15118|"
    r"배터리|에너지|전력|수소|친환경|탄소|신재생|"
    r"모빌리티|스마트\s*그리드|ess|"
    r"motrex",
    re.I,
)

_MIN_CONFIDENCE = 0.35


@dataclass(frozen=True)
class RelevanceResult:
    relevant: bool
    reason: str


def is_obvious_junk(title: str, content: str, url: str = "") -> bool:
    """명백한 사이트 안내·약관·로그인 등만 차단."""
    title = (title or "").strip()
    if len(title) < 2:
        return True
    if _JUNK_TITLE.match(title):
        return True
    if _JUNK_TITLE_CONTAINS.search(title):
        return True
    return False


def has_ev_hint(title: str, content: str, url: str = "") -> bool:
    """제목·본문에 EV/에너지 관련 단서가 있는지 (참고용)."""
    blob = f"{title}\n{(content or '')[:4000]}\n{url}"
    return bool(_POSITIVE_HINTS.search(blob))


def assess_ev_relevance(title: str, content: str, url: str = "") -> RelevanceResult:
    if is_obvious_junk(title, content, url):
        return RelevanceResult(False, "사이트 안내·약관 등 명백한 무관 페이지")
    if has_ev_hint(title, content, url):
        return RelevanceResult(True, "EV/에너지 관련 단서 확인")
    return RelevanceResult(False, "EV/충전 관련 단서 없음 (AI 판단에 위임 가능)")


def passes_keyword_gate(title: str, content: str, url: str = "") -> bool:
    """사전 게이트: 명백한 쓰레기만 막고 나머지는 AI에 넘김."""
    return not is_obvious_junk(title, content, url)


def ai_reject_reason(evaluation: dict, title: str, content: str, url: str = "") -> str | None:
    """AI/규칙 거부 시 스킵 사유 키 반환. 통과하면 None."""
    if is_obvious_junk(title, content, url):
        return "site_junk"
    if not evaluation.get("is_relevant"):
        return "ai_not_relevant"
    confidence = evaluation.get("confidence")
    if isinstance(confidence, (int, float)) and confidence < _MIN_CONFIDENCE:
        return "ai_low_confidence"
    return None


def passes_ai_evaluation(evaluation: dict, title: str, content: str, url: str = "") -> bool:
    return ai_reject_reason(evaluation, title, content, url) is None
