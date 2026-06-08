from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.chat import ChatAskRequest, ChatAskResponse
from app.services.chat_service import ChatService

router = APIRouter()


@router.post("/ask", response_model=ChatAskResponse)
def ask_chat(
    body: ChatAskRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ChatService(db).ask(user.organization_id, body.message.strip())
