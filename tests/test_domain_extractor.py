import unittest

from domain_utils import extract_domains_from_text


class DomainExtractorTests(unittest.TestCase):
    def test_extracts_urls_from_mixed_notes_and_removes_duplicates(self):
        raw = """
        789win
        https://789win.successillustrated.co/vn/ (top3)
        https://www.roinetworks.com.mx/vi-vn/ (top2)
        789win.successillustrated.co
        NK88 - không có đối thủ
        """
        self.assertEqual(
            extract_domains_from_text(raw),
            [
                "https://789win.successillustrated.co/vn/",
                "https://www.roinetworks.com.mx/vi-vn/",
                "789win.successillustrated.co",
            ],
        )


if __name__ == "__main__":
    unittest.main()
