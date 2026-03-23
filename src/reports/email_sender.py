"""Gmail SMTP email sender."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from src.config import AppConfig

logger = logging.getLogger(__name__)


def send_report_email(
    config: AppConfig,
    subject: str,
    html_body: str,
    pdf_path: Path | None = None,
) -> bool:
    """Send the weekly report via Gmail SMTP.

    Returns True if email was sent successfully.
    """
    email_cfg = config.reports.email
    if not email_cfg.enabled:
        logger.info("Email sending is disabled in config")
        return False

    if not email_cfg.sender_email or not email_cfg.sender_password:
        logger.warning("Email credentials not configured")
        return False

    msg = MIMEMultipart("mixed")
    msg["From"] = email_cfg.sender_email
    msg["To"] = email_cfg.recipient_email
    msg["Subject"] = subject

    # HTML body
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # PDF attachment
    if pdf_path and pdf_path.exists():
        with open(pdf_path, "rb") as f:
            pdf_attachment = MIMEApplication(f.read(), _subtype="pdf")
            pdf_attachment.add_header(
                "Content-Disposition", "attachment", filename=pdf_path.name
            )
            msg.attach(pdf_attachment)

    try:
        with smtplib.SMTP(email_cfg.smtp_server, email_cfg.smtp_port, timeout=30) as server:
            server.ehlo()
            context = ssl.create_default_context()
            server.starttls(context=context)
            server.ehlo()
            server.login(email_cfg.sender_email, email_cfg.sender_password)
            server.sendmail(
                email_cfg.sender_email,
                email_cfg.recipient_email,
                msg.as_string(),
            )
        logger.info(f"Report email sent to {email_cfg.recipient_email}")
        return True
    except smtplib.SMTPAuthenticationError:
        logger.exception("SMTP authentication failed — check email credentials")
        return False
    except smtplib.SMTPException:
        logger.exception("SMTP error while sending report email")
        return False
    except Exception:
        logger.exception("Unexpected error sending report email")
        return False
