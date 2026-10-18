"""The supported web application must not depend on retired desktop/root code."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULES = {'phishing_toolkit', 'browser_evidence', 'cloaking_detector', 'provider_replies',
    'quick_report_actions', 'cloudflare_dashboard_session', 'cloudflare_abuse_api'}


class WebOnlyLayoutTests(unittest.TestCase):
    def test_backend_has_no_bare_legacy_import_or_dynamic_module_lookup(self):
        for path in (ROOT / 'backend/src').rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse({alias.name.split('.')[0] for alias in node.names} & MODULES, str(path))
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn((node.module or '').split('.')[0], MODULES, str(path))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module':
                    if node.args and isinstance(node.args[0], ast.Constant):
                        self.assertNotIn(node.args[0].value, MODULES, str(path))

    def test_retired_application_entrypoints_and_runtime_are_absent(self):
        self.assertEqual(list(ROOT.glob('*.py')), [])
        self.assertEqual(list((ROOT / 'pages').glob('*.py')), [])
        self.assertEqual(list((ROOT / 'backend/compat').glob('*.py')), [])
        self.assertFalse((ROOT / 'backend/requirements-legacy.txt').exists())
        self.assertFalse((ROOT / 'backend/scripts/build_app.bat').exists())
        self.assertFalse((ROOT / 'PhishingTool.spec').exists())

    def test_packaged_engines_import_without_reading_config_or_writing_runtime(self):
        script = '''
import builtins, importlib, pathlib, sys
sys.path.insert(0, sys.argv[1])
original_open = builtins.open
def guarded_open(file, *args, **kwargs):
    if str(file).endswith('config.ini'):
        raise AssertionError('engine tried to read legacy config')
    return original_open(file, *args, **kwargs)
builtins.open = guarded_open
modules = %r
for name in modules:
    module = importlib.import_module('domaincheck.engine.' + name)
    assert not hasattr(module, 'load_config')
assert not (set(modules) & set(sys.modules))
assert not pathlib.Path('data').exists()
assert pathlib.Path('config.ini').read_text() == 'fixture-only'
''' % sorted(MODULES)
        # Read the sentinel with the real open only after the import guard was checked.
        script = script.replace("assert pathlib.Path('config.ini').read_text()", "builtins.open = original_open\nassert pathlib.Path('config.ini').read_text()")
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'config.ini').write_text('fixture-only')
            result = subprocess.run([sys.executable, '-I', '-c', script, str(ROOT / 'backend/src')],
                cwd=directory, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
