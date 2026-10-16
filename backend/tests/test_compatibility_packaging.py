from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


GUIDANCE_SMOKE = '''
from domaincheck.core.report_guidance import build_report_guidance
guides = build_report_guidance(domain='fixture.cc', target_url='https://fixture.cc/path?q=1',
    result={'cloudflare': True, 'whois': {'registrar': 'NAMECHEAP INC'},
        'registrar_abuse_email_used': 'abuse@namecheap.com',
        'registry_contact': {'source': 'static_table', 'registry': 'Fixture Registry',
            'abuse_email': 'abuse@registry.example',
            'report_webform': 'https://registry.example/abuse'}},
    profile={'brand_name': 'Fixture', 'contact_name': 'Tester', 'contact_email': 'test@example.com'})
assert len([g for g in guides if g['channel'] == 'browser']) == 5
assert any(g['channel'] == 'cdn' for g in guides)
assert any(g['channel'] == 'registrar' and 'To: abuse@namecheap.com' in g['draft'] for g in guides)
assert any(g['channel'] == 'registry' and g['form_url'] for g in guides)
assert any(g['channel'] == 'registry' and 'To: abuse@registry.example' in g['draft'] for g in guides)
from domaincheck.core.worker_precheck import email_examples, LegacyWorkerPrecheckAdapter
LegacyWorkerPrecheckAdapter.validate_runtime()
examples = email_examples('fixture.cc', 'https://fixture.cc/path?q=1', {
    'recipients': [{'channel': 'hosting', 'email': 'abuse@host.example'}],
    'hosting_ip': '8.8.8.8', 'origin_ip_whois': {'org': 'Fixture Host', 'abuse_email': 'abuse@host.example'}},
    {'contact_name': 'Tester', 'contact_email': 'test@example.com'})
assert examples[0]['advisory'] and 'Reported URL: https://fixture.cc/path?q=1' in examples[0]['body']
from domaincheck.core.email_content import bundled_logo_bytes
from domaincheck.application.sent_copy import SentCopyService, LegacySentCopyAdapter
import inspect, phishing_toolkit
assert 'message_id' in inspect.signature(phishing_toolkit._imap_save_sent).parameters
from browser_evidence import format_email_evidence_manifest
assert '--- Observed Phishing Behavior and Supporting Evidence ---' in format_email_evidence_manifest({'evidence_type': 'manual_upload'})
assert bundled_logo_bytes().startswith(bytes([255, 216, 255]))
from email.message import EmailMessage
from domaincheck.core.email_content import set_email_content
message = EmailMessage()
set_email_content(message, 'Report body' + chr(10) + chr(10) + 'Regards,' + chr(10) + 'Fixture')
assert any(part.get_content_maintype() == 'image' for part in message.walk())
'''


class CompatibilityPackagingTests(unittest.TestCase):
    def test_installed_check_domain_guidance_outside_repository_cwd(self):
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, '-I', '-c', GUIDANCE_SMOKE], cwd=cwd,
                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installed_api_can_render_completed_result_outside_repository_cwd(self):
        script = '''
from datetime import date
from types import SimpleNamespace
from uuid import uuid4
from domaincheck.api.quick_reports import response
check = SimpleNamespace(id=uuid4(), requested_url='https://fixture.monster/path?q=1',
    domain='fixture.monster', status='succeeded', result={'registrar':'Dynadot'}, cache_provenance={})
operation = SimpleNamespace(job=SimpleNamespace(id=uuid4(), state='completed', stage='completed', business_day=date(2026,10,16)), checks=[check], duplicates=[])
dto = response(operation)
assert len(dto.checks[0].report_forms) == 2
assert any(p['id']=='xyz_registry_abuse' for p in dto.checks[0].providers)
'''
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, '-I', '-c', script], cwd=cwd,
                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_WHEEL_PATH'), 'requires freshly built wheel')
    def test_built_wheel_has_explicit_modules_and_runs_without_editable_paths(self):
        wheel = Path(os.environ['DOMAINCHECK_TEST_WHEEL_PATH']).resolve()
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            metadata = archive.read(next(name for name in names if name.endswith('.dist-info/METADATA'))).decode()
            self.assertIn('Requires-Dist: playwright==1.62.0', metadata)
        self.assertTrue({'phishing_toolkit.py', 'cloaking_detector.py', 'browser_evidence.py',
            'quick_report_actions.py'} <= names)
        self.assertFalse(any(name.endswith('config.ini') or '/.env' in name or
            name.startswith(('data/', 'reports/')) for name in names))
        script = '''
import sys, sysconfig
sys.path.insert(0, sys.argv[1])
sys.path.append(sysconfig.get_paths()['purelib'])
import phishing_toolkit
from domaincheck.core.quick_report_routes import report_forms
assert phishing_toolkit.__file__.startswith(sys.argv[1])
assert len(report_forms('fixture.monster', {'registrar':'Dynadot'})) == 2
'''
        script += GUIDANCE_SMOKE
        with tempfile.TemporaryDirectory() as cwd:
            installed = Path(cwd) / 'installed-wheel'
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(installed)
            result = subprocess.run([sys.executable, '-I', '-S', '-c', script, str(installed)],
                cwd=cwd, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
