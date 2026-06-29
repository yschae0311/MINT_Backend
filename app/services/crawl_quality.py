"""Detect non-article pages during crawl (login, terms, etc.). Not topic filtering."""

from __future__ import annotations

import re

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


def is_obvious_junk(title: str, content: str, url: str = "") -> bool:
    title = (title or "").strip()
    if len(title) < 2:
        return True
    if _JUNK_TITLE.match(title):
        return True
    if _JUNK_TITLE_CONTAINS.search(title):
        return True
    return False
