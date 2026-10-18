from __future__ import annotations

import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from domaincheck.engine import browser_evidence
from fastapi import HTTPException
from uuid import uuid4
from domaincheck.core.networking import EgressDenied, EgressProxy, PublicEgressPolicy
from domaincheck.core.mail import AllowlistedMailConnectionTester, SmtpConnectionSpec, ImapConnectionSpec


class EgressPolicyTests(unittest.TestCase):
    def setUp(self):
        self.proxy = EgressProxy.parse("http://user:pass@proxy.example:3128")

    def test_proxy_url_keeps_credentials_out_of_server_metadata(self):
        self.assertEqual(self.proxy.server, "http://proxy.example:3128")
        self.assertEqual(self.proxy.playwright()["username"], "user")
        self.assertIn("user:pass@", self.proxy.requests_url())
        for value in ("http://proxy.example", "socks5://proxy.example:1080",
                      "http://proxy.example:3128/path"):
            with self.subTest(value=value), self.assertRaises(EgressDenied):
                EgressProxy.parse(value)

    def test_private_ipv4_ipv6_and_non_http_are_denied(self):
        policy = PublicEgressPolicy(self.proxy, resolver=lambda *_a, **_k: ["93.184.216.34"])
        for value in ("http://127.0.0.1/", "http://10.0.0.1/", "http://169.254.169.254/",
                      "http://[::1]/", "http://[fc00::1]/", "file:///etc/passwd",
                      "http://example.com:8080/", "http://user:pass@example.com/"):
            with self.subTest(value=value), self.assertRaises(EgressDenied):
                policy.validate_url(value)

    def test_rebinding_is_rechecked_on_every_request(self):
        responses = iter((["93.184.216.34"], [], ["127.0.0.1"], []))
        policy = PublicEgressPolicy(self.proxy, resolver=lambda *_a, **_k: next(responses))
        policy.validate_url("https://example.com/")
        with self.assertRaises(EgressDenied):
            policy.validate_url("https://example.com/asset")

    def test_redirect_to_private_address_is_blocked_before_second_request(self):
        policy = PublicEgressPolicy(self.proxy, resolver=lambda *_a, **_k: ["93.184.216.34"])
        response = SimpleNamespace(status_code=302, headers={"Location": "http://127.0.0.1/private"},
                                   close=lambda: None)
        with patch("domaincheck.core.networking.requests.Session") as session:
            session.return_value.__enter__.return_value.get.return_value = response
            with self.assertRaises(EgressDenied):
                policy.probe_redirects("https://example.com/", user_agent="fixture")
        get = session.return_value.__enter__.return_value.get
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(get.call_args.kwargs["proxies"]["https"], self.proxy.requests_url())

    def test_direct_probe_does_not_require_proxy_and_ignores_environment_proxy(self):
        policy = PublicEgressPolicy(resolver=lambda *_a, **_k: ["93.184.216.34"])
        response = SimpleNamespace(status_code=200, headers={}, close=lambda: None)
        with patch("domaincheck.core.networking.requests.Session") as session:
            session.return_value.__enter__.return_value.get.return_value = response
            result = policy.probe_redirects("https://example.com/", user_agent="fixture")
        client = session.return_value.__enter__.return_value
        self.assertFalse(client.trust_env)
        self.assertNotIn("proxies", client.get.call_args.kwargs)
        self.assertEqual(result["final_url"], "https://example.com/")

    def test_browser_route_aborts_private_subresource(self):
        calls = []
        route = SimpleNamespace(request=SimpleNamespace(url="http://127.0.0.1/private"),
                                abort=lambda: calls.append("abort"),
                                continue_=lambda: calls.append("continue"))
        browser_evidence._guard_browser_route(route, lambda url: (_ for _ in ()).throw(EgressDenied(url)))
        self.assertEqual(calls, ["abort"])

    def test_mail_test_requires_exact_host_port_allowlist(self):
        calls = []
        delegate = SimpleNamespace(test_smtp=lambda _spec: calls.append("smtp") or "connected",
                                   test_imap=lambda _spec: calls.append("imap") or "connected")
        tester = AllowlistedMailConnectionTester(delegate, ("mail.example.com:465",))
        denied = tester.test_smtp(SmtpConnectionSpec("mail.example.com", 587, "u", "p"))
        self.assertEqual(denied.code, "egress_denied")
        self.assertEqual(calls, [])
        with patch("domaincheck.core.mail.connectivity.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("93.184.216.34", 465))]):
            self.assertEqual(tester.test_smtp(SmtpConnectionSpec("mail.example.com", 465, "u", "p")),
                             "connected")
        denied_imap = tester.test_imap(ImapConnectionSpec("mail.example.com", 993, "u", "p"))
        self.assertEqual(denied_imap.code, "egress_denied")
        self.assertEqual(calls, ["smtp"])

    def test_mail_system_dns_checks_all_addresses_and_reports_dns_separately(self):
        import socket
        from unittest.mock import Mock
        delegate = Mock()
        tester = AllowlistedMailConnectionTester(delegate, ("mail.example.com:465", "mail.example.com:993"))
        smtp = SmtpConnectionSpec("mail.example.com", 465, "u", "p")
        imap = ImapConnectionSpec("mail.example.com", 993, "u", "p")
        for addresses in (["127.0.0.1"], ["93.184.216.34", "::1"], ["10.0.0.1"]):
            with self.subTest(addresses=addresses), patch(
                "domaincheck.core.mail.connectivity.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", (ip, 465)) for ip in addresses]):
                self.assertEqual(tester.test_smtp(smtp).code, "egress_denied")
                self.assertEqual(tester.test_imap(imap).code, "egress_denied")
        with patch("domaincheck.core.mail.connectivity.socket.getaddrinfo", side_effect=socket.gaierror):
            self.assertEqual(tester.test_smtp(smtp).code, "dns_failed")
            self.assertEqual(tester.test_imap(imap).code, "dns_failed")
        delegate.test_smtp.assert_not_called()
        delegate.test_imap.assert_not_called()

    def test_mail_api_is_disabled_without_operator_egress_configuration(self):
        from domaincheck.api.mail_settings import test_smtp_connection
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            settings=SimpleNamespace(mail_connectivity_enabled=False))))
        with self.assertRaises(HTTPException) as denied:
            asyncio.run(test_smtp_connection(uuid4(), request, SimpleNamespace(), None))
        self.assertEqual(denied.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
