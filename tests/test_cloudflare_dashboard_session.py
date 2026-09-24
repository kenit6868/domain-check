import unittest

import cloudflare_dashboard_session as dashboard


class FakeResponse:
    def __init__(self, status_code=200, data=None, reason=""):
        self.status_code = status_code
        self._data = data or {}
        self.reason = reason

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


class CloudflareDashboardSessionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "cloudflare_account_id": "0123456789abcdef0123456789abcdef",
            "contact_email": "reporter@example.test",
            "contact_name": "Reporter",
            "brand_name": "Example Brand",
        }

    def test_dashboard_payload_matches_observed_shape(self):
        payload = dashboard.build_dashboard_payload(
            "https://example.test/login", "Detailed phishing evidence for review.", self.cfg
        )
        self.assertEqual(payload["act"], "abuse_phishing")
        self.assertEqual(payload["urls"], "https://example.test/login")
        self.assertEqual(payload["host_notification"], "send-anon")
        self.assertEqual(payload["owner_notification"], "send-anon")
        self.assertEqual(payload["agree"], 0)

    def test_submit_uses_verified_dashboard_headers_and_response_shape(self):
        session = FakeSession(FakeResponse(data={
            "request": {"act": "abuse_phishing"},
            "result": "success", "abuse_rand": "report-1",
        }))
        result = dashboard.submit_dashboard_report(
            "https://example.test/login", "Detailed phishing evidence for review.",
            self.cfg, "cf_clearance=manual-cookie-secret; session=value", session=session,
        )
        url, kwargs = session.calls[0]
        self.assertTrue(url.startswith("https://dash.cloudflare.com/api/v4/accounts/"))
        self.assertEqual(
            kwargs["headers"]["Cookie"], "cf_clearance=manual-cookie-secret; session=value"
        )
        self.assertNotIn("manual-cookie-secret", str(kwargs["json"]))
        self.assertNotIn("x-atok", kwargs["headers"])
        self.assertEqual(kwargs["headers"]["Origin"], "https://dash.cloudflare.com")
        self.assertEqual(kwargs["headers"]["x-cross-site-security"], "dash")
        self.assertNotIn("manual-cookie-secret", str(result))
        self.assertEqual(result["report_id"], "report-1")

    def test_authentication_error_is_not_success(self):
        session = FakeSession(FakeResponse(data={
            "success": False,
            "errors": [{"code": 10000, "message": "Authentication error"}],
        }))
        with self.assertRaises(dashboard.CloudflareDashboardSessionError):
            dashboard.submit_dashboard_report(
                "https://example.test/login", "Detailed phishing evidence for review.",
                self.cfg, "expired-cookie", session=session,
            )

    def test_dedupe_error_exposes_server_message(self):
        message = "You have already submitted this URL recently: https://example.test/login"
        session = FakeSession(FakeResponse(data={
            "request": {"act": "abuse_phishing"},
            "result": "error", "msg": message,
            "err_code": "dedupe", "error_code": "dedupe",
        }))
        with self.assertRaises(dashboard.CloudflareDashboardDuplicateError) as caught:
            dashboard.submit_dashboard_report(
                "https://example.test/login", "Detailed phishing evidence for review.",
                self.cfg, "valid-cookie", session=session,
            )
        self.assertEqual(str(caught.exception), message)

    def test_lost_response_is_unknown(self):
        session = FakeSession(error=TimeoutError("secret must not escape"))
        with self.assertRaises(dashboard.CloudflareDashboardUnknownError) as caught:
            dashboard.submit_dashboard_report(
                "https://example.test/login", "Detailed phishing evidence for review.",
                self.cfg, "manual-cookie-secret", session=session,
            )
        self.assertNotIn("manual-cookie-secret", str(caught.exception))
        self.assertNotIn("secret must not escape", str(caught.exception))

    def test_cookie_prefix_is_accepted_and_header_injection_is_rejected(self):
        headers = dashboard._headers("Cookie: a=1; b=2")
        self.assertEqual(headers["Cookie"], "a=1; b=2")
        self.assertNotIn("x-atok", headers)
        with self.assertRaises(dashboard.CloudflareDashboardSessionError):
            dashboard._headers("a=1\r\nX-Injected: yes")


if __name__ == "__main__":
    unittest.main()
