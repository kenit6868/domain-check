import asyncio
import json
import subprocess
import socket
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
