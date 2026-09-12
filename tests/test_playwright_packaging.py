import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import browser_evidence


ROOT = Path(__file__).resolve().parents[1]


class PlaywrightPackagingTests(unittest.TestCase):
    def test_frozen_runtime_uses_hermetic_browser_path(self):
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(browser_evidence.sys, "frozen", True, create=True),
                patch.object(browser_evidence.sys, "_MEIPASS", folder, create=True),
                patch.dict(os.environ, {}, clear=True),
            ):
                browser_evidence.configure_playwright_runtime()
                self.assertEqual(
                    os.environ["PLAYWRIGHT_BROWSERS_PATH"],
                    str(Path(folder) / "playwright" / "driver" / "package.local-browsers"),
                )

    def test_build_installs_chromium_hermetically_before_pyinstaller(self):
        source = (ROOT / "build_app.bat").read_text(encoding="utf-8")
        self.assertIn('set "PLAYWRIGHT_BROWSERS_PATH=0"', source)
        self.assertIn("python -m playwright install chromium chromium-headless-shell --no-progress", source)
        self.assertLess(
            source.index("python -m playwright install chromium chromium-headless-shell --no-progress"),
            source.index("python -m PyInstaller PhishingTool.spec -y --clean"),
        )
        self.assertIn("python -m PyInstaller PhishingTool.spec -y --clean", source)
        self.assertIn("package.local-browsers\\chromium-*", source)
        self.assertIn("chrome-win64\\chrome.exe", source)
        self.assertIn(
            "chrome-headless-shell-win64\\chrome-headless-shell.exe",
            source,
        )

    def test_spec_requires_bundled_chromium_and_headless_shell(self):
        source = (ROOT / "PhishingTool.spec").read_text(encoding="utf-8")
        self.assertIn("package.local-browsers", source)
        self.assertIn('"driver" / "package" / ".local-browsers"', source)
        self.assertIn("playwright_browser_datas", source)
        self.assertIn('if ".local-browsers" not in Path(source).parts', source)
        self.assertIn('and ".local-browsers" not in Path(destination).parts', source)
        self.assertIn("a.datas = _without_source_playwright_browsers(a.datas)", source)
        self.assertIn("a.binaries = _without_source_playwright_browsers(a.binaries)", source)
        self.assertIn("chromium-*/chrome-win*/chrome.exe", source)
        self.assertIn(
            "chromium_headless_shell-*/chrome-headless-shell-win*/chrome-headless-shell.exe",
            source,
        )


if __name__ == "__main__":
    unittest.main()
