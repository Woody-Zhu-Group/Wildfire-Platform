"""Account notifications as events and observers.

Routes publish what happened after their transaction commits; observers
subscribed at startup decide who hears about it and how (SES mail today). A
failing observer never undoes the event or stops the other observers, and
nothing an observer receives is logged.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

log = logging.getLogger("services.accounts.notify")


@dataclass(frozen=True)
class AccessRequested:
    request_id: UUID
    name: str
    email: str
    organization: str
    purpose: str


@dataclass(frozen=True)
class AccessReviewed:
    request_id: UUID
    email: str
    status: str
    public_note: str


@dataclass(frozen=True)
class InvitationIssued:
    invitation_id: UUID
    email: str
    token: str = field(repr=False)


Event = AccessRequested | AccessReviewed | InvitationIssued
Observer = Callable[[Event], None]
Delivery = Literal["sent", "failed"]


class Notifier:
    """Delivers each published event to every observer subscribed to its type."""

    def __init__(self) -> None:
        self.observers: dict[type, list[Observer]] = defaultdict(list)

    def subscribe(self, event_type: type, observer: Observer) -> None:
        self.observers[event_type].append(observer)

    def publish(self, event: Event) -> Delivery:
        """`failed` when any observer raised; the event itself already happened."""
        delivery: Delivery = "sent"
        for observer in self.observers[type(event)]:
            try:
                observer(event)
            except Exception as error:  # noqa: BLE001 - one channel must not silence the rest
                # Only the kinds: messages can carry addresses or tokens.
                log.warning("%s observer failed: %s", type(event).__name__, type(error).__name__)
                delivery = "failed"
        return delivery


class AdminAlert:
    """Tells every active administrator that someone asked for access."""

    def __init__(self, store, mailer) -> None:
        self.store, self.mailer = store, mailer

    def __call__(self, event: AccessRequested) -> None:
        failed = 0
        recipients = self.store.active_admin_emails()
        for email in recipients:
            try:
                self.mailer.access_requested(email, event)
            except Exception:  # noqa: BLE001 - try every administrator before reporting
                failed += 1
        if failed:
            raise RuntimeError(f"{failed} of {len(recipients)} administrator alerts failed")


def build_notifier(store, mailer) -> Notifier:
    notifier = Notifier()
    notifier.subscribe(InvitationIssued, lambda event: mailer.invite(event.email, event.token))
    notifier.subscribe(AccessReviewed, lambda event: mailer.review(event.email, event.status, event.public_note))
    notifier.subscribe(AccessRequested, AdminAlert(store, mailer))
    return notifier
