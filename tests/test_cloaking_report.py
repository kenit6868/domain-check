import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cloaking_report import (
    classify_cloaking,
    evidence_readiness,
    generate_cloaking_report,
    normalize_report_url,
)
from cloaking_probe import _fetch_variant, collect_cloaking_evidence
from cloaking_render_probe import rendered_content_differs
from cloaking_workflow import build_cloaking_email, resolve_provider, select_html_pair
import phishing_toolkit as pt


class CloakingReportTests(unittest.TestCase):
    def test_desktop_mobile_difference_is_strong_but_not_declared_confirmed(self):
        level, text = classify_cloaking({"desktop_benign_mobile_abuse": True})
        self.assertEqual(level, "strong")
        self.assertIn("xác minh độc lập", text)

    def test_game_only_is_insufficient(self):
        level, text = classify_cloaking({"game_only": True})
        self.assertEqual(level, "insufficient")
        self.assertIn("Chưa đủ bằng chứng", text)

    def test_report_contains_reproducible_observation_and_evidence(self):
        report = generate_cloaking_report(
            reported_url="example.com/path", brand="Example Brand",
            discovered_on=date(2026, 8, 29),
            signals={"direct_benign_google_abuse": True},
            evidence_names=["direct.png", "google.png"],
        )
        self.assertIn("https://example.com/path", report)
        self.assertIn("29/08/2026", report)
        self.assertIn("direct.png", report)
        self.assertIn("bấm từ Google", report)

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(ValueError):
            normalize_report_url("file:///etc/passwd")

    def test_evidence_gate_lists_every_missing_group(self):
        ready, missing = evidence_readiness({"pc_screenshot": True})
        self.assertFalse(ready)
        self.assertNotIn("Ảnh PC thấy rõ URL", missing)
        self.assertIn("Kết quả curl đối chiếu", missing)
        self.assertIn("HTML và chứng từ liên quan", missing)

    def test_evidence_gate_requires_all_groups(self):
        ready, missing = evidence_readiness({
            "pc_screenshot": True,
            "mobile_screenshot": True,
            "google_screenshot": True,
            "f12_details": True,
            "curl_details": True,
            "html_evidence": True,
            "strong_comparison": True,
            "reproduction_notes": True,
        })
        self.assertTrue(ready)
        self.assertEqual(missing, [])

    @patch("cloaking_probe._fetch_variant")
    def test_automatic_probe_detects_material_device_difference(self, fetch):
        def fake_fetch(url, *, user_agent, referrer=""):
            mobile = "Android" in user_agent
            size = 70_000 if mobile else 38_000
            return {
                "status": 200,
                "final_url": url,
                "redirect_chain": [{"url": url, "status": 200, "location": ""}],
                "headers": {"x-matched-path": "/mobile" if mobile else "/desktop"},
                "size": size,
                "sha256": "mobile" if mobile else "desktop",
                "html": "x",
                "truncated": False,
            }
        fetch.side_effect = fake_fetch
        result = collect_cloaking_evidence("https://example.com")
        self.assertEqual(result["successful_count"], 4)
        self.assertTrue(result["conditional_difference"])
        self.assertIn("desktop_direct", result["f12_summary"])

    @patch("cloaking_probe.validate_public_url", side_effect=lambda value: value)
    @patch("cloaking_probe.subprocess.run")
    def test_curl_probe_captures_headers_body_and_fingerprint(self, run, _validate):
        def fake_run(args, **kwargs):
            header_path = Path(args[args.index("--dump-header") + 1])
            body_path = Path(args[args.index("--output") + 1])
            header_path.write_bytes(
                b"HTTP/1.1 200 OK\r\nx-matched-path: /mirror-document/mobile\r\n"
                b"content-type: text/html\r\n\r\n"
            )
            body_path.write_text(
                '<script src="https://best-traffic.pages.dev/traffic_dr.js"></script>',
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="200", stderr="")
        run.side_effect = fake_run
        result = _fetch_variant(
            "https://example.com/vn/",
            user_agent="Mobile UA",
            referrer="https://www.google.com/",
        )
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["headers"]["x-matched-path"], "/mirror-document/mobile")
        self.assertTrue(result["traffic_fingerprint"])
        self.assertIn("--referer", result["commands"][0])

    @patch("cloaking_probe._fetch_variant")
    def test_identical_curl_profiles_block_report(self, fetch):
        fetch.return_value = {
            "status": 200, "final_url": "https://example.com/",
            "redirect_chain": [{"url": "https://example.com/", "status": 200, "location": ""}],
            "headers": {}, "size": 10_000, "sha256": "same", "html": "game",
            "truncated": False, "traffic_fingerprint": False, "commands": [],
        }
        result = collect_cloaking_evidence("https://example.com")
        self.assertFalse(result["reportable_cloaking"])
        self.assertFalse(result["conditional_difference"])

    def test_select_html_pair_prefers_desktop_direct_mobile_google(self):
        base = {"status": 200, "final_url": "https://example.com/", "headers": {}}
        probe = {"results": {
            "desktop_direct": {**base, "size": 20_000, "sha256": "decoy"},
            "mobile_direct": {**base, "size": 20_000, "sha256": "decoy"},
            "desktop_google": {**base, "size": 20_000, "sha256": "decoy"},
            "mobile_google": {**base, "size": 80_000, "sha256": "gambling"},
        }}
        self.assertEqual(select_html_pair(probe), ("desktop_direct", "mobile_google"))

    @patch.object(pt, "get_rdap_abuse_email", return_value={"registrar": "Namecheap, Inc.", "abuse_email": "abuse@namecheap.com"})
    @patch.object(pt, "get_whois_info", return_value={"registrar": "Namecheap, Inc.", "name_servers": []})
    def test_provider_webform_policy_overrides_email(self, _whois, _rdap):
        provider = resolve_provider("https://example.com/path")
        self.assertIsNone(provider["recipient"])
        self.assertIn("namecheap.com", provider["webform"])

    def test_generated_report_is_cautious_and_reproducible(self):
        probe = {"results": {
            "desktop_direct": {"status": 200, "size": 27_000, "sha256": "a" * 64},
            "mobile_google": {"status": 200, "size": 104_000, "sha256": "b" * 64},
        }}
        report = build_cloaking_email(
            reported_url="https://example.com/vi-vn/", keyword="98win",
            provider={"domain": "example.com", "registrar": "Example Registrar"},
            probe=probe, pair=("desktop_direct", "mobile_google"),
            desktop_image_name="pc.png", mobile_image_name="mobile.png",
        )
        self.assertIn("apparent cloaking", report["subject"])
        self.assertIn("does not assert credential theft", report["body"])
        self.assertIn("html_mobile_tu_google.html", report["body"])

    @patch.object(pt, "_imap_save_sent", return_value=None)
    @patch.object(pt.smtplib, "SMTP_SSL")
    def test_single_send_supports_evidence_attachments(self, smtp_ssl, _imap):
        server = MagicMock()
        smtp_ssl.return_value.__enter__.return_value = server
        result = pt.send_report_email_single_with_attachments(
            "abuse@example.com", "Subject", "Body",
            [{"filename": "evidence.html", "data": b"<html></html>", "mime_type": "text/html"}],
            {"username": "sender@example.com", "password": "secret", "host": "smtp.example.com", "port": 465},
        )
        self.assertTrue(result["success"])
        raw_message = server.sendmail.call_args.args[2]
        self.assertIn("evidence.html", raw_message)

    def test_rendered_dom_detects_decoy_vs_gambling_content(self):
        decoy = {
            "final_url": "https://example.com/vi-vn/",
            "html": "<html><body>Thiết kế xây dựng nhà trọn gói kiến tạo tổ ấm tư vấn công trình</body></html>",
        }
        gambling = {
            "final_url": "https://example.com/vi-vn/",
            "html": "<html><body>98WIN casino nổ hũ đăng ký đăng nhập nhận thưởng cá cược thể thao</body></html>",
        }
        self.assertTrue(rendered_content_differs(decoy, gambling))

    def test_rendered_dom_ignores_responsive_markup_only(self):
        first = {"final_url": "https://example.com/", "html": "<div class='desktop'>same useful text</div>"}
        second = {"final_url": "https://example.com/", "html": "<main class='mobile'><p>same useful text</p></main>"}
        self.assertFalse(rendered_content_differs(first, second))


if __name__ == "__main__":
    unittest.main()
