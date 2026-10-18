"""Render the complete deployment using fixture-only configuration, no daemon."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import importlib.util

ROOT = Path(__file__).resolve().parents[2]


class DeploymentHelperTests(unittest.TestCase):
    def load_helper(self):
        spec = importlib.util.spec_from_file_location('deploy_team', ROOT / 'backend/scripts/deploy_team.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_build_and_migration_failure_do_not_start_new_application(self):
        from types import SimpleNamespace
        import sys
        import contextlib
        import io
        helper = self.load_helper()
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / '.env').write_text('fixture-only')
            for failures in ([1], [0, 0, 0, 7]):
                with patch.object(helper, 'BACKEND', Path(directory)), \
                     patch.object(sys, 'argv', ['deploy_team']), \
                     patch.object(helper.subprocess, 'run', side_effect=[SimpleNamespace(returncode=code) for code in failures]) as run, \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(helper.main(), failures[-1])
                self.assertEqual(run.call_count, len(failures))

    def test_deploy_plan_builds_then_drains_before_migration(self):
        commands = self.load_helper().deployment_commands()
        self.assertEqual(commands[0][-1], 'build')
        self.assertIn('stop', commands[2])
        self.assertIn('migrate', commands[3])
        self.assertIn('--exit-code-from', commands[3])
        self.assertIn('--wait', commands[4])
        self.assertNotIn('down', [arg for command in commands for arg in command])


@unittest.skipUnless(shutil.which('docker'), 'requires Docker Compose CLI')
class TeamDeploymentTests(unittest.TestCase):
    def test_complete_stack_isolated_services_private_storage_and_safe_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('compose.yaml', 'compose.production.yaml'):
                shutil.copyfile(ROOT / 'backend' / name, root / name)
            (root / '.env').write_text(
                'POSTGRES_PASSWORD=fixture-only\n'
                'DOMAINCHECK_CONTAINER_DATABASE_URL=postgresql+asyncpg://fixture:fixture@db/fixture\n'
                'DOMAINCHECK_PUBLIC_HOST=fixture.example.test\n'
                'VITE_FORM_ASSISTANT_EXTENSION_ID=' + 'a' * 32 + '\n')
            output = subprocess.run(['docker', 'compose', '--env-file', str(root / '.env'),
                '-f', str(root / 'compose.yaml'), '-f', str(root / 'compose.production.yaml'),
                'config', '--format', 'json'], cwd=directory, capture_output=True, text=True, timeout=20)
            self.assertEqual(output.returncode, 0, output.stderr)
            config = json.loads(output.stdout)
        services = config['services']
        self.assertEqual(set(services), {'db', 'redis', 'migrate', 'api', 'web', 'quick-report',
            'check-domain', 'precheck', 'evidence', 'send-mail', 'cloudflare-runner'})
        self.assertEqual(config['name'], 'domaincheck')
        for name in ('api', 'quick-report', 'check-domain', 'precheck', 'evidence', 'send-mail', 'cloudflare-runner'):
            with self.subTest(name=name):
                self.assertEqual(services[name]['restart'], 'unless-stopped')
                self.assertNotIn('ports', services[name])
                self.assertEqual(services[name]['environment']['DOMAINCHECK_ARTIFACT_ROOT'], '/var/lib/domaincheck/artifacts')
                self.assertEqual(services[name]['depends_on']['migrate']['condition'], 'service_completed_successfully')
                self.assertEqual(services[name]['stop_grace_period'], '30m0s')
        self.assertEqual(services['cloudflare-runner']['environment']['DOMAINCHECK_CLOUDFLARE_RUNNER_HOST'], '0.0.0.0')
        self.assertEqual(services['api']['command'] if 'command' in services['api'] else None, None)
        self.assertEqual(services['evidence']['command'][-1], 'browser_evidence')
        self.assertIn('postgres-data', config['volumes'])
        self.assertIn('artifacts-data', config['volumes'])

    def test_build_contexts_exclude_configuration_and_runtime(self):
        backend_ignore = (ROOT / 'backend/.dockerignore').read_text()
        self.assertTrue(backend_ignore.startswith('*\n'))
        self.assertNotIn('!.env', backend_ignore)
        self.assertNotIn('!.runtime', backend_ignore)
        frontend_ignore = (ROOT / 'frontend/.dockerignore').read_text()
        self.assertIn('.env\n', frontend_ignore)
        self.assertIn('node_modules\n', frontend_ignore)

    def test_runtime_dependencies_include_oidc_client_and_browser_version_matches_image(self):
        import tomllib
        metadata = tomllib.loads((ROOT / 'backend/pyproject.toml').read_text())
        self.assertIn('httpx==0.28.1', metadata['project']['dependencies'])
        playwright = next(item.split('==')[1] for item in metadata['project']['dependencies'] if item.startswith('playwright=='))
        self.assertIn(f'ARG PLAYWRIGHT_VERSION={playwright}', (ROOT / 'backend/Dockerfile').read_text())
