"""Translate crawled news titles to Korean when appropriate."""

import logging

from app.core.config import get_settings
from app.services.korean_output import text_needs_korean
from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)


def localized_title_for_storage(title: str) -> str:
    """Return the title to persist on Post — Korean when translation succeeds."""
    original = (title or "").strip()[:512]
    if not original or len(original) < 2:
        return original
    if not get_settings().translate_titles_on_crawl:
        return original
    if not text_needs_korean(original):
        return original

    try:
        translated = get_llm_client().translate_title(original)
        translated = (translated or "").strip()[:512]
        if len(translated) >= 2:
            return translated
    except Exception as exc:
        logger.warning("Title translation failed, keeping original: %s", exc)

    return original
