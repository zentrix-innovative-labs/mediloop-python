# Mediloop for Python

A small client for the Mediloop API. No dependencies: it uses only the Python standard library.

## Use a supported Python

This library needs Python 3.11 or later. Run a release that still gets security fixes and keep it patched. A webhook endpoint is a
public door into your server, so it is the last place to run an old interpreter.

## Install

    pip install mediloop

## Use

```python
import os
from mediloop import Client

mediloop = Client(api_key=os.environ["MEDILOOP_API_KEY"])

# Act for one facility, in the name of the person doing the work.
facility = mediloop.for_facility(facility_id).on_behalf_of(clinician_uhid)

cover = facility.post("/api/v1/coverage/verify/", {
    "patient_uhid": "UG123456789A",
    "benefit_category": "consultation",
    "amount": 50000,
})

for bill in facility.paginate("/api/v1/healthcare-billing/bills/", {"open": True}):
    ...
```

What the client does for you:

- Exchanges your API key for a short-lived token, keeps it, and renews it before it ends (and once more if Mediloop refuses it).
- Sends `X-Facility-ID` and `X-On-Behalf-Of` when you scope the client.
- Gives every write an `Idempotency-Key` and re-sends the same key when it retries, so a retry never writes twice.
  Pass your own key as the last argument to tie a write to a record in your system.
- Retries a network failure, `429` (waiting as long as `Retry-After` says) and `502`, `503`, `504`. It never retries any other refusal.
- Raises `PermissionDeniedError`, `NotFoundError`, `ValidationError`, `ConflictError`, `RateLimitError` and the like,
  each carrying Mediloop's reason in `error.error_code` (for example `route_not_open`).
- Follows paging links only when they point at Mediloop.
- Refuses a base URL that is not https, and never follows a redirect, so a token cannot be sent to another address.

## Webhooks

Pass the body exactly as received, before you parse it.

```python
from mediloop import Webhook

event = Webhook.event(request.get_data(), request.headers["X-MediLoop-Signature"], signing_secret)
```

It checks the signature in constant time and refuses a delivery older than five minutes.

## Sign in with Mediloop

For an app people sign in to. Do the code exchange on your server: it sends your client secret.

```python
from mediloop import Pkce

p = Pkce.start()                      # keep p.verifier and p.state in the person's session
url = Pkce.authorize_url(client_id, redirect_uri, p.challenge, p.state)
# send the person to url; on return, check ?state equals p.state, then:
tokens = Pkce.exchange(client_id, redirect_uri, code, p.verifier, client_secret)
# later, before the access token ends:
fresh = Pkce.refresh(client_id, tokens["refresh_token"], client_secret)
```

The access token is the person's. Send it as a bearer token to call what the person may do in your app, such as
[calls and messages](https://docs.mediloop.co/docs/api/calls).

## Tests

    python tests/test_client.py        # no network
    MEDILOOP_API_KEY=... MEDILOOP_FACILITY=... MEDILOOP_PATIENT=... python tests/live.py
