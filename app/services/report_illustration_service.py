import base64
import logging
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

# Prefer known-good ids first; env primary is inserted unless dead.
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


class ReportIllustrationService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.media_root = Path(self.settings.media_root)
        self.reports_dir = self.media_root / "reports"

    def _model_candidates(self) -> list[str]:
        primary = (self.settings.gemini_image_model or "").strip()
        ordered: list[str] = []
        for name in (*_IMAGE_MODEL_FALLBACKS, primary):
            if not name or name in _DEAD_IMAGE_MODELS or name in ordered:
                continue
            ordered.append(name)
        # Keep configured primary first when it is still valid.
        if primary and primary not in _DEAD_IMAGE_MODELS:
            ordered = [primary, *[m for m in ordered if m != primary]]
        return ordered

    def generate_image_bytes(self, scene: str) -> bytes | None:
        api_key = self.settings.gemini_api_key.strip()
        if not api_key or not self.settings.report_illustration_enabled:
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
            for model in self._model_candidates():
                for cfg in payloads:
                    payload = {
                        "contents": [{"parts": [{"text": prompt}]}],
                        **cfg,
                    }
                    image = self._request_image(api_key, model, payload)
                    if image:
                        return image
                time.sleep(0.4)

        logger.warning("Report illustration exhausted all model/payload fallbacks")
        return None

    def _request_image(self, api_key: str, model: str, payload: dict) -> bytes | None:
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

    def save_for_report(self, report_id: UUID, image_bytes: bytes) -> str:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        path = self.reports_dir / f"{report_id}.png"
        path.write_bytes(image_bytes)
        return f"{self.settings.media_url_prefix.rstrip('/')}/reports/{report_id}.png"

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
        return (
            f"{self.settings.media_url_prefix.rstrip('/')}"
            f"/front/{organization_id}/{path.name}"
        )

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
