"""personalization_enabled=false blocks personal feed/report APIs."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.api.v1 import personalization as personalization_api
from app.core.exceptions import ForbiddenError


def test_require_personalization_disabled_raises():
    with patch("app.api.v1.personalization.get_settings") as gs:
        gs.return_value = MagicMock(personalization_enabled=False)
        with pytest.raises(ForbiddenError):
            personalization_api._require_personalization_enabled()


def test_require_personalization_enabled_ok():
    with patch("app.api.v1.personalization.get_settings") as gs:
        gs.return_value = MagicMock(personalization_enabled=True)
        personalization_api._require_personalization_enabled()
