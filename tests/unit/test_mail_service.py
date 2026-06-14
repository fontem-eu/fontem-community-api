"""MailService — suppress mode + the live-mode misconfig guard.

We never hit Brevo in unit tests. We pin two behaviours:
- suppress mode (the default) never sends and never raises.
- live mode with no API key fails loud rather than silently dropping.
"""
from __future__ import annotations

import pytest

from src.services.mail_service import MailMessage, MailSendError, MailService


def _msg():
    return MailMessage(
        to_email="x@test.com", to_name="X",
        subject="Hi", html="<p>hi</p>", text="hi https://fontem.eu/verify-email?token=abc",
    )


@pytest.mark.asyncio
async def test_suppress_mode_does_not_raise(monkeypatch):
    monkeypatch.setenv("MAIL_SUPPRESS", "true")
    svc = MailService()
    assert svc.suppressed is True
    await svc.send(_msg())  # logs, returns, no exception


@pytest.mark.asyncio
async def test_live_mode_without_key_raises(monkeypatch):
    monkeypatch.setenv("MAIL_SUPPRESS", "false")
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    svc = MailService()
    assert svc.suppressed is False
    with pytest.raises(MailSendError, match="BREVO_API_KEY"):
        await svc.send(_msg())
