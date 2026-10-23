import asyncio
import json
import io
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import dns.resolver
import requests
from httpx import ASGITransport, AsyncClient

from domaincheck.application.link_status import LinkStatusService
from domaincheck.core import link_status as core
from domaincheck.core.networking import EgressDenied


class LinkStatusCoreTests(unittest.TestCase):
    def test_module_entrypoint_runs_nested_whois_with_offline_network_fixtures(self):
        # A real -m process (and its real WHOIS child) is necessary here:
        # imported-function mocks cannot expose __name__ becoming __main__.
        fixture = '''
import os, socket, requests, whois
from domaincheck.engine import phishing_toolkit
domain = os.environ['DOMAINCHECK_LINK_FIXTURE_DOMAIN']
status = os.environ['DOMAINCHECK_LINK_FIXTURE_STATUS']
def no_network(*args, **kwargs):
    raise AssertionError('Unexpected live network')
def timeout(*args, **kwargs):
    raise requests.ReadTimeout('Fixture HTTP timeout')
socket.create_connection = no_network
socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
requests.get = timeout
whois.whois = lambda *a, **k: {'status': [status]}
phishing_toolkit.query_whois_server = lambda *a, **k: f'Domain Name: {domain.upper()}\\nDomain Status: {status}\\n'
'''
        cases = [('fixture.com', 'clientHold', 'HOLD'),
                 ('fixture.radio.am', 'serverHold', 'HOLD'),
                 ('fixture.africa.com', 'clientTransferProhibited', 'REGISTERED'),
                 ('fixture.com', 'ok', 'REGISTERED')]
        with tempfile.TemporaryDirectory(prefix='link-status-entrypoint-') as cwd:
            Path(cwd, 'sitecustomize.py').write_text(fixture, encoding='utf-8')
            for domain, status, expected in cases:
                env = {**os.environ, 'PYTHONPATH': cwd,
                       'DOMAINCHECK_LINK_FIXTURE_DOMAIN': domain,
                       'DOMAINCHECK_LINK_FIXTURE_STATUS': status}
                url = f'https://{domain}/path?q=1'
                with self.subTest(domain=domain, status=status):
                    completed = subprocess.run([sys.executable, '-m', 'domaincheck.core.link_status'],
                        input=json.dumps({'url': url, 'endpoints': {}}), env=env, cwd=cwd,
                        capture_output=True, text=True, encoding='utf-8', timeout=15)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    result = json.loads(completed.stdout)
                    self.assertEqual(result['domain'], domain)
                    self.assertEqual(result['registration']['state'], expected)
                    self.assertEqual(result['registration']['source'], 'WHOIS')
                    self.assertEqual(result['registration']['statuses'], [status])
                    self.assertEqual(result['registration']['hold_flags'],
                                     [status.lower()] if expected == 'HOLD' else [])
                    self.assertEqual(result['dns']['state'], 'RESOLVES')
                    self.assertEqual(result['website_status'], 'TIMEOUT')
                    self.assertEqual(result['final_url'], url)
                    self.assertEqual(result['status'], 'DOMAIN_HOLD' if expected == 'HOLD' else 'TIMEOUT')

    def test_system_dns_works_when_direct_dns_is_unavailable(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        with patch.object(core.socket, 'getaddrinfo', return_value=addresses), patch.object(core.dns.resolver, 'Resolver', side_effect=AssertionError('direct DNS must not gate HTTP')):
            result = core.dns_status('example.com')
            self.assertEqual(result['state'], 'RESOLVES')
            self.assertEqual(result['source'], 'SYSTEM')
            self.assertEqual(result['records']['A'], ['93.184.216.34'])
            core.link_policy().validate_url('https://example.com/')

    def test_system_policy_rejects_mixed_private_answers_and_private_redirects(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))
        private = (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::1', 443, 0, 0))
        with patch.object(core.socket, 'getaddrinfo', return_value=[public, private]):
            with self.assertRaises(EgressDenied):
                core.link_policy().validate_url('https://example.com/')
        response = Mock(status_code=302, headers={'Location': 'http://127.0.0.1/private'})
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        get = Mock(return_value=response)
        with patch.object(core.socket, 'getaddrinfo', return_value=[public]):
            result = core.http_status('https://example.com/', get=get)
        self.assertEqual(result['reason'], 'egress_denied')
        self.assertEqual(get.call_count, 1)

    def test_system_dns_failure_falls_back_without_inventing_nxdomain(self):
        resolver = Mock(); resolver.resolve.side_effect = dns.exception.Timeout()
        with patch.object(core.socket, 'getaddrinfo', side_effect=socket.gaierror('private details')), patch.object(core.dns.resolver, 'Resolver', return_value=resolver):
            result = core.dns_status('example.com')
        self.assertEqual(result['state'], 'TIMEOUT')
        self.assertNotIn('private', json.dumps(result))

    def test_system_resolution_has_local_timeout(self):
        with patch.object(core.socket, 'getaddrinfo', return_value=[]), patch.object(core.queue.Queue, 'get', side_effect=core.queue.Empty):
            with self.assertRaises(dns.exception.Timeout):
                core.system_addresses('example.com')

    def test_registered_domain_uses_offline_psl_idna_and_full_url(self):
        self.assertEqual(core.registered_domain('https://a.shop.example.com.vn/login'), 'example.com.vn')
        self.assertEqual(core.registered_domain('https://a.example.co.uk/path?q=1'), 'example.co.uk')
        self.assertEqual(core.registered_domain('https://bücher.de'), 'xn--bcher-kva.de')

    def test_private_registry_names_keep_the_registered_label(self):
        for host, expected in [
            ('fixture.radio.am', 'fixture.radio.am'),
            ('www.fixture.radio.am', 'fixture.radio.am'),
            ('fixture.radio.fm', 'fixture.radio.fm'),
            ('login.fixture.africa.com', 'fixture.africa.com'),
            ('fixture.uk.com', 'fixture.uk.com'),
            ('www.fixture.ru.com', 'fixture.ru.com'),
            ('fixture.github.io', 'fixture.github.io'),
            ('radio.am', 'radio.am'), ('africa.com', 'africa.com'),
            ('www.example.co.uk', 'example.co.uk'),
            ('sub.bücher.africa.com', 'xn--bcher-kva.africa.com'),
        ]:
            with self.subTest(host=host):
                self.assertEqual(core.registered_domain(f'https://{host}/login?q=1'), expected)

    def test_private_whois_queries_exact_name_and_returns_only_statuses(self):
        import whois
        from domaincheck.engine import phishing_toolkit as toolkit
        for suffix in ('radio.am', 'radio.fm', 'africa.com', 'uk.com', 'ru.com'):
            domain = f'fixture.{suffix}'
            raw = f'Domain Name: {domain.upper()}\nDomain Status: serverHold https://icann.org/epp#serverHold\nRegistrant Email: private@fixture.example\n'
            with self.subTest(suffix=suffix), patch.object(toolkit, 'query_whois_server', return_value=raw) as query, \
                    patch.object(whois, 'whois', side_effect=AssertionError('Must not strip private name')):
                result = core.whois_observation(domain)
                query.assert_called_once_with('whois.centralnic.com', domain)
                self.assertEqual(result['domain'], domain)
                self.assertEqual(result['statuses'], ['serverHold https://icann.org/epp#serverHold'])
                self.assertNotIn('private@', json.dumps(result))

    def test_private_parent_wrong_empty_or_ambiguous_whois_does_not_prove_hold(self):
        from domaincheck.engine import phishing_toolkit as toolkit
        for names in ('Domain Name: AFRICA.COM\n', 'Domain Name: OTHER.AFRICA.COM\n', '',
                      'Domain Name: FIXTURE.AFRICA.COM\nDomain Name: AFRICA.COM\n'):
            raw = names + 'Domain Status: serverHold\n'
            with self.subTest(names=names), patch.object(toolkit, 'query_whois_server', return_value=raw):
                self.assertEqual(core.whois_observation('fixture.africa.com')['statuses'], [])

    def test_private_registration_skips_public_parent_rdap_and_checks_child_identity(self):
        for suffix, tld in [('radio.am', 'am'), ('africa.com', 'com')]:
            domain = f'fixture.{suffix}'
            child = SimpleNamespace(stdout=json.dumps({'domain': domain, 'statuses': ['clientHold']}))
            with self.subTest(suffix=suffix), patch.object(core.requests, 'get', side_effect=AssertionError('No parent RDAP')), \
                    patch.object(core.subprocess, 'run', return_value=child) as run:
                result = core.registration(domain, {tld: 'https://public-registry.example/'})
                self.assertEqual(run.call_args.kwargs['input'], domain)
                self.assertEqual(run.call_args.kwargs['timeout'], 9)
                self.assertEqual(result['state'], 'HOLD')
                self.assertEqual(result['hold_flags'], ['clienthold'])
            wrong = SimpleNamespace(stdout=json.dumps({'domain': suffix, 'statuses': ['serverHold']}))
            with patch.object(core.subprocess, 'run', return_value=wrong):
                self.assertEqual(core.registration(domain, {})['state'], 'UNKNOWN')

    def test_unknown_private_namespace_does_not_invent_registration_from_parent(self):
        import whois
        from domaincheck.engine import phishing_toolkit as toolkit
        with patch.object(toolkit, 'query_whois_server', side_effect=AssertionError('No registry mapping')), \
                patch.object(whois, 'whois', side_effect=AssertionError('No parent query')):
            result = core.whois_observation('fixture.github.io')
            self.assertEqual(result, {'domain': 'fixture.github.io', 'statuses': []})

    def test_private_website_and_dns_keep_original_host_and_full_url(self):
        url = 'https://login.fixture.africa.com/path?q=1'
        with patch.object(core, 'registration', return_value={'state': 'UNKNOWN'}) as registration, \
                patch.object(core, 'dns_status', return_value={'state': 'RESOLVES'}) as dns, \
                patch.object(core, 'http_status', return_value={'website_status': 'HTTP_OK'}) as http:
            result = core.inspect_link(url, {})
        self.assertEqual(result['domain'], 'fixture.africa.com')
        registration.assert_called_once_with('fixture.africa.com', {})
        dns.assert_called_once_with('login.fixture.africa.com')
        http.assert_called_once_with(url)
        self.assertEqual(core.unknown(url)['domain'], 'fixture.africa.com')
        self.assertEqual(result['status'], 'HTTP_OK')

    def test_private_dns_ns_query_uses_registration_name_not_suffix(self):
        resolver = Mock()
        resolver.resolve.return_value = []
        core.dns_status('www.fixture.radio.am', resolver)
        self.assertIn(unittest.mock.call('fixture.radio.am', 'NS', lifetime=2), resolver.resolve.call_args_list)
        self.assertNotIn(unittest.mock.call('radio.am', 'NS', lifetime=2), resolver.resolve.call_args_list)

    def test_private_whois_child_dispatch_uses_validated_record(self):
        from domaincheck.engine import phishing_toolkit as toolkit
        raw = 'Domain Name: FIXTURE.RADIO.AM\nDomain Status: clientTransferProhibited\n'
        output = io.StringIO()
        with patch.object(core.sys, 'stdin', io.StringIO('fixture.radio.am')), \
                patch.object(core.sys, 'stdout', output), patch.object(core.sys, 'argv', ['link_status', 'whois']), \
                patch.object(toolkit, 'query_whois_server', return_value=raw):
            core.main()
        self.assertEqual(json.loads(output.getvalue()), {'domain': 'fixture.radio.am',
                         'statuses': ['clientTransferProhibited']})

    def test_http_classification_does_not_conflate_block_challenge_and_dead_domain(self):
        cf = {'Server': 'cloudflare', 'CF-Ray': 'fixture'}
        for code, headers, body, expected in [
            (404, {}, '', 'URL_NOT_FOUND'), (410, {}, '', 'URL_GONE'),
            (403, cf, 'Error 1020 Access denied', 'ACCESS_BLOCKED'),
            (200, {**cf, 'cf-mitigated': 'challenge'}, '', 'CF_CHALLENGE'),
            (200, cf, 'Suspected Phishing. This website has been reported for potential phishing.', 'CF_PHISHING_BLOCKED'),
            (200, cf, 'Blog post about suspected phishing', 'HTTP_OK'),
            (200, {}, 'Suspected Phishing. reported for potential phishing', 'HTTP_OK'),
            (200, cf, 'Suspected Malware. reported for potential malware', 'CF_MALWARE_BLOCKED'),
            (451, cf, '', 'LEGAL_RESTRICTION'), (522, cf, '', 'SERVER_ERROR'),
            (526, cf, '', 'SERVER_ERROR'), (429, cf, '', 'RATE_LIMITED'),
        ]:
            with self.subTest(code=code, expected=expected): self.assertEqual(core.classify(code, headers, body)[0], expected)

    def test_dns_distinguishes_nxdomain_no_address_and_servfail(self):
        for error, expected in [(dns.resolver.NXDOMAIN(), 'NXDOMAIN'), (dns.resolver.NoAnswer(), 'NO_ADDRESS'),
                                (dns.resolver.NoNameservers(), 'SERVFAIL_OR_REFUSED')]:
            resolver = Mock(); resolver.resolve.side_effect = error
            self.assertEqual(core.dns_status('example.com', resolver)['state'], expected)

    def test_whois_failure_is_unknown_and_rdap_inactive_does_not_prove_hold(self):
        response = Mock(status_code=200, headers={})
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [json.dumps({'objectClassName': 'domain', 'ldhName': 'example.com', 'status': ['inactive']}).encode()]
        with patch.object(core.PublicEgressPolicy, 'validate_url'), patch.object(core.requests, 'get', return_value=response), patch.object(core.subprocess, 'run', side_effect=subprocess.TimeoutExpired('whois', 9)):
            result = core.registration('example.com', {'com': 'https://registry.example/'})
            self.assertEqual(result['state'], 'REGISTERED'); self.assertEqual(result['hold_flags'], [])
        with patch.object(core.subprocess, 'run', side_effect=RuntimeError('private secret')):
            self.assertEqual(core.registration('example.com', {})['state'], 'UNKNOWN')

    def test_whois_hold_and_rdap_404_are_explicit(self):
        with patch.object(core.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps({'statuses': ['serverHold https://icann.org/epp#serverHold']}))):
            self.assertEqual(core.registration('example.com', {})['hold_flags'], ['serverhold'])

    def test_redirect_destination_is_validated_before_follow_and_errors_redacted(self):
        response = Mock(status_code=302, headers={'Location': 'http://127.0.0.1/private'})
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        policy = Mock(); policy.validate_url.side_effect = [None, EgressDenied('private')]
        get = Mock(return_value=response)
        result = core.http_status('https://example.com', policy=policy, get=get)
        self.assertEqual(get.call_count, 1); self.assertEqual(result['reason'], 'egress_denied')
        result = core.http_status('https://example.com', policy=Mock(), get=Mock(side_effect=requests.ConnectionError('private secret')))
        self.assertNotIn('secret', json.dumps(result))

    def test_hold_dns_conflict_and_hard_deadline(self):
        with patch.object(core, 'registration', return_value={'state': 'HOLD'}), patch.object(core, 'dns_status', return_value={'state': 'RESOLVES'}), patch.object(core, 'http_status', return_value={'website_status': 'HTTP_OK'}):
            result = core.inspect_link('https://example.com', {})
            self.assertTrue(result['conflict']); self.assertEqual(result['status'], 'DOMAIN_HOLD')
        with patch.object(core, 'bootstrap', return_value={}), patch.object(core.subprocess, 'run', side_effect=subprocess.TimeoutExpired('probe', 45)):
            self.assertEqual(core.check_bounded('https://example.com')['reason'], 'probe_deadline_exceeded')


class LinkStatusWebTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_input_dedupe_groups_no_result_limit_or_body_limit(self):
        from domaincheck.api.link_status import CheckInput
        text = 'Brand\n' + '\n'.join(f'https://host-{i}.example.com/path' for i in range(5000))
        self.assertGreater(len(text), 100000)
        value = CheckInput(text=text)
        svc = LinkStatusService(checker=core.unknown)
        data = svc.prepare(value.text + '\nhttps://host-0.example.com/path\nhttps://user:private@example.com/')
        self.assertEqual(data['total'], 5000)
        self.assertEqual(data['duplicate_count'], 1); self.assertEqual(data['invalid_count'], 1)
        self.assertNotIn('private', json.dumps(data))
        self.assertEqual(data['groups'][data['urls'][0]], 'Brand')

    async def test_stream_is_request_local_fresh_and_keeps_other_results_after_failure(self):
        calls = []
        def checker(url):
            calls.append(url)
            if 'bad' in url: raise RuntimeError('private')
            return core.unknown(url)
        svc = LinkStatusService(checker=checker)
        first = svc.prepare('Brand\nhttps://bad.example.com\nhttps://good.example.com')
        second = svc.prepare('https://other.example.com')
        async def consume(data): return [event async for event in svc.stream(data)]
        events, other = await asyncio.gather(consume(first), consume(second))
        self.assertEqual(events[0]['type'], 'start'); self.assertEqual(events[-1]['type'], 'complete')
        self.assertEqual(len([v for v in events if v['type'] == 'result']), 2)
        self.assertNotIn('private', json.dumps(events))
        self.assertNotIn('good.example.com', json.dumps(other))
        await consume(svc.prepare('https://good.example.com'))
        self.assertEqual(calls.count('https://good.example.com/'), 2)

    async def test_more_than_200_streams_with_bounded_global_concurrency(self):
        import threading
        import time
        active, peak = 0, 0
        lock = threading.Lock()
        def checker(url):
            nonlocal active, peak
            with lock: active += 1; peak = max(peak, active)
            time.sleep(.001)
            result = core.unknown(url)
            with lock: active -= 1
            return result
        svc = LinkStatusService(checker=checker)
        data = svc.prepare('\n'.join(f'https://host-{i}.example.com' for i in range(250)))
        events = [event async for event in svc.stream(data)]
        self.assertEqual(events[-1], {'type': 'complete', 'processed': 250})
        self.assertLessEqual(peak, 5)

    async def test_api_stream_permission_csrf_and_no_workspace_or_export(self):
        from domaincheck.api.dependencies import current_user, get_db_session
        from domaincheck.infrastructure.auth.sessions import hash_token
        from domaincheck.main import create_app
        from domaincheck.settings import Settings
        actor = SimpleNamespace(user_id=uuid4(), status='active', permissions={'link_status.view'}, csrf_hash=hash_token('csrf'))
        app = create_app(Settings(_env_file=None))
        app.state.link_status_checker = core.unknown
        async def user(): return actor
        async def db(): yield SimpleNamespace()
        app.dependency_overrides[current_user] = user; app.dependency_overrides[get_db_session] = db
        headers = {'Origin': 'http://127.0.0.1:5173', 'X-CSRF-Token': 'csrf'}
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.post('/api/v1/link-status/check', json={'text': 'example.com'}, headers=headers)).status_code, 403)
            actor.permissions.add('link_status.check')
            self.assertEqual((await client.post('/api/v1/link-status/check', json={'text': 'example.com'})).status_code, 403)
            for _ in range(2):
                response = await client.post('/api/v1/link-status/check', json={'text': 'example.com'}, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertIn('application/x-ndjson', response.headers['content-type'])
                events = [json.loads(line) for line in response.text.splitlines()]
                self.assertEqual(events[-1]['processed'], 1)
            self.assertEqual((await client.get('/api/v1/link-status')).status_code, 404)
            self.assertEqual((await client.get('/api/v1/link-status/export')).status_code, 404)
        await app.state.database.dispose(); await app.state.redis.aclose()

    async def test_closing_stream_stops_scheduling_remaining_targets(self):
        import time
        calls = []
        def checker(url):
            calls.append(url); time.sleep(.01)
            return core.unknown(url)
        svc = LinkStatusService(checker=checker)
        stream = svc.stream(svc.prepare('\n'.join(f'host-{i}.example.com' for i in range(300))))
        self.assertEqual((await anext(stream))['type'], 'start')
        self.assertEqual((await anext(stream))['type'], 'result')
        await stream.aclose()
        await asyncio.sleep(.03)
        self.assertLess(len(calls), 20)
