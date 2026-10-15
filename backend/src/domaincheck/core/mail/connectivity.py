from __future__ import annotations

import imaplib
import smtplib
import socket
import ssl
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal, Protocol

try:
    import socks
except ImportError:  # pragma: no cover - exercised only in a broken deployment
    socks = None


@dataclass(frozen=True, slots=True)
class ProxySpec:
    scheme: Literal["socks5", "socks4", "http"]
    host: str
    port: int
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class SmtpConnectionSpec:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    transport: Literal["implicit_tls", "starttls", "plain"] = "starttls"
    timeout_seconds: float = 30.0
    proxy: ProxySpec | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ImapConnectionSpec:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    status: Literal["success", "failed"]
    protocol: Literal["smtp", "imap"]
    transport: str
    code: str
    message: str
    elapsed_ms: int


class MailConnectionTester(Protocol):
    def test_smtp(self, spec: SmtpConnectionSpec) -> ConnectionTestResult: ...
    def test_imap(self, spec: ImapConnectionSpec) -> ConnectionTestResult: ...


class StandardMailConnectionTester:
    """Authenticate only; this class never sends messages or reads mailboxes."""

    def test_smtp(self, spec: SmtpConnectionSpec) -> ConnectionTestResult:
        started = monotonic()
        server = None
        try:
            context = ssl.create_default_context()
            server = self._open_smtp(spec, context)
            if spec.transport == "starttls":
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
            server.login(spec.username, spec.password)
            return self._success("smtp", spec.transport, started)
        except Exception as exc:
            return self._failure("smtp", spec.transport, started, exc)
        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception:
                    try:
                        server.close()
                    except Exception:
                        pass

    def test_imap(self, spec: ImapConnectionSpec) -> ConnectionTestResult:
        started = monotonic()
        server = None
        try:
            server = imaplib.IMAP4_SSL(
                spec.host,
                spec.port,
                ssl_context=ssl.create_default_context(),
                timeout=spec.timeout_seconds,
            )
            server.login(spec.username, spec.password)
            return self._success("imap", "implicit_tls", started)
        except Exception as exc:
            return self._failure("imap", "implicit_tls", started, exc)
        finally:
            if server is not None:
                try:
                    server.logout()
                except Exception:
                    try:
                        server.shutdown()
                    except Exception:
                        pass

    @staticmethod
    def _open_smtp(spec: SmtpConnectionSpec, context: ssl.SSLContext):
        implicit = spec.transport == "implicit_tls"
        if spec.proxy is not None:
            if socks is None:
                raise RuntimeError("proxy_support_unavailable")
            smtp_class = _ProxySMTPSSL if implicit else _ProxySMTP
            kwargs = {"context": context} if implicit else {}
            return smtp_class(
                spec.host, spec.port, spec.proxy, timeout=spec.timeout_seconds, **kwargs
            )
        if implicit:
            return smtplib.SMTP_SSL(
                spec.host, spec.port, timeout=spec.timeout_seconds, context=context
            )
        return smtplib.SMTP(spec.host, spec.port, timeout=spec.timeout_seconds)

    @staticmethod
    def _success(protocol: Literal["smtp", "imap"], transport: str, started: float) -> ConnectionTestResult:
        return ConnectionTestResult(
            status="success", protocol=protocol, transport=transport,
            code="authenticated", message="Connection and authentication succeeded",
            elapsed_ms=max(0, round((monotonic() - started) * 1000)),
        )

    @staticmethod
    def _failure(
        protocol: Literal["smtp", "imap"], transport: str, started: float, exc: Exception,
    ) -> ConnectionTestResult:
        if isinstance(exc, (smtplib.SMTPAuthenticationError, imaplib.IMAP4.error)):
            code, message = "authentication_failed", "Authentication was rejected"
        elif isinstance(exc, ssl.SSLError):
            code, message = "tls_failed", "TLS negotiation or certificate verification failed"
        elif isinstance(exc, (TimeoutError, socket.timeout)):
            code, message = "timeout", "Connection timed out"
        elif isinstance(exc, socket.gaierror):
            code, message = "dns_failed", "Mail server name could not be resolved"
        elif isinstance(exc, (ConnectionError, OSError)):
            code, message = "connection_failed", "Mail server could not be reached"
        elif isinstance(exc, RuntimeError) and exc.args == ("proxy_support_unavailable",):
            code, message = "proxy_unavailable", "Configured proxy support is unavailable"
        else:
            code, message = "protocol_failed", "Mail server rejected the connection test"
        return ConnectionTestResult(
            status="failed", protocol=protocol, transport=transport, code=code, message=message,
            elapsed_ms=max(0, round((monotonic() - started) * 1000)),
        )


def _proxy_type(scheme: str) -> int:
    assert socks is not None
    return {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}[scheme]


class _ProxyMixin:
    _proxy: ProxySpec

    def _proxy_socket(self, host: str, port: int, timeout: float | None):
        assert socks is not None
        connection = socks.socksocket()
        connection.set_proxy(
            _proxy_type(self._proxy.scheme), self._proxy.host, self._proxy.port,
            username=self._proxy.username, password=self._proxy.password,
        )
        connection.settimeout(timeout or socket.getdefaulttimeout())
        connection.connect((host, port))
        return connection


class _ProxySMTP(_ProxyMixin, smtplib.SMTP):
    def __init__(self, host: str, port: int, proxy: ProxySpec, **kwargs):
        self._proxy = proxy
        super().__init__(host, port, **kwargs)

    def _get_socket(self, host, port, timeout):
        return self._proxy_socket(host, port, timeout)


class _ProxySMTPSSL(_ProxyMixin, smtplib.SMTP_SSL):
    def __init__(self, host: str, port: int, proxy: ProxySpec, **kwargs):
        self._proxy = proxy
        super().__init__(host, port, **kwargs)

    def _get_socket(self, host, port, timeout):
        connection = self._proxy_socket(host, port, timeout)
        return self.context.wrap_socket(connection, server_hostname=host)
