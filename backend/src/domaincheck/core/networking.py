"""Fail-closed URL checks for untrusted browser and HTTP investigation traffic."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

import dns.exception
import dns.resolver
import requests


class EgressDenied(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EgressProxy:
    server: str
    username: str | None = None
    password: str | None = None

    @classmethod
    def parse(cls, value: str) -> "EgressProxy":
        try:
            parsed = urlsplit(value.strip())
            host, port = parsed.hostname, parsed.port
        except ValueError as exc:
            raise EgressDenied("egress_proxy_invalid") from exc
        if parsed.scheme not in {"http", "https"} or not host or not port:
            raise EgressDenied("egress_proxy_invalid")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise EgressDenied("egress_proxy_invalid")
        server_host = f"[{host}]" if ":" in host else host
        return cls(f"{parsed.scheme}://{server_host}:{port}",
                   unquote(parsed.username) if parsed.username else None,
                   unquote(parsed.password) if parsed.password else None)

    def playwright(self) -> dict:
        result = {"server": self.server, "bypass": ""}
        if self.username:
            result["username"] = self.username
            result["password"] = self.password or ""
        return result

    def requests_url(self) -> str:
        if not self.username:
            return self.server
        from urllib.parse import quote
        parsed = urlsplit(self.server)
        password = f":{quote(self.password or '', safe='')}" if self.password is not None else ""
        return f"{parsed.scheme}://{quote(self.username, safe='')}{password}@{parsed.netloc}"


def validate_public_host(value: str, *, resolver=None) -> None:
    host = value.rstrip(".").lower()
    resolve = resolver or dns.resolver.resolve
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.endswith((".local", ".localhost", ".internal")):
            raise EgressDenied("egress_host_denied") from None
    else:
        if not literal.is_global:
            raise EgressDenied("egress_host_denied")
        return
    addresses = []
    for record_type in ("A", "AAAA"):
        try:
            addresses.extend(str(item) for item in resolve(host, record_type, lifetime=3.0))
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            continue
        except (dns.exception.DNSException, OSError) as exc:
            raise EgressDenied("egress_dns_failed") from exc
    if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
        raise EgressDenied("egress_host_denied")


class PublicEgressPolicy:
    """Validate observed URLs, with optional operator-managed egress proxy."""
    def __init__(self, proxy: EgressProxy | None = None, *, resolver=None):
        self.proxy = proxy
        self._resolve = resolver or dns.resolver.resolve

    def validate_url(self, value: str) -> None:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise EgressDenied("egress_url_invalid") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise EgressDenied("egress_url_invalid")
        # A fragment selects a browser route; it does not change the network host.
        if parsed.username is not None or parsed.password is not None:
            raise EgressDenied("egress_url_invalid")
        if port not in (None, 80, 443):
            raise EgressDenied("egress_port_denied")
        self.validate_host(parsed.hostname)

    def validate_host(self, value: str) -> None:
        validate_public_host(value, resolver=self._resolve)

    def browser_request_allowed(self, value: str) -> bool:
        # Browser-generated data/blob/about resources make no outbound request.
        if urlsplit(value).scheme in {"data", "blob", "about"}:
            return True
        self.validate_url(value)
        return True

    def probe_redirects(self, target: str, *, user_agent: str, timeout: float = 15.0) -> dict:
        """Probe redirects one hop at a time, validating each destination."""
        current = target
        hops = []
        proxy_url = self.proxy.requests_url() if self.proxy else None
        for _ in range(10):
            self.validate_url(current)
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(current, allow_redirects=False, timeout=timeout,
                    headers={"User-Agent": user_agent}, verify=False, stream=True,
                    **({"proxies": {"http": proxy_url, "https": proxy_url}} if proxy_url else {}))
            try:
                location = response.headers.get("Location", "")
                if response.status_code in {301, 302, 303, 307, 308} and location:
                    destination = urljoin(current, location)
                    self.validate_url(destination)
                    hops.append({"status": int(response.status_code), "url": current,
                                 "location": destination})
                    current = destination
                    continue
                return {"success": True, "requested_url": target, "final_url": current,
                        "final_status": int(response.status_code), "redirect_chain": hops,
                        "error": ""}
            finally:
                response.close()
        raise EgressDenied("egress_redirect_limit")
