import unittest
from unittest.mock import Mock, patch

import requests

from link_status import (
    check_link,
    check_links,
    counts_as_reported_down,
    format_result_note,
    should_show_result,
)


class LinkStatusTests(unittest.TestCase):
    @patch("link_status.requests.get")
    def test_reports_301_and_302_redirect_chain(self, get):
        first = Mock(status_code=301, url="http://example.com")
        first.headers = {"Location": "https://example.com"}
        second = Mock(status_code=302, url="https://example.com")
        second.headers = {"Location": "https://www.example.com/home"}
        final = Mock(
            status_code=200,
            url="https://www.example.com/home",
            history=[first, second],
        )
        get.return_value = final

        result = check_link("http://example.com")

        self.assertEqual(result["status"], "LIVE")
        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["redirect_codes"], "301 → 302")
        self.assertEqual(result["redirect_count"], 2)
        self.assertTrue(should_show_result(result))
        self.assertEqual(
            format_result_note(result),
            (
                "REDIRECT | HTTP 301 → 302 → 200 | REDIRECT URL: "
                "https://example.com → https://www.example.com/home"
            ),
        )

    @patch("link_status.requests.get")
    def test_marks_http_error_as_die(self, get):
        get.return_value = Mock(
            status_code=404,
            url="https://example.com/missing",
            history=[],
        )

        result = check_link("https://example.com/missing")

        self.assertEqual(result["status"], "DIE")
        self.assertEqual(result["status_code"], 404)

    @patch("link_status.requests.get")
    def test_cloudflare_403_is_blocked_not_die(self, get):
        response = Mock(status_code=403, url="https://example.com", history=[])
        response.headers = {"Server": "cloudflare", "CF-Ray": "abc"}
        get.return_value = response

        result = check_link("https://example.com")

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["provider"], "Cloudflare")
        self.assertTrue(counts_as_reported_down(result))
        self.assertEqual(format_result_note(result), "BLOCKED Cloudflare | HTTP 403")

    @patch("link_status.requests.get")
    def test_cloudflare_phishing_warning_with_http_200_is_blocked(self, get):
        response = Mock(
            status_code=200,
            url="https://example.com",
            history=[],
            encoding="utf-8",
        )
        response.headers = {
            "Content-Type": "text/html",
            "Server": "cloudflare",
            "CF-Ray": "abc",
        }
        response.iter_content.return_value = [
            b"<h1>Suspected Phishing</h1>"
            b"<p>This website has been reported for potential phishing.</p>"
        ]
        get.return_value = response

        result = check_link("https://example.com")

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["provider"], "Cloudflare")
        self.assertTrue(should_show_result(result))
        self.assertIn("Suspected Phishing", result["reason"])

    @patch("link_status.requests.get")
    def test_server_error_is_temporary_not_die(self, get):
        response = Mock(status_code=503, url="https://example.com", history=[])
        response.headers = {}
        get.return_value = response

        result = check_link("https://example.com")

        self.assertEqual(result["status"], "TEMP ERROR")
        self.assertFalse(counts_as_reported_down(result))
        self.assertFalse(should_show_result(result))

    @patch("link_status.requests.get")
    def test_three_timeouts_are_shown_as_unreachable(self, get):
        get.side_effect = requests.Timeout("too slow")

        result = check_link("https://slow.example")

        self.assertEqual(get.call_count, 3)
        self.assertEqual(result["status"], "UNREACHABLE")
        self.assertTrue(should_show_result(result))
        self.assertEqual(
            format_result_note(result),
            "UNREACHABLE | TIMEOUT 3/3",
        )

    @patch("link_status.requests.get")
    def test_dns_failure_is_shown_as_unreachable(self, get):
        get.side_effect = requests.ConnectionError(
            "NameResolutionError: Failed to resolve example.invalid (getaddrinfo failed)"
        )

        result = check_link("https://example.invalid")

        self.assertEqual(get.call_count, 3)
        self.assertEqual(result["status"], "UNREACHABLE")
        self.assertEqual(
            format_result_note(result),
            "UNREACHABLE | DNS ERROR 3/3",
        )

    def test_die_note_includes_redirect_chain_and_final_code(self):
        result = {
            "status": "DIE",
            "provider": "",
            "status_code": 404,
            "redirect_chain": [
                {"status": 301, "url": "https://start.example"},
                {"status": 302, "url": "https://middle.example"},
            ],
            "final_url": "https://end.example/missing",
        }

        self.assertEqual(
            format_result_note(result),
            (
                "DIE | HTTP 301 → 302 → 404 | REDIRECT URL: "
                "https://middle.example → https://end.example/missing"
            ),
        )

    @patch("link_status.requests.get")
    def test_bare_domain_falls_back_from_https_to_http(self, get):
        get.side_effect = [
            requests.ConnectionError("https failed"),
            requests.ConnectionError("https failed"),
            requests.ConnectionError("https failed"),
            Mock(status_code=200, url="http://example.com", history=[]),
        ]

        result = check_link("example.com")

        self.assertEqual(result["status"], "LIVE")
        self.assertEqual(result["request_url"], "http://example.com")
        self.assertEqual(get.call_count, 4)

    @patch("link_status.check_link")
    def test_batch_keeps_input_order(self, check):
        check.side_effect = lambda target, _timeout: {
            "input": target,
            "status": "LIVE",
        }

        results = check_links(["one.example", "two.example"], max_workers=2)

        self.assertEqual([item["input"] for item in results], ["one.example", "two.example"])


if __name__ == "__main__":
    unittest.main()
