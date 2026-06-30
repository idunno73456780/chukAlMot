from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from runtime_config import config_bool, config_int, config_value


@dataclass
class EmailSendResult:
    status: str
    message: str
    preview_path: str | None = None


def save_email_preview(subject: str, body_markdown: str, output_path: Path) -> EmailSendResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"# {subject}\n\n{body_markdown}", encoding="utf-8")
    return EmailSendResult(
        status="preview_saved",
        message="SMTP settings are not configured, so an email preview was saved.",
        preview_path=str(output_path),
    )


def send_markdown_email(
    subject: str,
    body_markdown: str,
    to_email: str,
    preview_path: Path,
) -> EmailSendResult:
    try:
        from google_oauth_client import google_oauth_connected, send_oauth_markdown_email

        if google_oauth_connected() and to_email:
            send_oauth_markdown_email(to_email, subject, body_markdown)
            return EmailSendResult(status="sent", message=f"Gmail sent to {to_email}.")
    except Exception:
        pass

    smtp_host = config_value("EMAIL_SMTP_HOST")
    smtp_port = config_int("EMAIL_SMTP_PORT", 587)
    smtp_user = config_value("EMAIL_SMTP_USER")
    smtp_password = config_value("EMAIL_SMTP_PASSWORD")
    email_from = config_value("EMAIL_FROM") or smtp_user
    use_ssl = config_bool("EMAIL_SMTP_SSL", False)

    if not to_email:
        return save_email_preview(subject, body_markdown, preview_path)

    if not smtp_host or not email_from:
        return save_email_preview(subject, body_markdown, preview_path)

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_from
    message["To"] = to_email
    message.set_content(body_markdown)

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as smtp:
                if smtp_user and smtp_password:
                    smtp.login(smtp_user, smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
                smtp.starttls()
                if smtp_user and smtp_password:
                    smtp.login(smtp_user, smtp_password)
                smtp.send_message(message)
    except Exception as exc:
        preview = save_email_preview(subject, body_markdown, preview_path)
        preview.status = "send_failed_preview_saved"
        preview.message = f"Email sending failed, preview saved instead: {exc}"
        return preview

    return EmailSendResult(status="sent", message=f"Email sent to {to_email}.")
