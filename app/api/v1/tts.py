from fastapi import APIRouter, Depends, Response

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.tts import NarrateRequest
from app.services.tts_service import TtsService

router = APIRouter()


@router.post("/narrate")
def narrate(
    body: NarrateRequest,
    user: User = Depends(get_current_user),
):
    """Synthesize narration audio (WAV). Requires auth to limit API spend."""
    _ = user
    settings = get_settings()
    if not settings.tts_enabled:
        raise ServiceUnavailableError("서버 TTS가 비활성화되어 있습니다.")
    if not settings.gemini_api_key.strip():
        raise ServiceUnavailableError("Gemini API 키가 설정되지 않았습니다.")

    text = body.text.strip()
    if not text:
        raise BadRequestError("읽을 텍스트가 비어 있습니다.")

    wav = TtsService().narrate_wav(text)
    if not wav:
        raise ServiceUnavailableError("음성 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.")

    return Response(
        content=wav,
        media_type="audio/wav",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": 'inline; filename="narration.wav"',
        },
    )
