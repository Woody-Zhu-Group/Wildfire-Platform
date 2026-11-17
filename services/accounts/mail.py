"""Account mail through SES or SMTP, with no credentials or invitation token in logs."""

from __future__ import annotations

import smtplib
import ssl
from contextlib import suppress
from email.message import EmailMessage

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from services.accounts.config import Settings


class MailUnavailable(Exception):
    pass


class Mailer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def invite(self, email: str, raw: str) -> None:
        self.send(email, "Your Wildfire Platform invitation", "You have been invited to Wildfire Platform.\n\n"
                  + self.settings.public_origin + "/invite#token=" + raw)

    def access_requested(self, email: str, event) -> None:
        self.send(email, "New Wildfire Platform access request",
                  f"{event.name} ({event.email}) from {event.organization} asked for access.\n\n"
                  f"Intended use:\n{event.purpose}\n\nReview it in the console: {self.settings.public_origin}/admin")

    def review(self, email: str, status: str, note: str) -> None:
        self.send(email, "Your Wildfire Platform access request",
                  "Your access request has been " + status + ".\n\n" + note + "\n\n" + self.settings.public_origin + "/access-status")

    def send(self, email: str, subject: str, content: str) -> None:
        if not self.settings.mail_from:
            raise MailUnavailable("mail_not_configured")
        if self.settings.mail_transport == "smtp":
            self.send_smtp(email, subject, content)
        else:
            self.send_ses(email, subject, content)

    def send_smtp(self, email: str, subject: str, content: str) -> None:
        """Encrypted SMTP only: implicit TLS on port 465, otherwise STARTTLS or nothing is sent."""
        settings = self.settings
        context = ssl.create_default_context()
        server = None
        try:
            message = EmailMessage()
            message["From"] = settings.mail_from
            message["To"] = email
            message["Subject"] = subject
            message.set_content(content)
            if settings.smtp_port == 465:
                server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10, context=context)
            else:
                server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
                server.starttls(context=context)
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
        except (smtplib.SMTPException, OSError, ValueError) as exc:
            raise MailUnavailable("mail_delivery_failed") from exc
        finally:
            if server is not None:
                # A failed goodbye must not turn an accepted message into a reported failure.
                with suppress(smtplib.SMTPException, OSError):
                    server.quit()
                server.close()

    def send_ses(self, email: str, subject: str, content: str) -> None:
        try:
            client = boto3.client(
                "sesv2", region_name=self.settings.aws_region,
                config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}),
            )
            try:
                client.send_email(
                    FromEmailAddress=self.settings.mail_from,
                    Destination={"ToAddresses": [email]},
                    Content={"Simple": {
                        "Subject": {"Data": subject, "Charset": "UTF-8"},
                        "Body": {"Text": {"Data": content, "Charset": "UTF-8"}},
                    }},
                )
            finally:
                client.close()
        except (BotoCoreError, ClientError) as exc:
            raise MailUnavailable("mail_delivery_failed") from exc
