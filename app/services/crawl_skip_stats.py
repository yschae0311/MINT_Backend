from __future__ import annotations

from collections import Counter

SKIP_LABELS: dict[str, str] = {
    "no_url": "URL 없음",
    "fetch_failed": "본문 수집 실패",
    "content_short": "본문 너무 짧음",
    "title_invalid": "제목 형식 부적합",
    "site_junk": "사이트 안내·약관 페이지",
    "ai_not_relevant": "AI 관련 없음",
    "ai_low_confidence": "AI 신뢰도 낮음",
    "ai_eval_failed": "AI 판정 실패",
    "duplicate": "이미 등록됨",
    "source_error": "소스 크롤 오류",
    "other": "기타",
}


class CrawlSkipStats:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def add(self, reason: str, n: int = 1) -> None:
        self.counts[reason] += n

    def merge(self, other: CrawlSkipStats) -> None:
        self.counts.update(other.counts)

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
        return "\n".join(lines)
