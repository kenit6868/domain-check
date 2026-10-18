"""Existing unpacked extension copy must not overwrite unrelated runtime files."""
from pathlib import Path
import runpy
import tempfile
import unittest


class ExtensionDistributionTests(unittest.TestCase):
    def test_sync_copies_runtime_assets_only_and_preserves_unrelated_files(self):
        sync = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/sync_installed_extension.py'))['sync']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'frontend/extensions/cloudflare-profile-worker'
            destination = root / 'chrome_extension/cloudflare-profile-worker'
            source.mkdir(parents=True); destination.mkdir(parents=True)
            (source / 'manifest.json').write_text('{"version":"3.0.0"}')
            (source / 'background.js').write_text('/* fixture runtime */')
            (source / '.env').write_text('fixture-only')
            (source / 'config.ini').write_text('fixture-only')
            (destination / 'local-note.txt').write_text('keep')
            self.assertEqual(sync(root), 2)
            self.assertEqual((destination / 'background.js').read_bytes(), (source / 'background.js').read_bytes())
            self.assertFalse((destination / '.env').exists())
            self.assertFalse((destination / 'config.ini').exists())
            self.assertEqual((destination / 'local-note.txt').read_text(), 'keep')
