"""Email delivery via Resend. Falls back to stdout in dev (no API key)."""

import os

from dotenv import load_dotenv

load_dotenv()


def send_reset_email(to_email: str, reset_url: str):
    """Send a password reset email. Logs to stdout if RESEND_API_KEY is not set."""
    api_key = os.getenv("RESEND_API_KEY")
    from_addr = os.getenv("RESEND_FROM", "GoBuga <noreply@gobuga.org>")

    if not api_key:
        print(f"[EMAIL] No RESEND_API_KEY — reset link for {to_email}:\n  {reset_url}")
        return

    import resend

    resend.api_key = api_key
    resend.Emails.send({
        "from": from_addr,
        "to": [to_email],
        "subject": "Reset your GoBuga password",
        "html": (
            f"<p>Hi,</p>"
            f"<p>We received a request to reset your password.</p>"
            f'<p><a href="{reset_url}">Click here to set a new password</a></p>'
            f"<p>This link expires in 15 minutes. If you didn't request this, you can ignore this email.</p>"
            f"<p>— GoBuga</p>"
        ),
        "text": (
            f"Hi,\n\n"
            f"We received a request to reset your password.\n\n"
            f"Reset your password: {reset_url}\n\n"
            f"This link expires in 15 minutes. If you didn't request this, you can ignore this email.\n\n"
            f"— GoBuga"
        ),
    })
