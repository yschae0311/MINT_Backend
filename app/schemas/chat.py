from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ChatAskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    allow_general: bool = False


class ChatCitation(BaseModel):
    post_id: UUID
    title: str
    url: str | None = None
    summary: str | None = None


class ChatAskResponse(BaseModel):
    reply: str
    citations: list[ChatCitation] = []
    needs_general_confirm: bool = False
    source: Literal["mint", "general"] | None = None
