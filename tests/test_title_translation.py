from unittest.mock import patch

from app.services.korean_output import text_needs_korean
from app.services.llm_client import MockLLMClient
from app.services.title_translation import localized_title_for_storage


def test_text_needs_korean_detects_english_title():
    assert text_needs_korean("Tesla expands Supercharger network in Europe")
    assert not text_needs_korean("현대차, 전기차 충전 인프라 확대 발표")


def test_localized_title_keeps_korean_title():
    title = "현대차, 국내 충전소 확대"
    assert localized_title_for_storage(title) == title


@patch("app.services.title_translation.get_llm_client")
def test_localized_title_translates_english(mock_get_client):
    mock_get_client.return_value = MockLLMClient()
    title = "New OCPP 2.1 security profile for EV chargers"
    result = localized_title_for_storage(title)
    assert result.endswith("(번역)")
    assert title in result


@patch("app.services.title_translation.get_settings")
def test_localized_title_respects_feature_flag(mock_settings):
    mock_settings.return_value.translate_titles_on_crawl = False
    title = "Tesla opens new charging hub"
    assert localized_title_for_storage(title) == title
