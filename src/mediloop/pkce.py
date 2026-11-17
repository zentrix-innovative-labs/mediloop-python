from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from .errors import MediloopError
from .transport import Transport, UrllibTransport

ISSUER = "https://auth.mediloop.co/realms/mediloop-healthcare"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class PkceStart:
    """Keep the verifier and the state in the person's session."""

    verifier: str
    challenge: str
    state: str


class Pkce:
    """"Sign in with Mediloop" for an app people sign in to: authorization code with PKCE."""

    @staticmethod
    def start() -> PkceStart:
        verifier = _b64url(secrets.token_bytes(48))
        return PkceStart(verifier, _b64url(hashlib.sha256(verifier.encode("ascii")).digest()), _b64url(secrets.token_bytes(24)))

    @staticmethod
    def authorize_url(client_id: str, redirect_uri: str, challenge: str, state: str, scope: str = "openid") -> str:
        return f"{ISSUER}/protocol/openid-connect/auth?" + urlencode({
            "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": scope, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256"})

    @staticmethod
    def exchange(client_id: str, redirect_uri: str, code: str, verifier: str, client_secret: str | None = None,
                 transport: Transport | None = None) -> dict[str, Any]:
        """Swap the code Mediloop sent back for the person's tokens.

        Check that the state you got back equals the one you kept. Do this on your server: it sends your client secret.
        """
        form = {"grant_type": "authorization_code", "client_id": client_id, "redirect_uri": redirect_uri, "code": code,
                "code_verifier": verifier}
        if client_secret:
            form["client_secret"] = client_secret
        return _token_request(form, transport, "Mediloop did not accept the sign-in code.")

    @staticmethod
    def refresh(client_id: str, refresh_token: str, client_secret: str | None = None, transport: Transport | None = None) -> dict[str, Any]:
        """Get a new access token with the refresh token. Do this on your server: it sends your client secret."""
        form = {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token}
        if client_secret:
            form["client_secret"] = client_secret
        return _token_request(form, transport, "Mediloop did not accept the refresh token. The person may have stopped your app.")


def _token_request(form: dict[str, str], transport: Transport | None, failure: str) -> dict[str, Any]:
    transport = transport or UrllibTransport()
    response = transport.send("POST", f"{ISSUER}/protocol/openid-connect/token",
                              {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                              urlencode(form), 30.0)
    try:
        data = json.loads(response.body)
    except ValueError:
        data = None
    if response.status != 200 or not isinstance(data, dict) or not data.get("access_token"):
        raise MediloopError(failure)
    return data
