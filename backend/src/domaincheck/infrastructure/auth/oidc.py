from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

import httpx
import jwt
from redis.asyncio import Redis


class OIDCError(ValueError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class OIDCState:
    state: str
    nonce: str
    code_verifier: str
    browser_binding_hash: str
    return_path: str


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    authorization_url: str
    browser_binding: str


class OIDCStateStore(Protocol):
    async def put(self, state: OIDCState, ttl_seconds: int) -> None: ...
    async def consume(self, state: str) -> OIDCState | None: ...


class RedisOIDCStateStore:
    def __init__(self, redis: Redis, *, namespace: str = "domaincheck:v1:oidc"):
        self._redis = redis
        self._namespace = namespace

    def _key(self, state: str) -> str:
        return f"{self._namespace}:{hashlib.sha256(state.encode()).hexdigest()}"

    async def put(self, state: OIDCState, ttl_seconds: int) -> None:
        payload = json.dumps({
            "state": state.state,
            "nonce": state.nonce,
            "code_verifier": state.code_verifier,
            "browser_binding_hash": state.browser_binding_hash,
            "return_path": state.return_path,
        })
        stored = await self._redis.set(self._key(state.state), payload, ex=ttl_seconds, nx=True)
        if not stored:
            raise OIDCError("OIDC state collision")

    async def consume(self, state: str) -> OIDCState | None:
        raw = await self._redis.getdel(self._key(state))
        if raw is None:
            return None
        data = json.loads(raw)
        return OIDCState(**data)


def _base64url_sha256(value: str) -> str:
    digest = hashlib.sha256(value.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _safe_return_path(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


class TelegramOIDCClient:
    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        state_store: OIDCStateStore,
        state_ttl_seconds: int = 300,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self._client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._state_store = state_store
        self._state_ttl_seconds = state_ttl_seconds
        self._http_client = http_client

    async def _get(self, url: str) -> dict:
        try:
            if self._http_client is not None:
                response = await self._http_client.get(url, timeout=10.0)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=10.0)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError("OIDC provider request failed") from exc
        if not isinstance(data, dict):
            raise OIDCError("OIDC provider returned an invalid document")
        return data

    async def _post(self, url: str, data: dict[str, str], *, basic_auth: tuple[str, str]) -> dict:
        try:
            if self._http_client is not None:
                response = await self._http_client.post(
                    url, data=data, auth=basic_auth, timeout=10.0,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url, data=data, auth=basic_auth, timeout=10.0,
                    )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError("OIDC token exchange failed") from exc
        if not isinstance(payload, dict):
            raise OIDCError("OIDC token endpoint returned an invalid response")
        return payload

    async def discovery(self) -> dict:
        document = await self._get(f"{self.issuer}/.well-known/openid-configuration")
        if document.get("issuer") != self.issuer:
            raise OIDCError("OIDC discovery issuer mismatch")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(document.get(key), str) or not document[key].startswith("https://"):
                raise OIDCError(f"OIDC discovery is missing a secure {key}")
        return document

    async def create_authorization_request(self, *, return_path: str | None = None) -> AuthorizationRequest:
        discovery = await self.discovery()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        browser_binding = secrets.token_urlsafe(32)
        record = OIDCState(
            state=state,
            nonce=nonce,
            code_verifier=verifier,
            browser_binding_hash=hashlib.sha256(browser_binding.encode()).hexdigest(),
            return_path=_safe_return_path(return_path),
        )
        await self._state_store.put(record, self._state_ttl_seconds)
        query = urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": _base64url_sha256(verifier),
            "code_challenge_method": "S256",
        })
        return AuthorizationRequest(f"{discovery['authorization_endpoint']}?{query}", browser_binding)

    async def exchange_code(self, *, code: str, state: str, browser_binding: str) -> tuple[dict, str]:
        record = await self._state_store.consume(state)
        if record is None:
            raise OIDCError("OIDC state is expired or was already used")
        supplied_hash = hashlib.sha256(browser_binding.encode()).hexdigest()
        if not hmac.compare_digest(record.browser_binding_hash, supplied_hash):
            raise OIDCError("OIDC callback browser binding mismatch")
        discovery = await self.discovery()
        token_response = await self._post(discovery["token_endpoint"], {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": record.code_verifier,
        }, basic_auth=(self.client_id, self._client_secret))
        id_token = token_response.get("id_token")
        if not isinstance(id_token, str):
            raise OIDCError("OIDC response has no ID token")
        jwks = await self._get(discovery["jwks_uri"])
        claims = self.verify_id_token(id_token, jwks, expected_nonce=record.nonce)
        return claims, record.return_path

    def verify_id_token(self, token: str, jwks: dict, *, expected_nonce: str) -> dict:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm not in {"RS256", "ES256"}:
            raise OIDCError("OIDC token uses an unsupported algorithm")
        kid = header.get("kid")
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise OIDCError("OIDC JWKS is invalid")
        matching = [item for item in keys if isinstance(item, dict) and item.get("kid") == kid]
        if len(matching) != 1:
            raise OIDCError("OIDC signing key was not found")
        key = jwt.PyJWK.from_dict(matching[0], algorithm=algorithm).key
        claims = jwt.decode(
            token,
            key=key,
            algorithms=[algorithm],
            audience=self.client_id,
            issuer=self.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        )
        if not hmac.compare_digest(str(claims.get("nonce", "")), expected_nonce):
            raise OIDCError("OIDC nonce mismatch")
        return claims

