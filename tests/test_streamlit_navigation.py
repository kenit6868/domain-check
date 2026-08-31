import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StreamlitNavigationTests(unittest.TestCase):
    def test_every_internal_page_link_is_registered_in_navigation(self):
        entrypoint = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
        registered = set(re.findall(r'st\.Page\(\s*["\']([^"\']+)["\']', entrypoint))
        linked = set()
        for path in [ROOT / "streamlit_app.py", *(ROOT / "pages").glob("*.py")]:
            source = path.read_text(encoding="utf-8")
            linked.update(re.findall(r'st\.page_link\(\s*["\']([^"\']+)["\']', source))

        internal_links = {path for path in linked if not path.startswith(("http://", "https://"))}
        self.assertFalse(
            internal_links - registered,
            f"Internal page links missing from st.navigation: {sorted(internal_links - registered)}",
        )


if __name__ == "__main__":
    unittest.main()
