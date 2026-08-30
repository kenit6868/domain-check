import unittest
from unittest.mock import MagicMock, patch

import cloaking_ui


class CloakingUiTests(unittest.TestCase):
    def test_playwright_screenshots_render_as_horizontal_thumbnails(self):
        result = {
            "verdict": "POSSIBLE",
            "score": 25,
            "profiles": {},
            "playwright": {
                "available": True,
                "verdict": "POSSIBLE",
                "score": 25,
                "screenshots": [
                    {"path": "desktop.png", "label": "Desktop"},
                    {"path": "mobile.png", "label": "Mobile"},
                ],
            },
        }
        streamlit = MagicMock()
        streamlit.container.return_value.__enter__.return_value = streamlit.container.return_value
        with (
            patch.object(cloaking_ui, "st", streamlit),
            patch.object(cloaking_ui.os.path, "isfile", return_value=True),
        ):
            cloaking_ui.render_cloaking_result(result)

        streamlit.container.assert_called_once_with(horizontal=True, gap="small")
        self.assertEqual(streamlit.image.call_count, 2)
        self.assertTrue(all(call.kwargs["width"] == 160 for call in streamlit.image.call_args_list))


if __name__ == "__main__":
    unittest.main()
