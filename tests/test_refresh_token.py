"""Refresh token issue / rotate / revoke."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import UnauthorizedError
from app.core.security import hash_refresh_token
from app.models.enums import AccountApprovalStatus, UserRole
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.auth_service import AuthService


def _user() -> User:
    return User(
        id=uuid4(),
        organization_id=uuid4(),
        email="tv@example.com",
        password_hash="x",
        name="TV",
        role=UserRole.admin,
        approval_status=AccountApprovalStatus.approved,
        is_active=True,
    )


def test_hash_refresh_token_is_stable():
    assert hash_refresh_token("abc") == hash_refresh_token("abc")
    assert hash_refresh_token("abc") != hash_refresh_token("abd")


def test_refresh_rejects_unknown_token():
    db = MagicMock()
    db.scalar.return_value = None
    with pytest.raises(UnauthorizedError):
        AuthService(db).refresh("missing-token-value-here")


def test_refresh_rejects_expired_token():
    db = MagicMock()
    user = _user()
    row = RefreshToken(
        id=uuid4(),
        user_id=user.id,
        token_hash=hash_refresh_token("old-refresh-token-value"),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        revoked_at=None,
    )
    row.user = user
    db.scalar.return_value = row

    with pytest.raises(UnauthorizedError, match="expired"):
        AuthService(db).refresh("old-refresh-token-value")
