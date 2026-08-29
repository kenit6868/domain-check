import unittest

from domain_utils import domain_cache_key, filter_unseen_domains


class DomainUtilsTests(unittest.TestCase):
    def test_domain_cache_key_uses_domain_and_subpath(self):
        self.assertEqual(domain_cache_key("HTTPS://Example.COM/login"), "https://example.com/login")
        self.assertEqual(domain_cache_key("example.com/other?q=1"), "https://example.com/other?q=1")

    def test_filter_unseen_domains_dedupes_against_cache_and_current_batch(self):
        seen = {"https://old.example/login"}
        fresh, duplicate = filter_unseen_domains([
            "https://new.example/a",
            "https://OLD.example/login",
            "https://new.example/a",
            "https://new.example/b",
            "second.example",
        ], seen)
        self.assertEqual(fresh, ["https://new.example/a", "https://new.example/b", "second.example"])
        self.assertEqual(duplicate, ["https://OLD.example/login", "https://new.example/a"])
        self.assertEqual(seen, {
            "https://old.example/login", "https://new.example/a",
            "https://new.example/b", "https://second.example/",
        })


if __name__ == "__main__":
    unittest.main()
