import unittest
from unittest.mock import MagicMock, patch

import community_report_ui


class CommunityReportUiTests(unittest.TestCase):
    def test_renders_both_official_report_form_links(self):
        streamlit = MagicMock()
        streamlit.container.return_value.__enter__.return_value = streamlit.container.return_value
        with patch.object(community_report_ui, "st", streamlit):
            community_report_ui.render_community_report_buttons()

        streamlit.container.assert_called_once_with(horizontal=True, gap="small")
        rendered = [call.args[:2] for call in streamlit.link_button.call_args_list]
        self.assertEqual(rendered, list(community_report_ui.COMMUNITY_REPORT_FORMS))
        self.assertEqual(streamlit.link_button.call_count, 2)


if __name__ == "__main__":
    unittest.main()
