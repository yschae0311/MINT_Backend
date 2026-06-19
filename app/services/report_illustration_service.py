import base64
import logging
from pathlib import Path
from uuid import UUID

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

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

    def generate_image_bytes(self, scene: str) -> bytes | None:
        api_key = self.settings.gemini_api_key.strip()
        if not api_key or not self.settings.report_illustration_enabled:
            return None

        prompt = f"{NEWSPAPER_SKETCH_PREFIX}{scene.strip()}"
        model = self.settings.gemini_image_model
        url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }

        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(url, params={"key": api_key}, json=payload)
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

        logger.warning("Report illustration API returned no image (model=%s)", model)
        return None

    def save_for_report(self, report_id: UUID, image_bytes: bytes) -> str:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        path = self.reports_dir / f"{report_id}.png"
        path.write_bytes(image_bytes)
        return f"{self.settings.media_url_prefix.rstrip('/')}/reports/{report_id}.png"
