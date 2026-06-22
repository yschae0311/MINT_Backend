from __future__ import annotations

from collections import Counter

SKIP_LABELS: dict[str, str] = {
    "no_url": "URL 없음",
    "fetch_failed": "본문 수집 실패",
    "reddit_blocked": "Reddit 차단 — OAuth/RSS 설정 필요",
    "content_short": "본문 너무 짧음",
    "title_invalid": "제목 형식 부적합",
    "site_junk": "사이트 안내·약관 페이지",
    "ai_not_relevant": "AI 관련 없음",
    "weak_topic_only": "일반 에너지·친환경만 언급",
    "ai_topic_mismatch": "EV/충전 직접 신호 없음",
    "ai_low_confidence": "AI 신뢰도 낮음",
    "ai_eval_failed": "AI API/응답 오류",
    "ai_json_error": "AI 응답 JSON 파싱 실패",
    "ai_rate_limit": "AI API 호출 한도 초과",
    "ai_billing_depleted": "Gemini 선불 크레딧 소진",
    "ai_api_auth": "AI API 키/권한 오류",
    "duplicate": "이미 등록됨",
    "source_error": "소스 크롤 오류",
    "other": "기타",
}


def classify_eval_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "prepayment credits" in msg or "billing" in msg and "deplet" in msg:
        return "ai_billing_depleted"
    if "invalid json" in msg or "jsondecode" in msg or "json" in msg and "parse" in msg:
        return "ai_json_error"
    if "429" in msg or "quota" in msg or "rate" in msg or "resource exhausted" in msg:
        return "ai_rate_limit"
    if "api key" in msg or "403" in msg or "401" in msg or "permission" in msg:
        return "ai_api_auth"
    return "ai_eval_failed"


class CrawlSkipStats:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.error_sample: str | None = None
        self.billing_depleted: bool = False

    def add(self, reason: str, n: int = 1, *, sample: str | None = None) -> None:
        self.counts[reason] += n
        if reason == "ai_billing_depleted":
            self.billing_depleted = True
        if sample and not self.error_sample and (
            reason.startswith("ai_") or reason in ("reddit_blocked", "fetch_failed")
        ):
            self.error_sample = sample[:320]

    def merge(self, other: CrawlSkipStats) -> None:
        self.counts.update(other.counts)
        if other.error_sample and not self.error_sample:
            self.error_sample = other.error_sample
        if other.billing_depleted:
            self.billing_depleted = True

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def to_dict(self) -> dict[str, int]:
        return dict(self.counts)

    def format_summary(self, created: int, *, failed_sources: int = 0) -> str:
        lines = [f"등록 {created}건, 스킵 {self.total}건"]
        for key, count in sorted(self.counts.items(), key=lambda x: (-x[1], x[0])):
            label = SKIP_LABELS.get(key, key)
            lines.append(f"· {label}: {count}건")
        if failed_sources:
            lines.append(f"· {SKIP_LABELS['source_error']}: {failed_sources}개 소스")
        if self.billing_depleted:
            lines.append(
                "※ Gemini 선불 크레딧이 소진되었습니다. "
                "AI Studio에서 결제/크레딧 충전 후 다시 시도하세요."
            )
        elif self.error_sample:
            lines.append(f"※ 오류 예시: {self.error_sample}")
        return "\n".join(lines)
