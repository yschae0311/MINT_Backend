"""Per-candidate progress reporting for discovery crawl jobs."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from app.models.enums import SourceType
from app.models.source import Source
from app.services.community_sources import COMMUNITY_SOURCE_TYPES
from app.services.job_service import JobService

_FLUSH_INTERVAL_SEC = 0.4

CandidateOutcome = Literal["skipped", "passed", "review"]


def estimate_discovery_candidates_per_source(source: Source) -> int:
    """Upper bound of article candidates processed per source in discovery mode."""
    if source.source_type in COMMUNITY_SOURCE_TYPES or source.source_type == SourceType.reddit:
        return 10
    return 15


@dataclass
class CrawlProgressTracker:
    jobs: JobService
    job_id: UUID
    source_total: int
    estimated_candidate_total: int
    processed: int = 0
    passed: int = 0
    review: int = 0
    skipped: int = 0
    current_source_index: int = 0
    current_source_name: str = ""
    current_candidate_title: str = ""
    _last_flush_at: float = field(default=0.0, repr=False)

    def begin(self, *, source_name: str | None = None) -> None:
        if source_name:
            self.current_source_name = source_name
        self._flush(force=True)

    def on_source_start(self, index: int, source_name: str) -> None:
        self.current_source_index = index
        self.current_source_name = source_name
        self.current_candidate_title = ""
        self._flush(force=True)

    def on_candidate_start(self, title: str) -> None:
        self.current_candidate_title = (title or "").strip()[:80]
        self._flush(force=True)

    def on_candidate_done(self, *, outcome: CandidateOutcome) -> None:
        self.processed += 1
        self.current_candidate_title = ""
        if outcome == "passed":
            self.passed += 1
        elif outcome == "review":
            self.review += 1
        else:
            self.skipped += 1
        self._flush(force=True)

    def finish(self) -> None:
        total = max(self.estimated_candidate_total, self.processed, 1)
        self.jobs.update_progress(
            self.job_id,
            self.processed,
            total,
            self._message(done=True),
        )

    def _message(self, *, done: bool = False) -> str:
        total = max(self.estimated_candidate_total, self.processed, 1)
        parts = [f"{self.processed} / {total}건"]
        parts.append(f"등록 {self.passed}")
        parts.append(f"검수 {self.review}")
        parts.append(f"스킵 {self.skipped}")
        if self.current_candidate_title and not done:
            parts.append(f"분석 중: {self.current_candidate_title}")
        elif self.source_total > 1 and self.current_source_name and not done:
            parts.append(f"소스 {self.current_source_index}/{self.source_total} · {self.current_source_name}")
        elif self.current_source_name and self.source_total == 1 and not done:
            parts.append(self.current_source_name)
        if done:
            parts.append("완료")
        return " · ".join(parts)

    def _flush(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_flush_at) < _FLUSH_INTERVAL_SEC:
            return
        self._last_flush_at = now
        total = max(self.estimated_candidate_total, self.processed, 1)
        self.jobs.update_progress(
            self.job_id,
            self.processed,
            total,
            self._message(),
        )
