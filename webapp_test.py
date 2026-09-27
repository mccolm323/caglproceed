import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import webapp


class TestEscapeSnippet(unittest.TestCase):
    def test_escapes_stray_angle_brackets_from_ocr_noise(self):
        # SQLite's snippet() wraps matches in sentinel control chars, not
        # literal HTML, precisely so OCR noise like this can't be read as
        # a real tag and swallow following text in the browser.
        raw = f"Gran{chr(2)}dl{chr(3)} <Secretary> reported"
        self.assertEqual(
            webapp.escape_snippet(raw),
            "Gran<mark>dl</mark> &lt;Secretary&gt; reported",
        )

    def test_escapes_ampersand(self):
        raw = f"Board {chr(2)}of{chr(3)} & Trustees"
        self.assertEqual(webapp.escape_snippet(raw), "Board <mark>of</mark> &amp; Trustees")


class TestClamp(unittest.TestCase):
    def test_clamps_below_minimum(self):
        self.assertEqual(webapp.clamp(-5, 1, 100), 1)

    def test_clamps_above_maximum(self):
        self.assertEqual(webapp.clamp(500, 1, 100), 100)

    def test_within_range_is_unchanged(self):
        self.assertEqual(webapp.clamp(50, 1, 100), 50)


class TestPdfUrl(unittest.TestCase):
    def test_builds_url_with_page_fragment(self):
        url = webapp.pdf_url("https://pub-xxxx.r2.dev", "Proceedings_2020.pdf", 42)
        self.assertEqual(url, "https://pub-xxxx.r2.dev/Proceedings_2020.pdf#page=42")

    def test_strips_trailing_slash_on_base_url(self):
        url = webapp.pdf_url("https://pub-xxxx.r2.dev/", "A.pdf", 1)
        self.assertEqual(url, "https://pub-xxxx.r2.dev/A.pdf#page=1")

    def test_percent_encodes_spaces_and_parens(self):
        url = webapp.pdf_url("https://pub-xxxx.r2.dev", "Proceedings (1901-1902).pdf", 5)
        self.assertEqual(
            url,
            "https://pub-xxxx.r2.dev/Proceedings%20%281901-1902%29.pdf#page=5",
        )


class TestServer(unittest.TestCase):
    """Integration test: boots the real handler against a tiny fixture DB."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(cls.tmpdir.name, "test.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE volumes (
                id INTEGER PRIMARY KEY, label TEXT, source_txt TEXT,
                num_pages INTEGER, num_chars INTEGER
            );
            CREATE VIRTUAL TABLE pages_fts USING fts5(
                content, volume_label UNINDEXED, volume_id UNINDEXED,
                page_num UNINDEXED, tokenize='porter unicode61'
            );
            """
        )
        conn.execute(
            "INSERT INTO volumes VALUES (1, 'Proceedings 2020', 'Proceedings_2020.txt', 1, 20)"
        )
        conn.execute(
            "INSERT INTO pages_fts (content, volume_label, volume_id, page_num) "
            "VALUES ('Earl Warren spoke today', 'Proceedings 2020', 1, 7)"
        )
        # Volume 2 has no entry in mapping.json below, simulating a volume
        # whose PDF hasn't been mapped/uploaded yet.
        conn.execute(
            "INSERT INTO volumes VALUES (2, 'Proceedings 1875', 'Proceedings_1875.txt', 1, 20)"
        )
        conn.execute(
            "INSERT INTO pages_fts (content, volume_label, volume_id, page_num) "
            "VALUES ('the gavel was passed', 'Proceedings 1875', 2, 3)"
        )
        conn.execute(
            "INSERT INTO volumes VALUES (3, 'Proceedings 1901', 'Proceedings_1901.txt', 1, 20)"
        )
        conn.execute(
            "INSERT INTO pages_fts (content, volume_label, volume_id, page_num) "
            "VALUES ('the Grand <Secretary> reported to the lodge', 'Proceedings 1901', 3, 9)"
        )
        conn.commit()
        conn.close()

        mapping_path = os.path.join(cls.tmpdir.name, "mapping.json")
        with open(mapping_path, "w") as f:
            json.dump({"Proceedings_2020.pdf": "Proceedings_2020.txt"}, f)

        webapp.CONN = sqlite3.connect(db_path, check_same_thread=False)
        webapp.PDF_MAP = webapp.load_pdf_map(mapping_path)
        webapp.PDF_BASE_URL = "https://pub-xxxx.r2.dev"
        webapp.build_caches()

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webapp.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmpdir.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as resp:
            return resp.status, json.loads(resp.read())

    def test_volumes_endpoint_lists_the_fixture_volumes(self):
        status, data = self._get("/api/volumes")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["volumes"]), 3)

    def test_search_finds_matching_page_with_pdf_link(self):
        status, data = self._get("/api/search?q=warren")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["results"][0]["pdf"], "Proceedings_2020.pdf")
        self.assertEqual(data["results"][0]["page_num"], 7)

    def test_empty_query_returns_empty_results_without_error(self):
        status, data = self._get("/api/search?q=")
        self.assertEqual(status, 200)
        self.assertEqual(data, {"results": [], "total": 0})

    def test_malformed_fts_query_returns_json_error_not_crash(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get('/api/search?q=%22unbalanced')
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read())
        self.assertIn("error", body)

    def test_non_integer_volume_id_returns_json_error_not_crash(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/search?q=warren&volume_id=not-a-number")
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read())
        self.assertIn("error", body)

    def test_result_with_no_pdf_mapping_renders_without_a_link(self):
        status, data = self._get("/api/search?q=gavel")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        result = data["results"][0]
        self.assertIsNone(result["pdf"])
        self.assertIsNone(result["pdf_url"])

    def test_snippet_with_ocr_angle_bracket_noise_is_escaped_not_swallowed(self):
        status, data = self._get("/api/search?q=secretary")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        snippet = data["results"][0]["snippet"]
        self.assertIn("&lt;<mark>Secretary</mark>&gt;", snippet)

    def test_non_integer_limit_returns_json_error_not_crash(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/search?q=warren&limit=not-a-number")
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read())
        self.assertIn("error", body)

    def test_negative_limit_is_clamped_not_passed_through_as_unlimited(self):
        # -1 is SQLite's "no limit" sentinel; a client must not be able to
        # request an unbounded dump of results via a negative limit.
        status, data = self._get("/api/search?q=warren&limit=-1")
        self.assertEqual(status, 200)

    def test_huge_volume_id_returns_json_error_not_crash(self):
        # A value this large overflows SQLite's 64-bit integer binding and
        # raises OverflowError from CONN.execute(), not from int() parsing.
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/search?q=warren&volume_id=99999999999999999999999999")
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read())
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
