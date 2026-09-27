import os
import sqlite3
import tempfile
import unittest

from build_db import build


class TestBuild(unittest.TestCase):
    def test_builds_db_from_txt_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "Proceedings_2020.txt"), "w") as f:
                f.write("Earl Warren spoke\x0cA second page")
            with open(os.path.join(d, "Proceedings_2021.txt"), "w") as f:
                f.write("Another volume entirely")

            out_db = os.path.join(d, "out.db")
            result = build(d, out_db)

            self.assertEqual(result["volumes"], 2)
            self.assertEqual(result["pages"], 3)
            self.assertEqual(result["skipped"], [])

            conn = sqlite3.connect(out_db)
            count = conn.execute("SELECT COUNT(*) FROM volumes").fetchone()[0]
            self.assertEqual(count, 2)

    def test_skips_unreadable_file_and_continues(self):
        with tempfile.TemporaryDirectory() as d:
            good = os.path.join(d, "Good.txt")
            bad = os.path.join(d, "Bad.txt")
            with open(good, "w") as f:
                f.write("hello world")
            with open(bad, "w") as f:
                f.write("unreadable")
            os.chmod(bad, 0o000)
            try:
                result = build(d, os.path.join(d, "out.db"))
            finally:
                os.chmod(bad, 0o644)

            self.assertEqual(result["volumes"], 1)
            self.assertEqual(len(result["skipped"]), 1)
            self.assertEqual(result["skipped"][0][0], "Bad.txt")

    def test_overwrites_an_existing_db_file(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "A.txt"), "w") as f:
                f.write("first build")
            out_db = os.path.join(d, "out.db")
            build(d, out_db)
            first_size = os.path.getsize(out_db)
            self.assertGreater(first_size, 0)

            result = build(d, out_db)
            self.assertEqual(result["volumes"], 1)


if __name__ == "__main__":
    unittest.main()
