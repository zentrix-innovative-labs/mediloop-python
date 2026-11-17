import hashlib
import hmac
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mediloop import (ApiError, AuthenticationError, Client, ConnectionFailedError, MediloopError, NotFoundError, Pkce,
                      PermissionDeniedError, RateLimitError, Response, ValidationError, Webhook)


class Fake:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def send(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        if not self.script:
            raise AssertionError(f"Unexpected call: {method} {url}")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return Response(step.get("status", 200), step.get("headers", {}), step.get("body", ""))


TOK = {"status": 200, "body": json.dumps({"access_token": "tok1", "expires_in": 600})}
OK = {"status": 200, "body": '{"results":[1]}'}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.slept = []

    def make(self, fake, **extra):
        return Client(api_key="sk_live_x", transport=fake, sleeper=self.slept.append, **extra)

    def test_token_is_fetched_once_and_reused(self):
        t = Fake([TOK, OK, OK])
        c = self.make(t)
        c.get("/api/v1/a/")
        c.get("/api/v1/b/")
        self.assertEqual(len(t.calls), 3)
        self.assertTrue(t.calls[0][1].endswith("/api/v1/auth/token/"))
        self.assertEqual(t.calls[1][2]["Authorization"], "Bearer tok1")
        self.assertNotIn("Idempotency-Key", t.calls[1][2])

    def test_scoping_headers_and_shared_token(self):
        t = Fake([TOK, OK, OK])
        base = self.make(t)
        base.for_facility("F1").on_behalf_of("UG1").get("/api/v1/x/")
        self.assertEqual(t.calls[1][2]["X-Facility-ID"], "F1")
        self.assertEqual(t.calls[1][2]["X-On-Behalf-Of"], "UG1")
        base.get("/api/v1/y/")
        self.assertEqual(len(t.calls), 3, "the copy shared the token with the original")

    def test_a_write_has_a_key_and_a_retry_resends_the_same_key(self):
        t = Fake([TOK, {"status": 503}, {"status": 502}, {"status": 201, "body": '{"id":"r1"}'}])
        self.assertEqual(self.make(t).post("/api/v1/things/", {"a": 1}), {"id": "r1"})
        keys = {c[2].get("Idempotency-Key") for c in t.calls[1:]}
        self.assertEqual(len(keys), 1)
        self.assertTrue(next(iter(keys)))

    def test_a_given_key_is_honoured(self):
        t = Fake([TOK, {"status": 201, "body": "{}"}])
        self.make(t).post("/x/", {}, "mine-1")
        self.assertEqual(t.calls[1][2]["Idempotency-Key"], "mine-1")

    def test_connection_failure_is_retried_then_raised(self):
        t = Fake([TOK] + [ConnectionFailedError("down")] * 3)
        with self.assertRaises(ConnectionFailedError):
            self.make(t).get("/x/")
        self.assertEqual(len(t.calls), 4)

    def test_retry_after_is_respected(self):
        t = Fake([TOK, {"status": 429, "headers": {"retry-after": "3"}}, OK])
        self.make(t).get("/x/")
        self.assertEqual(self.slept, [3.0])

    def test_other_refusals_are_not_retried_and_carry_the_reason(self):
        t = Fake([TOK, {"status": 403, "body": '{"error":"This route is not open to outside software.","code":"route_not_open"}'}])
        with self.assertRaises(PermissionDeniedError) as caught:
            self.make(t).get("/x/")
        self.assertEqual(caught.exception.error_code, "route_not_open")
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(len(t.calls), 2)
        with self.assertRaises(NotFoundError):
            self.make(Fake([TOK, {"status": 404, "body": '{"detail":"No Claim matches the given query."}'}])).get("/x/")
        t = Fake([TOK, {"status": 400, "body": '{"message":"bad"}'}])
        with self.assertRaises(ValidationError):
            self.make(t).post("/x/", {})
        self.assertEqual(len(t.calls), 2)
        with self.assertRaises(RateLimitError):
            self.make(Fake([TOK, {"status": 429}, {"status": 429}, {"status": 429}])).get("/x/")

    def test_a_401_renews_the_token_once(self):
        renewed = {"status": 200, "body": json.dumps({"access_token": "tok2", "expires_in": 600})}
        t = Fake([TOK, {"status": 401, "body": '{"error":"expired"}'}, renewed, OK])
        self.make(t).get("/x/")
        self.assertEqual(t.calls[3][2]["Authorization"], "Bearer tok2")
        with self.assertRaises(AuthenticationError):
            self.make(Fake([TOK, {"status": 401}, renewed, {"status": 401}])).get("/x/")

    def test_paginate_follows_next_and_refuses_a_foreign_link(self):
        t = Fake([TOK,
                  {"status": 200, "body": json.dumps({"results": [1, 2], "next": "https://api.mediloop.co/api/v1/p/?page=2"})},
                  {"status": 200, "body": json.dumps({"results": [3], "next": None})}])
        self.assertEqual(list(self.make(t).paginate("/api/v1/p/")), [1, 2, 3])
        t = Fake([TOK, {"status": 200, "body": json.dumps({"results": [1], "next": "https://evil.example/steal"})}])
        with self.assertRaises(MediloopError):
            list(self.make(t).paginate("/api/v1/p/"))

    def test_settings_are_checked(self):
        with self.assertRaises(MediloopError):
            Client("k", base_url="http://api.mediloop.co")
        with self.assertRaises(MediloopError):
            Client("")

    def test_a_query_is_encoded(self):
        t = Fake([TOK, OK])
        self.make(t).get("/api/v1/p/", {"open": True, "page_size": 10, "skip": None})
        self.assertTrue(t.calls[1][1].endswith("/api/v1/p/?open=true&page_size=10"))

    def test_an_unexpected_status_is_an_api_error(self):
        with self.assertRaises(ApiError):
            self.make(Fake([TOK, {"status": 418, "body": "{}"}])).get("/x/")


SECRET = "whsec_test"
BODY = '{"event":"payment.succeeded"}'
NOW = 1763366400


def sign(body, ts, secret):
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()


class WebhookTests(unittest.TestCase):
    def test_verification(self):
        Webhook.verify(BODY, sign(BODY, NOW, SECRET), SECRET, 300, NOW)
        Webhook.verify(BODY.encode(), sign(BODY, NOW, SECRET), SECRET, 300, NOW)
        for body, header in ((BODY + " ", sign(BODY, NOW, SECRET)), (BODY, sign(BODY, NOW, "other")), (BODY, sign(BODY, NOW - 1000, SECRET)),
                             (BODY, "garbage"), (BODY, f"t={NOW},v1=short")):
            with self.assertRaises(MediloopError):
                Webhook.verify(body, header, SECRET, 300, NOW)
        good = hmac.new(SECRET.encode(), f"{NOW}.{BODY}".encode(), hashlib.sha256).hexdigest()
        Webhook.verify(BODY, f"t={NOW},v1=bad,v1={good}", SECRET, 300, NOW)

    def test_event(self):
        import time
        now = int(time.time())
        self.assertEqual(Webhook.event(BODY, sign(BODY, now, SECRET), SECRET), {"event": "payment.succeeded"})


class PkceTests(unittest.TestCase):
    def test_flow(self):
        import base64
        p = Pkce.start()
        expected = base64.urlsafe_b64encode(hashlib.sha256(p.verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(p.challenge, expected)
        url = Pkce.authorize_url("ext-app", "https://app.example/cb", p.challenge, p.state)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn(f"state={p.state}", url)
        t = Fake([{"status": 200, "body": json.dumps({"access_token": "a", "refresh_token": "r"})},
                  {"status": 200, "body": json.dumps({"access_token": "b"})}, {"status": 400, "body": "{}"}])
        self.assertEqual(Pkce.exchange("ext-app", "https://app.example/cb", "code1", p.verifier, "secret1", t)["access_token"], "a")
        self.assertIn("client_secret=secret1", t.calls[0][3])
        self.assertEqual(Pkce.refresh("ext-app", "r", "secret1", t)["access_token"], "b")
        with self.assertRaises(MediloopError):
            Pkce.refresh("ext-app", "r", "secret1", t)


if __name__ == "__main__":
    unittest.main()
