"""Invitation delivery, with no credentials or invitation token in logs."""

from __future__ import annotations

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
import boto3

from services.accounts.config import Settings


class MailUnavailable(Exception):
    pass


class Mailer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def invite(self, email: str, raw: str) -> None:
        self.send(email, "Your Wildfire Platform invitation", "You have been invited to Wildfire Platform.\n\n"
                  + self.settings.public_origin + "/invite#token=" + raw)

    def review(self, email: str, status: str, note: str) -> None:
        self.send(email, "Your Wildfire Platform access request",
                  "Your access request has been " + status + ".\n\n" + note + "\n\n" + self.settings.public_origin + "/access-status")

    def send(self, email: str, subject: str, content: str) -> None:
        if not self.settings.mail_from:
            raise MailUnavailable("mail_not_configured")
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
