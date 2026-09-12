import unittest

import cloudflare_abuse_api as api


class FakeResponse:
    def __init__(self, status_code=200, data=None, reason=""):
        self.status_code = status_code
        self._data = data or {}
        self.reason = reason

    def json(self):
        return self._data


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/user/tokens/verify"):
            return FakeResponse(data={"success": True, "result": {"status": "active"}})
        return FakeResponse(data={
            "success": True,
            "result": {"reports": None},
            "result_info": {"total_count": 0},
        })

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse(data={
            "success": True,
            "abuse_rand": "report-123",
            "result": "success",
        })


class CloudflareAbuseApiTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "cloudflare_api_token": "top-secret-token",
            "cloudflare_account_id": "0123456789abcdef0123456789abcdef",
            "contact_email": "reporter@example.test",
            "contact_name": "Reporter",
            "brand_name": "Example Brand",
        }

    def test_verify_access_checks_token_and_entitlement(self):
        session = FakeSession()
        result = api.verify_access(self.cfg, session=session)
        self.assertTrue(result["ok"])
        self.assertEqual(result["report_count"], 0)
        self.assertEqual(len(session.calls), 2)
        self.assertNotIn("top-secret-token", str(result))

    def test_payload_uses_phishing_schema_and_matching_email(self):
        payload = api.build_phishing_payload(
            "https://example.test/login",
            "Detailed observed phishing behavior for review.",
            self.cfg,
        )
        self.assertEqual(payload["act"], "abuse_phishing")
        self.assertEqual(payload["host_notification"], "send")
        self.assertEqual(payload["owner_notification"], "send")
        self.assertEqual(payload["email"], payload["email2"])
        self.assertEqual(payload["urls"], "https://example.test/login")
        self.assertEqual(payload["original_work"], "Example Brand")

    def test_submit_uses_phishing_endpoint_and_returns_report_id(self):
        session = FakeSession()
        result = api.submit_phishing_report(
            "https://example.test/login",
            "Detailed observed phishing behavior for review.",
            self.cfg,
            session=session,
        )
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/abuse-reports/abuse_phishing"))
        self.assertEqual(kwargs["json"]["act"], "abuse_phishing")
        self.assertEqual(result["report_id"], "report-123")

    def test_api_error_is_sanitized_and_does_not_include_token(self):
        class FailedSession:
            def get(self, *_args, **_kwargs):
                raise RuntimeError("connection failed")

        with self.assertRaises(api.CloudflareAbuseApiError) as caught:
            api.verify_access(self.cfg, session=FailedSession())
        self.assertNotIn("top-secret-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
