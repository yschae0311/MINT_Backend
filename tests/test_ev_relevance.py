"""Unit tests for EV relevance gate and display filter."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.ev_display_filter import is_ev_related_post
from app.services.ev_relevance import (
    ai_reject_reason,
    has_strong_ev_signal,
    passes_ai_evaluation,
    passes_keyword_gate,
)
from app.services.llm_client import MockLLMClient


def test_keyword_gate_accepts_ev_charging():
    assert passes_keyword_gate(
        "전기차 충전소 보조금 확대",
        "정부가 전기차 급속 충전 인프라 보조금을 확대한다.",
        "https://example.com/ev",
    )


def test_keyword_gate_rejects_off_topic_auto():
    assert not passes_keyword_gate(
        "디젤 SUV 신차 출시 시승기",
        "가솔린 엔진 성능과 연비를 비교했다.",
        "https://example.com/car",
    )


def test_keyword_gate_rejects_weak_energy_only():
    assert not passes_keyword_gate(
        "탄소중립 에너지 정책 발표",
        "신재생 에너지와 배터리 산업 육성 방안을 공개했다.",
        "https://example.com/energy",
    )


def test_ai_reject_when_not_relevant():
    reason = ai_reject_reason(
        {"is_relevant": False, "confidence": 0.9},
        "전기차 충전 요금 인하",
        "충전 요금이 내린다.",
    )
    assert reason == "ai_not_relevant"


def test_passes_ai_evaluation_requires_relevant_and_signal():
    evaluation = {"is_relevant": True, "confidence": 0.8}
    assert passes_ai_evaluation(
        evaluation,
        "OCPP CSMS 업데이트",
        "충전소 운영 시스템 OCPP 연동을 개선했다.",
    )
    assert not passes_ai_evaluation(
        {"is_relevant": True, "confidence": 0.9},
        "일반 주식 실적",
        "자동차 부품사 영업이익이 늘었다.",
    )


def test_mock_llm_discovery_sets_is_relevant():
    llm = MockLLMClient()
    ok = llm.evaluate_discovery_candidate(
        "전기차 충전기 설치 확대",
        "전국 급속 충전소가 늘어난다.",
        "https://example.com/charger",
    )
    assert ok["is_relevant"] is True
    bad = llm.evaluate_discovery_candidate(
        "아파트 분양 소식",
        "서울 부동산 분양 일정을 공개했다.",
        "https://example.com/apt",
    )
    assert bad["is_relevant"] is False


def test_display_filter_uses_title_signal():
    post = SimpleNamespace(
        title="전기차 충전 인프라 확충",
        category="충전 인프라",
        source=None,
        original_url=None,
        raw_content="",
    )
    assert is_ev_related_post(post, body="급속 충전기 설치")
    assert has_strong_ev_signal(post.title, "급속 충전기", "")


def test_display_filter_rejects_junk():
    post = SimpleNamespace(
        title="로그인",
        category="기타",
        source=None,
        original_url=None,
        raw_content="",
    )
    assert not is_ev_related_post(post)


def test_keyword_gate_accepts_autonomous():
    assert passes_keyword_gate(
        "웨이모 로보택시 확대",
        "자율주행 레벨4 운행 허가가 늘어난다.",
        "https://example.com/av",
    )


def test_keyword_gate_accepts_autonomous_vehicle_english():
    assert passes_keyword_gate(
        "Waymo robotaxi expansion",
        "The company will grow its autonomous vehicle fleet in two cities.",
        "https://www.autonews.com/mobility/waymo",
    )


def test_mock_llm_discovery_accepts_autonomous():
    llm = MockLLMClient()
    ok = llm.evaluate_discovery_candidate(
        "강남 로보택시 운행 구역 확대",
        "자율주행 레벨4 시범 운행이 강남으로 늘어난다.",
        "https://example.com/robotaxi",
    )
    assert ok["is_relevant"] is True
