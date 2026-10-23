from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from domaincheck.core.quick_report_routes import report_forms
from domaincheck.core.quick_report import QUICK_REPORT_ENGINE_VERSION
from domaincheck.engine import phishing_toolkit as toolkit


def private_record(domain="fixture.radio.am", registrar="NICENIC INTERNATIONAL GROUP CO., LIMITED"):
    return f"""Domain Name: {domain.upper()}
Registrar: {registrar}
Registrant Email: owner@fixture.example
Admin Email: hostmaster@radio.am
Registrar Abuse Contact Email: abuse@nicenic.net
Name Server: ns1.fixture.example
Domain Status: clientTransferProhibited
"""


class RegistrarContactTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("No live requests")))
        stack.enter_context(patch("socket.create_connection", side_effect=AssertionError("No live WHOIS")))

    def test_private_suffix_queries_each_registration_not_parent(self):
        with patch.object(toolkit, "query_whois_server", return_value=private_record()) as query, \
                patch.object(toolkit.whois, "whois", side_effect=AssertionError("No public .am lookup")):
            result = toolkit.get_whois_info("login.fixture.radio.am")
        query.assert_called_once_with("whois.centralnic.com", "fixture.radio.am")
        self.assertEqual(result["registrar"], "NICENIC INTERNATIONAL GROUP CO., LIMITED")
        self.assertEqual(result["emails"], ["abuse@nicenic.net"])
        self.assertNotIn("hostmaster@radio.am", str(result))
        self.assertNotIn("raw", result)

    def test_private_suffix_has_no_hardcoded_registrar(self):
        raw = private_record("second.radio.am", "Sav.com, LLC")
        with patch.object(toolkit, "query_whois_server", return_value=raw):
            result = toolkit.get_whois_info("second.radio.am")
        self.assertEqual(result["registrar"], "Sav.com, LLC")
        forms = report_forms("second.radio.am", result)
        self.assertEqual([row["form_url"] for row in forms if row["channel"] == "registrar"],
                         ["https://abuse.sav.com"])

    def test_private_fm_uses_same_exact_name_lookup(self):
        with patch.object(toolkit, "query_whois_server", return_value=private_record("fixture.radio.fm")) as query:
            toolkit.get_whois_info("fixture.radio.fm")
        query.assert_called_once_with("whois.centralnic.com", "fixture.radio.fm")

    def test_parent_unrelated_missing_and_ambiguous_records_fail_closed(self):
        records = ["", private_record("radio.am"), private_record("other.radio.am"),
                   private_record() + "Domain Name: radio.am\n"]
        for raw in records:
            with self.subTest(raw=raw), patch.object(toolkit, "query_whois_server", return_value=raw):
                result = toolkit.get_whois_info("fixture.radio.am")
                self.assertIn("error", result)
                self.assertNotIn("registrar", result)
                self.assertNotIn("emails", result)

    def test_public_whois_keeps_only_explicit_abuse_field(self):
        raw = "Registrant Email: owner@fixture.example\nRegistrar Abuse Contact Email: abuse@registrar.example\n"
        with patch.object(toolkit.whois, "whois", return_value={"registrar": "Fixture Registrar",
                "raw": raw, "emails": ["owner@fixture.example", "abuse@registrar.example"]}) as query:
            result = toolkit.get_whois_info("fixture.com")
        query.assert_called_once_with("fixture.com", inc_raw=True, timeout=10, quiet=True)
        self.assertEqual(result["emails"], ["abuse@registrar.example"])
        self.assertEqual(result["abuse_email"], "abuse@registrar.example")
        self.assertNotIn("owner@fixture.example", str(result))

    def test_unlabelled_whois_emails_are_not_contacts_in_any_lookup_consumer(self):
        with patch.object(toolkit.whois, "whois", return_value={"registrar": "Fixture Registrar",
                "raw": "Admin Email: hostmaster@radio.am\n", "emails": ["hostmaster@radio.am"]}):
            result = toolkit.get_whois_info("fixture.com")
        self.assertEqual(result["emails"], [])
        self.assertIsNone(result["abuse_email"])
        self.assertEqual(toolkit.resolve_worker_report_recipients(result, {}, {}, {}), [])
        self.assertEqual(toolkit.resolve_quick_report_recipients("fixture.com", who=result), [])

    def test_abuse_field_filters_invalid_privacy_blocked_and_duplicate_contacts(self):
        contacts = "invalid, a@withheldforprivacy.com, abuse@cloudflare.com, ABUSE@registrar.example, abuse@registrar.example"
        result = toolkit.resolve_quick_report_recipients("fixture.com", who={"abuse_email": contacts})
        self.assertEqual([row["email"] for row in result], ["ABUSE@registrar.example"])

    def test_generic_whois_contact_cannot_override_rdap(self):
        result = toolkit.resolve_quick_report_recipients("fixture.com",
            who={"emails": ["owner@fixture.example"]},
            rdap={"registrar": "Fixture Registrar", "abuse_email": "abuse@registrar.example"})
        self.assertEqual([row["email"] for row in result], ["abuse@registrar.example"])

    def pipeline(self, who, rdap):
        with ExitStack() as stack:
            stack.enter_context(patch.object(toolkit, "get_whois_info", return_value=who))
            stack.enter_context(patch.object(toolkit, "detect_cdn", return_value=[]))
            stack.enter_context(patch.object(toolkit, "run_cloaking_check", return_value={"verdict": "NO_SIGNAL"}))
            lookup = stack.enter_context(patch.object(toolkit, "get_rdap_abuse_email", return_value=rdap))
            result = toolkit.run_cdn_check("https://fixture.com/login", {})
            return result, lookup.call_count

    def test_rdap_fallback_restores_registrar_and_its_form(self):
        result, calls = self.pipeline({"emails": ["hostmaster@radio.am"]},
                                      {"registrar": "Sav.com, LLC", "abuse_email": "abuse@registrar.example"})
        self.assertEqual(calls, 1)
        self.assertEqual(result["registrar"], "Sav.com, LLC")
        self.assertEqual([row["form_url"] for row in report_forms("fixture.com", result)
                          if row["channel"] == "registrar"], ["https://abuse.sav.com"])
        self.assertFalse(any(row["channel"] == "registrar" for row in result["report_recipients"]))

    def test_rdap_is_consulted_before_static_email_fallback(self):
        result, calls = self.pipeline({"registrar": "Namecheap", "emails": ["owner@fixture.example"]},
                                      {"abuse_email": "abuse@registrar.example"})
        self.assertEqual(calls, 1)
        self.assertIn("abuse@registrar.example", [row["email"] for row in result["report_recipients"]])
        result, calls = self.pipeline({"registrar": "Namecheap"}, {"error": "fixture timeout"})
        self.assertEqual(calls, 1)
        self.assertIn("abuse@namecheap.com", [row["email"] for row in result["report_recipients"]])

    def test_known_form_and_explicit_contact_avoid_unnecessary_rdap(self):
        for who in ({"registrar": "Sav.com, LLC"},
                    {"registrar": "Fixture Registrar", "abuse_email": "abuse@registrar.example"}):
            with self.subTest(who=who):
                _, calls = self.pipeline(who, {})
                self.assertEqual(calls, 0)

    def test_private_names_do_not_use_public_parent_rdap(self):
        with patch.object(toolkit, "_load_rdap_bootstrap", side_effect=AssertionError("Wrong registry")):
            self.assertIn("error", toolkit.get_rdap_abuse_email("fixture.radio.am"))

    def test_new_cache_namespace_does_not_reuse_old_contact_policy(self):
        self.assertNotEqual(QUICK_REPORT_ENGINE_VERSION, "legacy-run-cdn-check-v1")

    def test_raw_whois_query_bounds_reads_and_rejects_injection(self):
        sock = MagicMock()
        sock.recv.side_effect = [b"Domain Name: FIXTURE.RADIO.AM\r\n", b""]
        connection = MagicMock()
        connection.__enter__.return_value = sock
        with patch.object(toolkit.socket, "create_connection", return_value=connection) as connect:
            self.assertIn("FIXTURE.RADIO.AM", toolkit.query_whois_server("whois.centralnic.com", "fixture.radio.am"))
            self.assertEqual(toolkit.query_whois_server("whois.centralnic.com", "fixture\r\nother"), "")
        self.assertEqual(connect.call_count, 1)
        self.assertTrue(sock.settimeout.called)
        sock.sendall.assert_called_once_with(b"fixture.radio.am\r\n")

    def test_raw_whois_response_size_and_total_time_are_bounded(self):
        sock = MagicMock()
        sock.recv.return_value = b"x" * 4096
        connection = MagicMock()
        connection.__enter__.return_value = sock
        with patch.object(toolkit.socket, "create_connection", return_value=connection):
            self.assertEqual(toolkit.query_whois_server("whois.centralnic.com", "fixture.radio.am"), "")
        self.assertEqual(sock.recv.call_count, 257)
        with patch.object(toolkit.socket, "create_connection", return_value=connection), \
                patch.object(toolkit.time, "monotonic", side_effect=[0, 9]):
            self.assertEqual(toolkit.query_whois_server("whois.centralnic.com", "fixture.radio.am"), "")


if __name__ == "__main__":
    unittest.main()
