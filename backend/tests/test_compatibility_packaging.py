from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


class CompatibilityPackagingTests(unittest.TestCase):
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
        with tempfile.TemporaryDirectory() as cwd:
            installed = Path(cwd) / 'installed-wheel'
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(installed)
            result = subprocess.run([sys.executable, '-I', '-S', '-c', script, str(installed)],
                cwd=cwd, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
