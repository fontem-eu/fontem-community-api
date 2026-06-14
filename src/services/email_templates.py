"""Plain, brand-light transactional email bodies.

Deliberately minimal HTML — transactional mail lands in more inboxes
when it's simple and text-heavy. Both an html and a text part are
returned; the text part is also what suppress-mode logs, so it always
carries the action URL in plain sight.
"""
from __future__ import annotations


def verification_email(name: str, link: str) -> tuple[str, str, str]:
    """Return (subject, html, text) for the email-verification message."""
    subject = "Confirm your Fontem email"
    greeting = f"Hi {name}," if name else "Hi,"
    text = (
        f"{greeting}\n\n"
        "Welcome to Fontem. Confirm your email address to start "
        "publishing, commenting, and following stories:\n\n"
        f"{link}\n\n"
        "This link expires in 24 hours. If you didn't create a Fontem "
        "account, you can ignore this email.\n\n"
        "— The Fontem team"
    )
    html = (
        f"<p>{greeting}</p>"
        "<p>Welcome to Fontem. Confirm your email address to start "
        "publishing, commenting, and following stories:</p>"
        f'<p><a href="{link}">Confirm my email</a></p>'
        "<p>This link expires in 24 hours. If you didn't create a "
        "Fontem account, you can ignore this email.</p>"
        "<p>— The Fontem team</p>"
    )
    return subject, html, text


def password_reset_email(name: str, link: str) -> tuple[str, str, str]:
    """Return (subject, html, text) for the password-reset message."""
    subject = "Reset your Fontem password"
    greeting = f"Hi {name}," if name else "Hi,"
    text = (
        f"{greeting}\n\n"
        "We received a request to reset your Fontem password. Set a "
        "new one here:\n\n"
        f"{link}\n\n"
        "This link expires in 1 hour and can be used once. If you "
        "didn't request a reset, you can ignore this email — your "
        "password hasn't changed.\n\n"
        "— The Fontem team"
    )
    html = (
        f"<p>{greeting}</p>"
        "<p>We received a request to reset your Fontem password. Set a "
        "new one here:</p>"
        f'<p><a href="{link}">Reset my password</a></p>'
        "<p>This link expires in 1 hour and can be used once. If you "
        "didn't request a reset, you can ignore this email — your "
        "password hasn't changed.</p>"
        "<p>— The Fontem team</p>"
    )
    return subject, html, text
