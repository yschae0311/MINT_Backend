import base64
import logging
from pathlib import Path
from uuid import UUID

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Older preview ids 404; try current stable then preview fallback.
_IMAGE_MODEL_FALLBACKS = (
    "gemini-2.5-flash-image",
    "gemini-2.5-flash-image-preview",
)

NEWSPAPER_SKETCH_PREFIX = (
    "Black and white newspaper editorial sketch illustration, pen and ink cross-hatching, "
    "newsprint texture, high contrast grayscale only, no color, no text, no logos, "
    "no readable signs, no photorealism, no human faces, metaphorical scene: "
)


class ReportIllustrationService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.media_root = Path(self.settings.media_root)
        self.reports_dir = self.media_root / "reports"

    def _model_candidates(self) -> list[str]:
        primary = (self.settings.gemini_image_model or "").strip()
        ordered: list[str] = []
        for name in (primary, *_IMAGE_MODEL_FALLBACKS):
            if name and name not in ordered:
                ordered.append(name)
        return ordered

    def generate_image_bytes(self, scene: str) -> bytes | None:
        api_key = self.settings.gemini_api_key.strip()
        if not api_key or not self.settings.report_illustration_enabled:
            return None

        prompt = f"{NEWSPAPER_SKETCH_PREFIX}{scene.strip()}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": "4:3"},
            },
        }

        last_error: Exception | None = None
        for model in self._model_candidates():
            url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
            try:
                with httpx.Client(timeout=120.0) as client:
                    response = client.post(url, params={"key": api_key}, json=payload)
                    response.raise_for_status()
                    data = response.json()
            except Exception as exc:
                last_error = exc
                logger.warning("Report illustration API failed (model=%s): %s", model, exc)
                continue

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

            logger.warning("Report illustration API returned no image (model=%s)", model)

        if last_error:
            logger.warning("Report illustration exhausted model fallbacks: %s", last_error)
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
