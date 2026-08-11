from pydantic import BaseModel, Field


class NarrateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=12000)
