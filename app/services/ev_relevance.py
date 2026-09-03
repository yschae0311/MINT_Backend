"""
EV·전기차 충전 관련성 필터.

강한 신호(전기차·충전·OCPP 등)가 있어야 통과.
배터리·친환경·탄소만 있는 일반 에너지/자동차 뉴스는 제외.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 명백한 무관 페이지
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

# EV·충전 직접 관련 (하나 이상 필수)
_STRONG_EV = re.compile(
    r"전기차|전기\s*자동차|전기\s*버스|전기\s*택시|전기\s*트럭|전기\s*이륜|"
    r"e-?mobility|electric\s*vehicle|electric\s*car|"
    r"(?<![a-z])ev(?![a-z])|bev|phev|fcev|"
    r"충전\s*(기|소|인프라|요금|서비스|망|사업|시장|표준|규제|정책)?|"
    r"급속\s*충전|완속\s*충전|초급속|"
    r"charger|charging|charge\s*point|charging\s*station|"
    r"ocpp|csms|emsp|(?<![a-z])cpo(?![a-z])|충전\s*로밍|roaming|"
    r"무공해\s*차|무공해차|"
    r"v2g|plug\s*and\s*charge|iso\s*15118|"
    r"motrex",
    re.I,
)

# 자율주행·ADAS (사업 분야 확장용 기본 신호)
_STRONG_AV = re.compile(
    r"자율\s*주행|autonomous\s*(?:driving|vehicle|car|truck)|self-driving|"
    r"로보\s*택시|robotaxi|robot\s*taxi|"
    r"(?<![a-z])adas(?![a-z])|"
    r"라이다|lidar|"
    r"레벨\s*[3-5]|level\s*[3-5]|"
    r"운행\s*허가|무인\s*(?:셔틀|택시|버스)|"
    r"(?<![a-z])waymo(?![a-z])|웨이모",
    re.I,
)

# 단독으로는 부족 — 강한 신호와 함께 있을 때만 보조
_WEAK_TOPIC = re.compile(
    r"배터리|에너지|전력|친환경|탄소|신재생|수소|모빌리티|ess|스마트\s*그리드",
    re.I,
)

# 제목에 있으면 강한 EV 신호 없이 제외
_TITLE_OFF_TOPIC = re.compile(
    r"디젤|가솔린|경유|lpg|"
    r"suv|세단|쿠페|왜건|"
    r"신차\s*출시|시승|모터쇼|오토쇼|"
    r"주식|코스피|실적|영업이익|매출|주가|"
    r"채용|모집|이벤트|당첨|"
    r"부동산|아파트|분양",
    re.I,
)

_MIN_CONFIDENCE = 0.5


@dataclass(frozen=True)
class RelevanceResult:
    relevant: bool
    reason: str


def _blob(title: str, content: str, url: str = "", content_limit: int = 3500) -> str:
    return f"{title}\n{(content or '')[:content_limit]}\n{url}"


def is_obvious_junk(title: str, content: str, url: str = "") -> bool:
    title = (title or "").strip()
    if len(title) < 2:
        return True
    if _JUNK_TITLE.match(title):
        return True
    if _JUNK_TITLE_CONTAINS.search(title):
        return True
    return False


def has_strong_ev_signal(title: str, content: str, url: str = "") -> bool:
    return bool(_STRONG_EV.search(_blob(title, content, url)))


def has_strong_av_signal(title: str, content: str, url: str = "") -> bool:
    return bool(_STRONG_AV.search(_blob(title, content, url)))


def matches_topic_terms(title: str, content: str, url: str = "", terms: list[str] | None = None) -> bool:
    if not terms:
        return False
    blob = _blob(title, content, url).lower()
    for term in terms:
        needle = (term or "").strip().lower()
        if len(needle) >= 2 and needle in blob:
            return True
    return False


def has_strong_topic_signal(
    title: str, content: str, url: str = "", extra_terms: list[str] | None = None
) -> bool:
    if has_strong_ev_signal(title, content, url) or has_strong_av_signal(title, content, url):
        return True
    return matches_topic_terms(title, content, url, extra_terms)


def is_weak_topic_only(title: str, content: str, url: str = "", extra_terms: list[str] | None = None) -> bool:
    """배터리·친환경 등만 있고 EV/충전/자율주행 직접 신호가 없음."""
    text = _blob(title, content, url)
    if has_strong_topic_signal(title, content, url, extra_terms):
        return False
    return bool(_WEAK_TOPIC.search(text))


def passes_keyword_gate(
    title: str, content: str, url: str = "", extra_terms: list[str] | None = None
) -> bool:
    """
    AI 호출 전 사전 게이트:
    - 명백한 쓰레기 제외
    - EV/충전 또는 자율주행(및 활성 판 키워드) 강한 신호 필수
    - 제목이 일반 자동차/주식 등이면서 주제 신호 없으면 제외
    """
    if is_obvious_junk(title, content, url):
        return False

    title_s = (title or "").strip()
    title_has_strong = has_strong_topic_signal(title_s, "", "", extra_terms)
    if _TITLE_OFF_TOPIC.search(title_s) and not title_has_strong:
        return False

    if has_strong_topic_signal(title, content, url, extra_terms):
        return True

    if is_weak_topic_only(title, content, url, extra_terms):
        return False

    return False


def ai_reject_reason(
    evaluation: dict, title: str, content: str, url: str = "", extra_terms: list[str] | None = None
) -> str | None:
    if is_obvious_junk(title, content, url):
        return "site_junk"
    if not evaluation.get("is_relevant"):
        return "ai_not_relevant"

    confidence = evaluation.get("confidence")
    if isinstance(confidence, (int, float)) and confidence < _MIN_CONFIDENCE:
        return "ai_low_confidence"

    if not has_strong_topic_signal(title, content, url, extra_terms):
        if is_weak_topic_only(title, content, url, extra_terms):
            return "weak_topic_only"
        return "ai_topic_mismatch"

    title_s = (title or "").strip()
    if _TITLE_OFF_TOPIC.search(title_s) and not has_strong_topic_signal(title_s, "", "", extra_terms):
        return "ai_topic_mismatch"

    return None


def passes_ai_evaluation(
    evaluation: dict, title: str, content: str, url: str = "", extra_terms: list[str] | None = None
) -> bool:
    return ai_reject_reason(evaluation, title, content, url, extra_terms) is None


# 하위 호환
def has_ev_hint(title: str, content: str, url: str = "") -> bool:
    return has_strong_ev_signal(title, content, url)


def assess_ev_relevance(title: str, content: str, url: str = "") -> RelevanceResult:
    if is_obvious_junk(title, content, url):
        return RelevanceResult(False, "사이트 안내·약관 등 명백한 무관 페이지")
    if has_strong_ev_signal(title, content, url):
        return RelevanceResult(True, "EV/충전 직접 관련 신호 확인")
    if has_strong_av_signal(title, content, url):
        return RelevanceResult(True, "자율주행 직접 관련 신호 확인")
    if is_weak_topic_only(title, content, url):
        return RelevanceResult(False, "일반 에너지·친환경 키워드만 있음")
    return RelevanceResult(False, "EV/충전 직접 관련 신호 없음")
