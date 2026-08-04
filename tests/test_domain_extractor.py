import unittest

from domain_utils import extract_branded_domains, extract_domains_from_text


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

    def test_associates_urls_with_nearest_brand_heading(self):
        raw = """
        QS88
        https://frutismo.co/vi-vn/
        https://example.com/login (top3)

        NK88
        opponent.example/path
        """
        self.assertEqual(
            extract_branded_domains(raw),
            [
                {"brand": "QS88", "target": "https://frutismo.co/vi-vn/"},
                {"brand": "QS88", "target": "https://example.com/login"},
                {"brand": "NK88", "target": "opponent.example/path"},
            ],
        )

    def test_cleans_brand_note_and_splits_urls_stuck_together(self):
        raw = """
        okfun - k có đối thủ

        DN88
        https://crazytattoos.in/vi-vhttps://dn88.jp.net/n/
        """
        self.assertEqual(
            extract_branded_domains(raw),
            [
                {"brand": "DN88", "target": "https://crazytattoos.in/vi-v"},
                {"brand": "DN88", "target": "https://dn88.jp.net/n/"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
