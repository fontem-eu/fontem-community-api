"""Outbound transactional mail via Brevo (ex-Sendinblue).

One thin abstraction over the provider so a future swap is a single
file. Two operating modes, chosen by ``MAIL_SUPPRESS``:

- **suppress** (non-prod default): nothing is sent. The fully-rendered
  message — subject, recipient, and crucially the action link — is
  logged at INFO so a staging dev can grab the verification/reset URL
  out of the pod logs without a real inbox. Burns no provider quota
  and can't accidentally email a real person from a test run.
- **live** (prod): POSTs to Brevo's transactional endpoint.

The Brevo API key + sender identity come from env (Vault-backed in
the cluster): ``BREVO_API_KEY``, ``MAIL_FROM``, ``MAIL_FROM_NAME``,
``MAIL_REPLY_TO``.

Failures are retried on transient (5xx / network) with a short
backoff; a hard 4xx (bad key, rejected recipient) is surfaced
immediately — retrying won't help. Mail failure never propagates out
of the *flow* services as a 500: registering should still succeed
even if the verification mail bounces (the user can hit "resend").
The flow services catch and log; this class just reports success.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger("fontem.mail")

_BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 0.5


@dataclass(frozen=True)
class MailMessage:
    to_email: str
    to_name: str
    subject: str
    html: str
    text: str


class MailSendError(Exception):
    """Raised when a live send fails after retries. The flow services
    catch this — a failed verification mail must not 500 a register."""


class MailService:
    def __init__(self) -> None:
        self._suppress = os.environ.get("MAIL_SUPPRESS", "true").lower() == "true"
        self._api_key = os.environ.get("BREVO_API_KEY", "")
        self._from_email = os.environ.get("MAIL_FROM", "noreply@fontem.eu")
        self._from_name = os.environ.get("MAIL_FROM_NAME", "Dargle")
        self._reply_to = os.environ.get("MAIL_REPLY_TO", "support@fontem.eu")

    @property
    def suppressed(self) -> bool:
        return self._suppress

    async def send(self, msg: MailMessage) -> None:
        if self._suppress:
            # The link is the thing a staging dev actually needs; log
            # the whole text body so it's grep-able in pod logs. WARNING
            # rather than INFO so it surfaces regardless of the root
            # logger's level (uvicorn leaves root at WARNING; an INFO
            # line here would be silently dropped) — and "we did not
            # actually send this mail" genuinely is a warn-worthy
            # condition in any env where someone's watching.
            logger.warning(
                "MAIL SUPPRESSED → to=%s subject=%r\n--- body ---\n%s\n--- end ---",
                msg.to_email, msg.subject, msg.text,
            )
            return

        if not self._api_key:
            # Live mode with no key is a misconfiguration; surface it
            # loudly rather than silently dropping mail.
            raise MailSendError("MAIL_SUPPRESS=false but BREVO_API_KEY is unset")

        payload = {
            "sender": {"email": self._from_email, "name": self._from_name},
            "replyTo": {"email": self._reply_to},
            "to": [{"email": msg.to_email, "name": msg.to_name or msg.to_email}],
            "subject": msg.subject,
            "htmlContent": msg.html,
            "textContent": msg.text,
        }
        headers = {
            "api-key": self._api_key,
            "content-type": "application/json",
            "accept": "application/json",
        }

        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=10.0) as client:
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    resp = await client.post(_BREVO_ENDPOINT, json=payload, headers=headers)
                    if resp.status_code < 300:
                        return
                    # 4xx is a permanent error — bad key, rejected
                    # recipient. Retrying is pointless; fail now.
                    if 400 <= resp.status_code < 500:
                        raise MailSendError(
                            f"Brevo rejected the send: {resp.status_code} {resp.text[:200]}"
                        )
                    # 5xx — transient; fall through to retry.
                    last_exc = MailSendError(
                        f"Brevo 5xx: {resp.status_code} {resp.text[:200]}"
                    )
                except httpx.HTTPError as e:
                    last_exc = e
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_BACKOFF_SECONDS * attempt)
        raise MailSendError(f"Brevo send failed after {_MAX_ATTEMPTS} attempts: {last_exc}")
