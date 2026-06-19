"""Detect and enforce Korean text in LLM user-facing fields."""

import re

_HANGUL_RE = re.compile(r"[\uAC00-\uD7A3]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{4,}")

KOREAN_RETRY_NOTE = (
    "\n\n[중요] summary, impact, action_items, relevance_reason 등 사용자에게 보이는 "
    "모든 문장을 반드시 한국어로 작성하세요. English output is not allowed."
)

KOREAN_USER_SUFFIX = "\n\n출력 언어: 한국어 (필수). 영어 문장 금지."


def text_needs_korean(text: str) -> bool:
    """True when non-empty text looks English-dominated and should be Korean."""
    text = (text or "").strip()
    if len(text) < 6:
        return False

    hangul = len(_HANGUL_RE.findall(text))
    if hangul >= 8:
        return False
    if hangul >= 3 and hangul / max(len(text), 1) >= 0.08:
        return False

    latin_words = _LATIN_WORD_RE.findall(text)
    if len(latin_words) >= 2 and hangul < 3:
        return True
    if len(text) >= 24 and hangul == 0 and latin_words:
        return True

    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return False
    latin_alpha = sum(1 for c in alpha if ord(c) < 128)
    if latin_alpha / len(alpha) > 0.82 and hangul < 2:
        return True
    return False


def collect_text_fields(
    result: dict,
    string_fields: tuple[str, ...],
    list_fields: tuple[str, ...] = (),
) -> list[str]:
    texts: list[str] = []
    for field in string_fields:
        value = result.get(field)
        if isinstance(value, str) and value.strip():
            texts.append(value)
    for field in list_fields:
        value = result.get(field)
        if isinstance(value, list):
            texts.extend(item for item in value if isinstance(item, str) and item.strip())
    return texts


def result_needs_korean_retry(
    result: dict,
    string_fields: tuple[str, ...],
    list_fields: tuple[str, ...] = (),
) -> bool:
    return any(text_needs_korean(text) for text in collect_text_fields(result, string_fields, list_fields))
