import os
import tempfile
import unittest
from unittest.mock import patch

import phishing_toolkit as pt


class CloakingConfigTests(unittest.TestCase):
    def test_loads_valid_vantage_points_and_ignores_incomplete_items(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config.ini")
            with open(config_path, "w", encoding="utf-8") as config_file:
                config_file.write(
                    "[cloaking]\n"
                    'vantage_points = [{"name":"vn-mobile","country":"vn",'
                    '"proxy":"socks5://user:pass@proxy.example:1080","browser":true},'
                    '{"name":"missing-proxy"}]\n'
                )
            with patch.object(pt, "CONFIG_PATH", config_path):
                config = pt.load_config()
        self.assertEqual(config["cloaking_vantage_points"], [{
            "name": "vn-mobile", "country": "VN",
            "proxy": "socks5://user:pass@proxy.example:1080", "browser": True,
        }])

    def test_invalid_vantage_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config.ini")
            with open(config_path, "w", encoding="utf-8") as config_file:
                config_file.write("[cloaking]\nvantage_points = not-json\n")
            with patch.object(pt, "CONFIG_PATH", config_path):
                config = pt.load_config()
        self.assertEqual(config["cloaking_vantage_points"], [])


if __name__ == "__main__":
    unittest.main()
