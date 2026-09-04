import base64
import json
import logging
import random
import time
from pathlib import Path
from uuid import UUID

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

_DEAD_IMAGE_MODELS = {
    "gemini-2.0-flash-preview-image-generation",
    "gemini-2.0-flash-exp-image-generation",
    "imagen-3.0-generate-002",
}

_IMAGE_MODEL_FALLBACKS = (
    "gemini-2.5-flash-image",
    "gemini-2.5-flash-image-preview",
    "gemini-3.1-flash-image-preview",
)

NEWSPAPER_SKETCH_PREFIX = (
    "Black and white newspaper editorial sketch illustration, pen and ink cross-hatching, "
    "newsprint texture, high contrast grayscale only, no color, no text, no logos, "
    "no readable signs, no photorealism, no human faces, metaphorical scene: "
)

_SIMPLE_SCENE = (
    "a quiet electric vehicle charging plaza at dawn, cables and soft geometric shapes, "
    "editorial metaphor, empty of people"
)


def _image_ext(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\xff\xd8"):
        return "jpg"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "gif"
    if image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16]:
        return "webp"
    return "png"


class ReportIllustrationService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.media_root = Path(self.settings.media_root)
        self.reports_dir = self.media_root / "reports"

    def _gemini_model_candidates(self) -> list[str]:
        primary = (self.settings.gemini_image_model or "").strip()
        ordered: list[str] = []
        for name in (*_IMAGE_MODEL_FALLBACKS, primary):
            if not name or name in _DEAD_IMAGE_MODELS or name in ordered:
                continue
            ordered.append(name)
        if primary and primary not in _DEAD_IMAGE_MODELS:
            ordered = [primary, *[m for m in ordered if m != primary]]
        return ordered

    def generate_image_bytes(self, scene: str) -> bytes | None:
        if not self.settings.report_illustration_enabled:
            return None
        provider = self.settings.llm_provider.lower().strip()
        if provider == "bedrock":
            return self._generate_bedrock_image(scene)
        if provider == "gemini":
            return self._generate_gemini_image(scene)
        return None

    def _bedrock_image_payloads(self, prompt: str) -> list[dict]:
        model_id = (self.settings.bedrock_image_model or "").lower()
        if "stability" in model_id or "stable-image" in model_id:
            return [
                {
                    "prompt": prompt[:10000],
                    "aspect_ratio": "1:1",
                    "output_format": "png",
                    "seed": random.randint(0, 4_294_967_295),
                },
                {"prompt": prompt[:10000], "output_format": "png"},
            ]
        # Amazon Nova Canvas / Titan-style TEXT_IMAGE
        return [
            {
                "taskType": "TEXT_IMAGE",
                "textToImageParams": {"text": prompt[:1024]},
                "imageGenerationConfig": {
                    "seed": random.randint(0, 858_993_459),
                    "quality": "standard",
                    "height": 768,
                    "width": 1024,
                    "numberOfImages": 1,
                },
            }
        ]

    def _generate_bedrock_image(self, scene: str) -> bytes | None:
        model_id = (self.settings.bedrock_image_model or "").strip()
        if not model_id:
            return None

        from botocore.exceptions import BotoCoreError, ClientError

        from app.services.bedrock_runtime import create_bedrock_runtime_client

        image_region = (
            (self.settings.bedrock_image_region or "").strip()
            or (self.settings.aws_region or "").strip()
        )
        scenes = [scene.strip(), _SIMPLE_SCENE]
        try:
            client = create_bedrock_runtime_client(self.settings, region=image_region)
        except Exception as exc:
            logger.warning("Bedrock illustration client failed: %s", exc)
            return None

        for attempt_scene in scenes:
            if not attempt_scene:
                continue
            prompt = f"{NEWSPAPER_SKETCH_PREFIX}{attempt_scene}"
            for payload in self._bedrock_image_payloads(prompt):
                try:
                    response = client.invoke_model(
                        modelId=model_id,
                        body=json.dumps(payload),
                        contentType="application/json",
                        accept="application/json",
                    )
                    body = response.get("body")
                    raw = body.read() if hasattr(body, "read") else body
                    data = json.loads(raw)
                    images = data.get("images") or []
                    if not images:
                        logger.warning(
                            "Bedrock illustration returned no images (model=%s region=%s)",
                            model_id,
                            image_region,
                        )
                        continue
                    return base64.b64decode(images[0])
                except (ClientError, BotoCoreError, ValueError, TypeError) as exc:
                    logger.warning(
                        "Bedrock illustration failed (model=%s region=%s): %s",
                        model_id,
                        image_region,
                        exc,
                    )
                    time.sleep(0.3)

        return None

    def _generate_gemini_image(self, scene: str) -> bytes | None:
        api_key = self.settings.gemini_api_key.strip()
        if not api_key:
            return None

        scenes = [scene.strip(), _SIMPLE_SCENE]
        payloads = [
            {
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {"aspectRatio": "4:3"},
                }
            },
            {
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                }
            },
            {
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                }
            },
        ]

        for attempt_scene in scenes:
            if not attempt_scene:
                continue
            prompt = f"{NEWSPAPER_SKETCH_PREFIX}{attempt_scene}"
            for model in self._gemini_model_candidates():
                for cfg in payloads:
                    payload = {
                        "contents": [{"parts": [{"text": prompt}]}],
                        **cfg,
                    }
                    image = self._request_gemini_image(api_key, model, payload)
                    if image:
                        return image
                time.sleep(0.4)

        logger.warning("Report illustration exhausted all Gemini model/payload fallbacks")
        return None

    def _request_gemini_image(self, api_key: str, model: str, payload: dict) -> bytes | None:
        url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(url, params={"key": api_key}, json=payload)
                if response.status_code in {404, 400}:
                    logger.warning(
                        "Report illustration rejected (model=%s status=%s): %s",
                        model,
                        response.status_code,
                        (response.text or "")[:240],
                    )
                    return None
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("Report illustration API failed (model=%s): %s", model, exc)
            return None

        for candidate in data.get("candidates") or []:
            for part in candidate.get("content", {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if not inline:
                    continue
                raw = inline.get("data")
                if not raw:
                    continue
                try:
                    return base64.b64decode(raw)
                except Exception as exc:
                    logger.warning("Report illustration decode failed: %s", exc)
                    return None
        return None

    def front_cache_path(self, organization_id: UUID, cache_key: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in cache_key)[:80]
        return self.media_root / "front" / str(organization_id) / f"{safe}.png"

    def read_front_cache(self, organization_id: UUID, cache_key: str) -> str | None:
        path = self.front_cache_path(organization_id, cache_key)
        if path.is_file() and path.stat().st_size > 100:
            return (
                f"{self.settings.media_url_prefix.rstrip('/')}"
                f"/front/{organization_id}/{path.name}"
            )
        return None

    def save_front_cache(self, organization_id: UUID, cache_key: str, image_bytes: bytes) -> str:
        path = self.front_cache_path(organization_id, cache_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_bytes)
        url = (
            f"{self.settings.media_url_prefix.rstrip('/')}"
            f"/front/{organization_id}/{path.name}"
        )
        logger.info(
            "Saved front illustration path=%s bytes=%s url=%s",
            path.resolve(),
            len(image_bytes),
            url,
        )
        return url

    def save_for_report(self, report_id: UUID, image_bytes: bytes) -> str:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        path = self.reports_dir / f"{report_id}.png"
        path.write_bytes(image_bytes)
        url = f"{self.settings.media_url_prefix.rstrip('/')}/reports/{report_id}.png"
        logger.info(
            "Saved report illustration path=%s bytes=%s url=%s",
            path.resolve(),
            len(image_bytes),
            url,
        )
        return url

    def save_for_post(self, post_id: UUID, image_bytes: bytes) -> str:
        ext = _image_ext(image_bytes)
        folder = self.media_root / "posts"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{post_id}.{ext}"
        path.write_bytes(image_bytes)
        url = f"{self.settings.media_url_prefix.rstrip('/')}/posts/{post_id}.{ext}"
        logger.info(
            "Saved story photo path=%s bytes=%s url=%s",
            path.resolve(),
            len(image_bytes),
            url,
        )
        return url

    def latest_front_cache(self, organization_id: UUID) -> str | None:
        folder = self.media_root / "front" / str(organization_id)
        if not folder.is_dir():
            return None
        files = sorted(
            (p for p in folder.glob("*.png") if p.is_file() and p.stat().st_size > 100),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return None
        return (
            f"{self.settings.media_url_prefix.rstrip('/')}"
            f"/front/{organization_id}/{files[0].name}"
        )
