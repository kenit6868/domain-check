import csv
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import phishing_toolkit as pt


class SentLogTests(unittest.TestCase):
    def test_concurrent_writers_keep_every_row_in_shared_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = os.path.join(temp_dir, "sent_log.csv")

            def write_row(index):
                pt.log_sent({
                    "timestamp": f"2026-08-04T00:00:{index:02d}+00:00",
                    "domain": f"domain-{index}.example",
                    "account": f"user-{index}@example.org",
                    "success": True,
                })

            with patch.object(pt, "SENT_LOG_PATH", log_path):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(write_row, range(20)))

            with open(log_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

            self.assertEqual(len(rows), 20)
            self.assertEqual(
                {row["account"] for row in rows},
                {f"user-{index}@example.org" for index in range(20)},
            )
            self.assertFalse(os.path.exists(log_path + ".lock"))

    def test_existing_log_schema_is_extended_with_account(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = os.path.join(temp_dir, "sent_log.csv")
            with open(log_path, "w", newline="", encoding="utf-8") as f:
                f.write("timestamp,domain,success\n")
                f.write("2026-08-03T00:00:00+00:00,old.example,True\n")

            with patch.object(pt, "SENT_LOG_PATH", log_path):
                pt.log_sent({
                    "timestamp": "2026-08-04T00:00:00+00:00",
                    "domain": "new.example",
                    "account": "sender@example.org",
                    "success": True,
                })

            with open(log_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

            self.assertEqual(rows[0]["account"], "")
            self.assertEqual(rows[1]["account"], "sender@example.org")


if __name__ == "__main__":
    unittest.main()
