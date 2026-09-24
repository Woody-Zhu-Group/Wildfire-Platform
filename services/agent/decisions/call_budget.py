"""Daily Jev API call budget, shared by shadow mode and decide mode.

AGENT_JEV_DAILY_CALL_CAP counts API calls, not user questions. One question
makes several calls (three for the v3_hybrid disposition in decide mode, up to
four in shadow mode with a tool pick), and every one of them counts. The budget
is per process and resets at the UTC day boundary. With more than one uvicorn
worker each worker has its own budget, so total spend is workers times the cap.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from typing import Callable


class DailyCallBudget:
    def __init__(self, cap: int, *, clock: Callable[[], datetime] | None = None) -> None:
        self.cap = int(cap)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._day: date = self._clock().date()
        self.calls_today = 0
        self.blocked = 0

    def _roll(self) -> None:
        today = self._clock().date()
        if today != self._day:
            self._day = today
            self.calls_today = 0

    def reserve(self, calls: int) -> bool:
        """Reserve `calls` API calls for one question, or none of them.

        A question is admitted only if every call it will make fits under the cap,
        so a question never runs partially and a blocked question spends nothing.
        """
        calls = max(0, int(calls))
        with self._lock:
            self._roll()
            if self.calls_today + calls > self.cap:
                self.blocked += 1
                return False
            self.calls_today += calls
            return True

    @property
    def remaining(self) -> int:
        with self._lock:
            self._roll()
            return max(0, self.cap - self.calls_today)
