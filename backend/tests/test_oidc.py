from __future__ import annotations

import time
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from domaincheck.infrastructure.auth.oidc import OIDCError, OIDCState, TelegramOIDCClient


class MemoryStateStore:
    def __init__(self):
        self.records: dict[str, OIDCState] = {}

    async def put(self, state: OIDCState, ttl_seconds: int) -> None:
        self.records[state.state] = state

    async def consume(self, state: str) -> OIDCState | None:
        return self.records.pop(state, None)


class FakeHTTPClient:
    pass


class TestTelegramClient(TelegramOIDCClient):
    async def discovery(self) -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": "https://provider.test/authorize",
            "token_endpoint": "https://provider.test/token",
            "jwks_uri": "https://provider.test/jwks",
        }


class OIDCTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStateStore()
        self.client = TestTelegramClient(
            issuer="https://issuer.test",
            client_id="client-id",
            client_secret="fixture-secret",
            redirect_uri="https://app.test/api/v1/auth/telegram/callback",
            state_store=self.store,
        )

    async def test_authorization_request_has_pkce_nonce_state_and_safe_return_path(self):
        request = await self.client.create_authorization_request(return_path="//evil.test/path")
        query = parse_qs(urlparse(request.authorization_url).query)
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertIn("nonce", query)
        self.assertIn("state", query)
        stored = self.store.records[query["state"][0]]
        self.assertEqual(stored.return_path, "/")
        self.assertNotEqual(stored.browser_binding_hash, request.browser_binding)

    async def test_state_is_consumed_once_and_wrong_browser_is_rejected(self):
        request = await self.client.create_authorization_request()
        state = parse_qs(urlparse(request.authorization_url).query)["state"][0]
        with self.assertRaises(OIDCError):
            await self.client.exchange_code(code="fixture", state=state, browser_binding="wrong")
        self.assertIsNone(await self.store.consume(state))

    async def test_token_exchange_uses_http_basic_and_does_not_send_secret_in_body(self):
        observed = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                observed["authorization"] = request.headers.get("authorization", "")
                observed["body"] = request.content.decode()
                return httpx.Response(200, json={"id_token": "fixture-token"})
            if request.url.path == "/jwks":
                return httpx.Response(200, json={"keys": []})
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = TestTelegramClient(
                issuer="https://issuer.test", client_id="client-id",
                client_secret="fixture-secret",
                redirect_uri="https://app.test/api/v1/auth/telegram/callback",
                state_store=self.store, http_client=http_client,
            )
            authorization = await client.create_authorization_request()
            state = parse_qs(urlparse(authorization.authorization_url).query)["state"][0]
            with patch.object(client, "verify_id_token", return_value={"sub": "fixture", "iss": "https://issuer.test"}):
                await client.exchange_code(
                    code="fixture-code", state=state,
                    browser_binding=authorization.browser_binding,
                )
        self.assertTrue(str(observed["authorization"]).startswith("Basic "))
        self.assertNotIn("client_secret", observed["body"])
        self.assertIn("code_verifier=", observed["body"])

    def test_id_token_verifies_issuer_audience_expiry_and_nonce(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
        public_jwk["kid"] = "fixture-key"
        now = int(time.time())
        token = jwt.encode(
            {
                "iss": "https://issuer.test", "aud": "client-id", "sub": "telegram-subject",
                "iat": now, "exp": now + 60, "nonce": "fixture-nonce",
            },
            key,
            algorithm="RS256",
            headers={"kid": "fixture-key"},
        )
        claims = self.client.verify_id_token(token, {"keys": [public_jwk]}, expected_nonce="fixture-nonce")
        self.assertEqual(claims["sub"], "telegram-subject")
        with self.assertRaises(OIDCError):
            self.client.verify_id_token(token, {"keys": [public_jwk]}, expected_nonce="replayed-nonce")

