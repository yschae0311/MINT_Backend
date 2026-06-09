"""
EV·전기차 충전 관련성 사전/사후 필터.
AI 발견 크롤링에서 무관한 기사 유입을 줄이기 위한 규칙 기반 검사.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 제목·본문에 하나라도 있으면 EV/충전 후보로 본다 (대소문자 무시)
_POSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"전기차|전기\s*자동차|e-?mobility|electric\s*vehicle",
        r"\bev\b|evs\b|bev\b|phev\b|hev\b",
        r"충전\s*(기|소|인프라|요금|서비스|망|사업|시장|표준|규제|정책)?",
        r"급속\s*충전|완속\s*충전|초급속|ultra\s*fast\s*charg",
        r"ocpp|csms|emsp|charge\s*point|charging\s*station|charging\s*infrastructure",
        r"\bcpo\b|로밍|roaming|plug\s*and\s*charge|iso\s*15118",
        r"무공해\s*차|친환경\s*차|zero\s*emission",
        r"v2g|vehicle\s*to\s*grid|양방향\s*충전",
        r"전력\s*망|전력\s*수요|전력\s*요금",  # 충전 맥락 정책
        r"배터리\s*(스왑|교환|팩|관리|충전)",  # 배터리 단독은 제외 패턴으로 보완
        r"수소\s*전기|fuel\s*cell\s*vehicle|fcev",
        r"모빌리티\s*플랫폼.*충전|충전.*플랫폼",
        r"motrex|motrexev",
    )
)

# 단독으로 있으면 오탐이 많은 키워드 — 강한 긍정 신호가 함께 있어야 인정
_WEAK_POSITIVE = re.compile(
    r"배터리|에너지|전력|친환경|그린|탄소|신재생|재생에너지|수소",
    re.I,
)

# 있으면 관련성 점수를 깎거나 즉시 제외
_NEGATIVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"개인정보\s*처리|이용\s*약관|개인정보\s*방침|copyright|저작권",
        r"로그인|회원\s*가입|비밀번호\s*찾기|아이디\s*찾기",
        r"사이트\s*맵|site\s*map|메뉴\s*안내|홈페이지\s*안내",
        r"채용\s*공고|인재\s*채용|입사\s*지원|모집\s*안내",
        r"설문\s*조사|이벤트\s*당첨|경품|할인\s*쿠폰",
        r"부동산|아파트\s*분양|분양\s*안내|청약",
        r"주식\s*시세|코인|비트코인|암호화폐|로또",
        r"연예|드라마|예능|k-?pop|아이돌",
        r"레시피|맛집|여행\s*코스|관광\s*안내",
        r"디젤\s*차|가솔린\s*차|lpg\s*차|경유\s*차",
        r"신차\s*출시|suv\s*출시|세단\s*출시",  # 일반 내연기관 신차
        r"공지\s*사항\s*안내|행정\s*서비스\s*안내",
    )
)

# 제목이 이 패턴이면 본문 검사 없이 제외
_TITLE_SKIP = re.compile(
    r"^(홈|home|메인|main|공지|notice|안내|welcome|index|더보기|more)$",
    re.I,
)

_MIN_STRONG_SIGNALS = 1  # 강한 긍정 키워드 최소 개수
_MIN_WEAK_WITH_STRONG = 1  # 약한 키워드만 있을 때 필요한 강한 신호 수
_MIN_CONFIDENCE = 0.55


@dataclass(frozen=True)
class RelevanceResult:
    relevant: bool
    reason: str
    score: int
    strong_hits: int
    weak_hits: int
    negative_hits: int


def _count_matches(patterns: tuple[re.Pattern[str], ...], text: str) -> int:
    return sum(1 for p in patterns if p.search(text))


def assess_ev_relevance(title: str, content: str, url: str = "") -> RelevanceResult:
    title = (title or "").strip()
    content = (content or "").strip()
    blob = f"{title}\n{content[:3000]}\n{url}"

    if len(title) < 3 or _TITLE_SKIP.match(title):
        return RelevanceResult(False, "제목이 너무 짧거나 사이트 안내 페이지입니다.", 0, 0, 0, 0)

    strong_hits = _count_matches(_POSITIVE_PATTERNS, blob)
    weak_hits = len(_WEAK_POSITIVE.findall(blob))
    negative_hits = _count_matches(_NEGATIVE_PATTERNS, blob)

    title_strong = _count_matches(_POSITIVE_PATTERNS, title)
    title_negative = _count_matches(_NEGATIVE_PATTERNS, title)

    score = strong_hits * 3 + weak_hits - negative_hits * 4

    if title_negative > 0 and title_strong == 0:
        return RelevanceResult(
            False,
            "제목에 EV/충전 무관 키워드가 포함되어 있습니다.",
            score,
            strong_hits,
            weak_hits,
            negative_hits,
        )

    if strong_hits >= _MIN_STRONG_SIGNALS:
        if negative_hits > strong_hits:
            return RelevanceResult(
                False,
                "EV/충전 신호보다 무관한 주제 신호가 더 강합니다.",
                score,
                strong_hits,
                weak_hits,
                negative_hits,
            )
        return RelevanceResult(
            True,
            f"EV/충전 관련 키워드 {strong_hits}건 확인",
            score,
            strong_hits,
            weak_hits,
            negative_hits,
        )

    if weak_hits > 0 and strong_hits >= _MIN_WEAK_WITH_STRONG:
        return RelevanceResult(
            True,
            "에너지·배터리 맥락과 EV/충전 키워드가 함께 확인됨",
            score,
            strong_hits,
            weak_hits,
            negative_hits,
        )

    return RelevanceResult(
        False,
        "EV·전기차·충전 관련 키워드가 충분하지 않습니다.",
        score,
        strong_hits,
        weak_hits,
        negative_hits,
    )


def passes_keyword_gate(title: str, content: str, url: str = "") -> bool:
    return assess_ev_relevance(title, content, url).relevant


def passes_ai_evaluation(evaluation: dict, title: str, content: str, url: str = "") -> bool:
    if not evaluation.get("is_relevant"):
        return False

    confidence = evaluation.get("confidence")
    if isinstance(confidence, (int, float)) and confidence < _MIN_CONFIDENCE:
        return False

    # LLM이 relevant=true여도 규칙 기반 재검증 (환각·오판 방지)
    rule = assess_ev_relevance(title, content, url)
    if not rule.relevant:
        return False

    return True
