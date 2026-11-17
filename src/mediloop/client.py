from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterator
from urllib.parse import urlencode

from .errors import (ApiError, AuthenticationError, ConflictError, ConnectionFailedError, MediloopError, NotFoundError,
                     PermissionDeniedError, RateLimitError, ServerError, ValidationError)
from .transport import Response, Transport, UrllibTransport

VERSION = "1.0.0"
DEFAULT_BASE_URL = "https://api.mediloop.co"
_RETRYABLE = {429, 502, 503, 504}


@dataclass
class _Token:
    value: str | None = None
    expires_at: float = 0.0


class Client:
    """The Mediloop API client.

        mediloop = Client(api_key=os.environ["MEDILOOP_API_KEY"])
        facility = mediloop.for_facility(facility_id).on_behalf_of(clinician_uhid)
        found = facility.post("/api/v1/uhid/integrations/identify/", {"nin": "CM..."})

    What it does for you, and nothing more:

    * exchanges your API key for a short-lived token, keeps it, and renews it before it ends;
    * sends X-Facility-ID and X-On-Behalf-Of when you scope the client;
    * gives every write an Idempotency-Key, and re-sends the SAME key on a retry, so a retry can never write twice;
    * retries a network failure, 429 and 502/503/504 with a pause, and never retries any other refusal;
    * turns a refusal into an exception that carries Mediloop's reason code.

    It has no dependencies.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        facility_id: str | None = None,
        on_behalf_of: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        transport: Transport | None = None,
        sleeper: Callable[[float], None] | None = None,
        _token: _Token | None = None,
    ) -> None:
        if not api_key:
            raise MediloopError("An API key is required.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        if not self._base_url.startswith("https://") and not self._base_url.startswith("http://localhost"):
            raise MediloopError("The base URL must be https.")
        self._facility_id = facility_id
        self._person = on_behalf_of
        self._timeout = timeout
        self._max_retries = max_retries
        self._transport: Transport = transport or UrllibTransport()
        self._sleeper = sleeper
        # Shared by the copies made with for_facility and on_behalf_of, so a renewal reaches all of them.
        self._token = _token or _Token()

    def for_facility(self, facility_id: str) -> "Client":
        """A copy that acts for one facility (X-Facility-ID)."""
        return self._copy(facility_id=facility_id)

    def on_behalf_of(self, uhid: str) -> "Client":
        """A copy that names the person doing the work (X-On-Behalf-Of): a clinician, a cashier, an insurer's officer."""
        return self._copy(on_behalf_of=uhid)

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, query=query)

    def post(self, path: str, json_body: Any = None, idempotency_key: str | None = None) -> Any:
        return self.request("POST", path, json_body={} if json_body is None else json_body, idempotency_key=idempotency_key)

    def patch(self, path: str, json_body: Any = None, idempotency_key: str | None = None) -> Any:
        return self.request("PATCH", path, json_body={} if json_body is None else json_body, idempotency_key=idempotency_key)

    def put(self, path: str, json_body: Any = None, idempotency_key: str | None = None) -> Any:
        return self.request("PUT", path, json_body={} if json_body is None else json_body, idempotency_key=idempotency_key)

    def paginate(self, path: str, query: dict[str, Any] | None = None) -> Iterator[Any]:
        """Every item of a paged list, following ``next`` until it ends."""
        page = self.get(path, query)
        while True:
            for item in (page or {}).get("results") or []:
                yield item
            nxt = (page or {}).get("next")
            if not isinstance(nxt, str) or nxt == "":
                return
            # Follow the link, but only to Mediloop: a link must never take your token elsewhere.
            if not nxt.startswith(self._base_url + "/"):
                raise MediloopError("A paging link pointed outside Mediloop and was not followed.")
            page = self.request("GET", nxt[len(self._base_url):])

    def request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        method = method.upper()
        url = self._base_url + "/" + path.lstrip("/")
        pairs = {k: ("true" if v is True else "false" if v is False else v) for k, v in (query or {}).items() if v is not None}
        if pairs:
            url += ("&" if "?" in url else "?") + urlencode(pairs)
        body = None if json_body is None else json.dumps(json_body, ensure_ascii=False)
        is_write = method not in ("GET", "HEAD", "OPTIONS")
        # One key for the whole call, retries included: that is what makes a retry safe.
        key = (idempotency_key or str(uuid.uuid4())) if is_write else None
        renewed = False
        attempt = 0
        while True:
            sent = {"Accept": "application/json", "User-Agent": f"mediloop-python/{VERSION}",
                    "Authorization": f"Bearer {self._access_token()}", **(headers or {})}
            if body is not None:
                sent["Content-Type"] = "application/json"
            if self._facility_id is not None:
                sent["X-Facility-ID"] = self._facility_id
            if self._person is not None:
                sent["X-On-Behalf-Of"] = self._person
            if key is not None:
                sent["Idempotency-Key"] = key
            try:
                response = self._transport.send(method, url, sent, body, self._timeout)
            except ConnectionFailedError:
                if attempt >= self._max_retries:
                    raise
                self._pause(attempt, None)
                attempt += 1
                continue
            if response.status == 401 and not renewed:
                # The token may have been revoked or expired early: get a new one once.
                renewed = True
                self._token.value = None
                continue
            if response.status in _RETRYABLE and attempt < self._max_retries:
                self._pause(attempt, response.headers.get("retry-after"))
                attempt += 1
                continue
            return self._decode(response)

    def _access_token(self) -> str:
        if self._token.value is not None and time.time() < self._token.expires_at - 30:
            return self._token.value
        body = json.dumps({"api_key": self._api_key})
        attempt = 0
        while True:
            try:
                response = self._transport.send(
                    "POST", self._base_url + "/api/v1/auth/token/",
                    {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": f"mediloop-python/{VERSION}"},
                    body, self._timeout)
            except ConnectionFailedError:
                if attempt >= self._max_retries:
                    raise
                self._pause(attempt, None)
                attempt += 1
                continue
            if response.status in _RETRYABLE and attempt < self._max_retries:
                self._pause(attempt, response.headers.get("retry-after"))
                attempt += 1
                continue
            break
        data = self._decode(response)
        if not isinstance(data, dict) or not data.get("access_token"):
            raise MediloopError("Mediloop did not return a token.")
        self._token.value = str(data["access_token"])
        self._token.expires_at = time.time() + float(data.get("expires_in") or 300)
        return self._token.value

    @staticmethod
    def _decode(response: Response) -> Any:
        data: Any = None
        if response.body != "":
            try:
                data = json.loads(response.body)
            except ValueError:
                data = None
        status = response.status
        if 200 <= status < 300:
            return data
        code = data.get("code") if isinstance(data, dict) and isinstance(data.get("code"), str) else None
        message = (str(data.get("error") or data.get("detail") or data.get("message") or "Mediloop refused the request.")
                   if isinstance(data, dict) else "Mediloop refused the request.")
        cls: type[ApiError]
        if status == 401:
            cls = AuthenticationError
        elif status == 403:
            cls = PermissionDeniedError
        elif status == 404:
            cls = NotFoundError
        elif status == 409:
            cls = ConflictError
        elif status == 429:
            cls = RateLimitError
        elif status in (400, 422):
            cls = ValidationError
        elif status >= 500:
            cls = ServerError
        else:
            cls = ApiError
        raise cls(status, code, message, data, response.headers.get("retry-after"))

    def _pause(self, attempt: int, retry_after: str | None) -> None:
        try:
            seconds = min(30.0, float(retry_after)) if retry_after is not None and retry_after.strip() != "" else None
        except ValueError:
            seconds = None
        if seconds is None:
            seconds = min(8.0, 0.5 * (2 ** attempt)) + random.uniform(0, 0.25)
        if self._sleeper is not None:
            self._sleeper(seconds)
        else:
            time.sleep(seconds)

    def _copy(self, facility_id: str | None = None, on_behalf_of: str | None = None) -> "Client":
        return Client(
            self._api_key, self._base_url,
            facility_id if facility_id is not None else self._facility_id,
            on_behalf_of if on_behalf_of is not None else self._person,
            self._timeout, self._max_retries, self._transport, self._sleeper, self._token)
