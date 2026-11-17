from __future__ import annotations

import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from .errors import ConnectionFailedError


@dataclass
class Response:
    status: int
    #: Header names in lower case.
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""


class Transport(Protocol):
    """Sends one HTTP request. Swap it for your own (a proxy-aware client, or a fake in tests)."""

    def send(self, method: str, url: str, headers: dict[str, str], body: str | None, timeout: float) -> Response:
        ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        # Never follow a redirect: a token must not travel to an address you did not choose.
        return None


class UrllibTransport:
    """The default transport: the standard library, with no redirects."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect)

    def send(self, method: str, url: str, headers: dict[str, str], body: str | None, timeout: float) -> Response:
        data = body.encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=timeout) as reply:
                return Response(reply.status, {k.lower(): v for k, v in reply.headers.items()}, reply.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as error:
            # A refusal (and a redirect we did not follow) is an answer, not a failure to connect.
            payload = error.read().decode("utf-8", "replace")
            return Response(error.code, {k.lower(): v for k, v in error.headers.items()}, payload)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as error:
            raise ConnectionFailedError(f"Mediloop could not be reached: {error}") from error
