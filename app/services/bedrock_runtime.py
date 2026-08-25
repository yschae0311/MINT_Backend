"""Shared Amazon Bedrock Runtime client factory."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from app.core.config import Settings, get_settings
from app.core.exceptions import BadRequestError


def _apply_bearer_token(api_key: str) -> None:
    key = (api_key or "").strip()
    if key:
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = key


def create_bedrock_runtime_client(
    settings: Settings | None = None,
    *,
    region: str | None = None,
):
    settings = settings or get_settings()
    resolved = (region or settings.aws_region or "").strip()
    if not resolved:
        raise BadRequestError("AWS_REGION is not configured for Bedrock")

    _apply_bearer_token(settings.bedrock_api_key)

    try:
        import boto3
    except ImportError as exc:
        raise BadRequestError("boto3 is required for Bedrock. Install with: pip install boto3") from exc

    kwargs: dict[str, Any] = {"service_name": "bedrock-runtime", "region_name": resolved}
    access_key = (settings.aws_access_key_id or "").strip()
    secret_key = (settings.aws_secret_access_key or "").strip()
    session_token = (settings.aws_session_token or "").strip()
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
        if session_token:
            kwargs["aws_session_token"] = session_token

    return boto3.client(**kwargs)


@lru_cache
def get_bedrock_runtime_client():
    return create_bedrock_runtime_client(get_settings())


def reset_bedrock_runtime_client() -> None:
    get_bedrock_runtime_client.cache_clear()


def illustration_provider_ready(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.report_illustration_enabled:
        return False
    provider = settings.llm_provider.lower().strip()
    if provider == "bedrock":
        return bool(settings.bedrock_image_model.strip() and settings.bedrock_text_ready)
    if provider == "gemini":
        return bool(settings.gemini_api_key.strip())
    return False
