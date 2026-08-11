"""Gemini TTS — high-quality narration via generateContent AUDIO modality."""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
import wave
from pathlib import Path

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_CHUNK_CHARS = 2200
_DEFAULT_SAMPLE_RATE = 24000

_NARRATION_PREFIX = (
    "다음 한국어 뉴스 브리핑을 차분하고 명확한 방송 앵커 톤으로 읽어주세요. "
    "숫자는 자연스럽게 읽고, 과도한 감정 표현은 하지 마세요.\n\n"
)


class TtsService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.cache_dir = Path(self.settings.media_root) / "tts"

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.tts_enabled
            and self.settings.gemini_api_key.strip()
        )

    def narrate_wav(self, text: str) -> bytes | None:
        if not self.enabled:
            return None

        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return None

        cache_key = self._cache_key(cleaned)
        cached = self._read_cache(cache_key)
        if cached:
            return cached

        chunks = self._chunk_text(cleaned)
        pcm_parts: list[bytes] = []
        sample_rate = _DEFAULT_SAMPLE_RATE

        for chunk in chunks:
            pcm, rate = self._synthesize_chunk(chunk)
            if not pcm:
                logger.warning("Gemini TTS returned empty audio for a chunk")
                return None
            sample_rate = rate or sample_rate
            pcm_parts.append(pcm)

        wav = self._pcm_to_wav(b"".join(pcm_parts), sample_rate=sample_rate)
        self._write_cache(cache_key, wav)
        return wav

    def _cache_key(self, text: str) -> str:
        raw = f"{self.settings.gemini_tts_model}|{self.settings.gemini_tts_voice}|{text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _read_cache(self, key: str) -> bytes | None:
        path = self.cache_dir / f"{key}.wav"
        if path.is_file() and path.stat().st_size > 44:
            return path.read_bytes()
        return None

    def _write_cache(self, key: str, wav: bytes) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{key}.wav").write_bytes(wav)
        except OSError as exc:
            logger.warning("TTS cache write failed: %s", exc)

    def _chunk_text(self, text: str) -> list[str]:
        if len(text) <= _CHUNK_CHARS:
            return [text]

        sentences = re.split(r"(?<=[.!?。！？…])\s+", text)
        chunks: list[str] = []
        buf = ""
        for sentence in sentences:
            piece = sentence.strip()
            if not piece:
                continue
            if buf and len(buf) + len(piece) + 1 > _CHUNK_CHARS:
                chunks.append(buf)
                buf = piece
            else:
                buf = f"{buf} {piece}".strip() if buf else piece
        if buf:
            chunks.append(buf)

        # Hard-split any leftover oversized chunk
        final: list[str] = []
        for chunk in chunks:
            if len(chunk) <= _CHUNK_CHARS:
                final.append(chunk)
                continue
            for i in range(0, len(chunk), _CHUNK_CHARS):
                final.append(chunk[i : i + _CHUNK_CHARS])
        return final or [text[:_CHUNK_CHARS]]

    def _synthesize_chunk(self, text: str) -> tuple[bytes | None, int]:
        api_key = self.settings.gemini_api_key.strip()
        model = self.settings.gemini_tts_model
        voice = self.settings.gemini_tts_voice
        url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": f"{_NARRATION_PREFIX}{text}"}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice},
                    }
                },
            },
        }

        try:
            with httpx.Client(timeout=180.0) as client:
                response = client.post(url, params={"key": api_key}, json=payload)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("Gemini TTS API failed (model=%s): %s", model, exc)
            return None, _DEFAULT_SAMPLE_RATE

        for candidate in data.get("candidates") or []:
            for part in candidate.get("content", {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if not inline:
                    continue
                raw = inline.get("data")
                if not raw:
                    continue
                mime = (inline.get("mimeType") or inline.get("mime_type") or "").lower()
                try:
                    audio = base64.b64decode(raw)
                except Exception as exc:
                    logger.warning("Gemini TTS decode failed: %s", exc)
                    return None, _DEFAULT_SAMPLE_RATE

                if "wav" in mime or audio[:4] == b"RIFF":
                    return self._wav_to_pcm(audio)
                if "mpeg" in mime or "mp3" in mime:
                    # Unexpected for this API; treat as opaque failure for concat path
                    logger.warning("Gemini TTS returned non-PCM audio (%s)", mime)
                    return None, _DEFAULT_SAMPLE_RATE

                rate = self._parse_sample_rate(mime)
                return audio, rate

        logger.warning("Gemini TTS API returned no audio (model=%s)", model)
        return None, _DEFAULT_SAMPLE_RATE

    @staticmethod
    def _parse_sample_rate(mime: str) -> int:
        match = re.search(r"rate=(\d+)", mime or "")
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return _DEFAULT_SAMPLE_RATE

    @staticmethod
    def _wav_to_pcm(wav_bytes: bytes) -> tuple[bytes, int]:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            rate = wf.getframerate()
            return wf.readframes(wf.getnframes()), rate

    @staticmethod
    def _pcm_to_wav(
        pcm: bytes,
        *,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        channels: int = 1,
        sample_width: int = 2,
    ) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return buf.getvalue()
