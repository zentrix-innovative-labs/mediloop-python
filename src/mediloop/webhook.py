from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any

from .errors import MediloopError


class Webhook:
    """Checks that a webhook really came from Mediloop and was not changed or replayed."""

    @staticmethod
    def verify(raw_body: bytes | str, signature_header: str, secret: str, tolerance_seconds: int = 300, now: int | None = None) -> None:
        """Raise MediloopError unless the delivery is authentic.

        ``raw_body`` is the body exactly as received: do not parse and re-serialise it first.
        ``signature_header`` is the X-MediLoop-Signature header, ``t=<seconds>,v1=<hex>``.
        """
        timestamp: str | None = None
        signatures: list[str] = []
        for part in (signature_header or "").split(","):
            name, sep, value = part.strip().partition("=")
            if not sep:
                continue
            if name == "t":
                timestamp = value
            elif name == "v1":
                signatures.append(value)
        if timestamp is None or not re.fullmatch(r"\d+", timestamp) or not signatures:
            raise MediloopError("The webhook signature header is not valid.")
        body = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
        matched = False
        for candidate in signatures:
            # Compare every candidate in constant time, and do not stop at the first.
            matched = hmac.compare_digest(expected, candidate) or matched
        if not matched:
            raise MediloopError("The webhook signature does not match.")
        if abs((now if now is not None else int(time.time())) - int(timestamp)) > tolerance_seconds:
            raise MediloopError("The webhook is too old. It may be a replay.")

    @staticmethod
    def event(raw_body: bytes | str, signature_header: str, secret: str, tolerance_seconds: int = 300) -> dict[str, Any]:
        """The decoded event, after it has been verified."""
        Webhook.verify(raw_body, signature_header, secret, tolerance_seconds)
        try:
            data = json.loads(raw_body)
        except ValueError as error:
            raise MediloopError("The webhook body is not JSON.") from error
        if not isinstance(data, dict):
            raise MediloopError("The webhook body is not JSON.")
        return data
