"""Failure classification and sticky-session rules, independent of transport."""
import time
from dataclasses import dataclass


@dataclass
class KeyState:
    status: str = "ready"
    until: float = 0
    failures: int = 0
    reason: str = ""

    def available(self, now=None):
        now = time.time() if now is None else now
        return self.status == "ready" or (self.status == "cooldown" and now >= self.until)

    def success(self):
        self.status, self.until, self.failures, self.reason = "ready", 0, 0, ""

    def failure(self, status, code="", retry_after=30, now=None):
        now = time.time() if now is None else now
        self.failures += 1
        if status == 401:
            self.status, self.reason = "invalid", "authentication"
        elif code in ("insufficient_quota", "budget_exceeded", "quota_exceeded") or status == 402:
            self.status, self.reason = "exhausted", "quota"
        elif status == 429:
            self.status, self.reason = "cooldown", "rate_limit"
            self.until = now + min(max(float(retry_after), 1), 3600)
        elif (status >= 500 or status == 0) and self.failures >= 3:
            self.status, self.reason, self.until = "cooldown", "upstream_failure", now + 30
        # 403/404/400 can be model/request-specific, not evidence of a dead key.


def stateful(body):
    if body.get("previous_response_id") or body.get("conversation"):
        return True
    items = body.get("input")
    if not isinstance(items, list):
        return False
    return any(isinstance(item, dict) and (
        item.get("encrypted_content") or item.get("type", "").endswith("_call_output")
    ) for item in items)
