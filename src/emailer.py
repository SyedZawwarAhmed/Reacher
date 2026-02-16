"""Gmail SMTP email sender with resume attachment."""

from __future__ import annotations

import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from src.models import AppConfig


def send_application_email(
    to_email: str,
    subject: str,
    body: str,
    config: AppConfig,
    resume_path: Optional[Path] = None,
) -> bool:
    """Send an application email via Gmail SMTP.

    Args:
        to_email: Recipient email address.
        subject: Email subject line.
        body: Email body text.
        config: Application config with SMTP credentials.
        resume_path: Optional path to resume PDF to attach.

    Returns:
        True if the email was sent successfully, False otherwise.
    """
    email_cfg = config.email

    msg = MIMEMultipart()
    msg["From"] = f"{email_cfg.sender_name} <{email_cfg.address}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Reply-To"] = email_cfg.address

    msg.attach(MIMEText(body, "plain", "utf-8"))

    if resume_path and resume_path.exists():
        try:
            with open(resume_path, "rb") as f:
                attachment = MIMEApplication(f.read(), _subtype="pdf")
                attachment.add_header(
                    "Content-Disposition",
                    "attachment",
                    filename=resume_path.name,
                )
                msg.attach(attachment)
        except Exception as e:
            print(f"  [Email] Warning: Could not attach resume: {e}")

    try:
        with smtplib.SMTP(email_cfg.smtp_server, email_cfg.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(email_cfg.address, email_cfg.app_password)
            server.send_message(msg)

        print(f"  [Email] Sent to {to_email}: {subject}")
        return True

    except smtplib.SMTPAuthenticationError:
        print(
            "  [Email] Authentication failed. Check your Gmail address and App Password.\n"
            "  Make sure 2FA is enabled and you're using an App Password, not your regular password."
        )
        return False
    except smtplib.SMTPRecipientsRefused as e:
        print(f"  [Email] Recipient refused ({to_email}): {e}")
        return False
    except smtplib.SMTPException as e:
        print(f"  [Email] SMTP error: {e}")
        return False
    except Exception as e:
        print(f"  [Email] Unexpected error: {e}")
        return False
