"""
Generic Email Service
Reusable for:
- Ticket acknowledgement
- Status updates
- Assignment notifications
- SLA breach alerts
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from src.config.settings import settings
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="ticket-service")


class EmailService:

    def __init__(self):
        self.host = settings.SMTP_HOST
        self.port = int(settings.SMTP_PORT)
        self.user = settings.SMTP_USER
        self.password = settings.SMTP_PASSWORD
        self.from_email = settings.EMAIL_FROM

    # -----------------------------------------------------
    # Generic sender
    # -----------------------------------------------------
    def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
    ) -> None:
        """
        Generic email sender.
        Accepts HTML + optional plain text fallback.
        """

        logger.info(
            "email_send_started",
            to_email=to_email,
            subject=subject,
        )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_email
        msg["To"] = to_email

        if text_body:
            msg.attach(MIMEText(text_body, "plain"))

        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(self.host, self.port) as server:
                server.ehlo()
                server.starttls()
                server.login(self.user, self.password)
                server.sendmail(self.from_email, to_email, msg.as_string())

            logger.info(
                "email_send_success",
                to_email=to_email,
                subject=subject,
            )

        except Exception as e:
            # Do NOT crash ticket creation
            logger.exception(
                "email_send_failed",
                to_email=to_email,
                subject=subject,
                error=str(e),
            )