import sqlite3
import unittest

from db_builder import (
    create_schema,
    derive_label,
    insert_volume,
    parse_pages,
    year_of,
)


class TestParsePages(unittest.TestCase):
    def test_splits_on_form_feed(self):
        text = "page one\x0cpage two\x0cpage three"
        self.assertEqual(parse_pages(text), ["page one", "page two", "page three"])

    def test_strips_whitespace_per_page(self):
        text = "  page one  \x0c  page two  "
        self.assertEqual(parse_pages(text), ["page one", "page two"])

    def test_trailing_form_feed_yields_empty_last_page(self):
        text = "only page\x0c"
        self.assertEqual(parse_pages(text), ["only page", ""])

    def test_no_form_feed_is_single_page(self):
        self.assertEqual(parse_pages("just text"), ["just text"])


class TestDeriveLabel(unittest.TestCase):
    def test_replaces_underscores_and_strips_extension(self):
        self.assertEqual(
            derive_label("Proceedings_1910_VOL._XXX.txt"),
            "Proceedings 1910 VOL. XXX",
        )

    def test_handles_year_range_filenames(self):
        self.assertEqual(
            derive_label("Proceedings_1893-1894_VOL._XXI.txt"),
            "Proceedings 1893-1894 VOL. XXI",
        )

    def test_handles_simple_year_filenames(self):
        self.assertEqual(derive_label("Proceedings_2020.txt"), "Proceedings 2020")


class TestYearOf(unittest.TestCase):
    def test_extracts_19th_century_year(self):
        self.assertEqual(year_of("Proceedings 1893-1894 VOL. XXI"), 1893)

    def test_extracts_20th_century_year(self):
        self.assertEqual(year_of("Proceedings 1960 - Vol. LXIII"), 1960)

    def test_extracts_21st_century_year(self):
        self.assertEqual(year_of("Proceedings 2020"), 2020)

    def test_missing_year_sorts_last(self):
        self.assertEqual(year_of("No Year Here"), 9999)


class TestInsertVolume(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        create_schema(self.conn)

    def test_inserts_volume_row_and_non_empty_pages(self):
        text = "first page text\x0c\x0cthird page text"
        num_pages, num_chars = insert_volume(self.conn, 1, "Proceedings_2020.txt", text)

        self.assertEqual(num_pages, 2)
        self.assertEqual(num_chars, len("first page text") + len("third page text"))

        vol = self.conn.execute(
            "SELECT label, source_txt, num_pages FROM volumes WHERE id = 1"
        ).fetchone()
        self.assertEqual(vol, ("Proceedings 2020", "Proceedings_2020.txt", 2))

    def test_preserves_page_number_gaps_for_empty_pages(self):
        text = "one\x0c\x0cthree"
        insert_volume(self.conn, 1, "Proceedings_2020.txt", text)
        page_nums = [
            row[0]
            for row in self.conn.execute(
                "SELECT page_num FROM pages_fts WHERE volume_id = 1 ORDER BY page_num"
            )
        ]
        self.assertEqual(page_nums, [1, 3])

    def test_content_is_searchable_via_fts(self):
        insert_volume(self.conn, 1, "Proceedings_2020.txt", "Earl Warren spoke today")
        rows = self.conn.execute(
            "SELECT page_num FROM pages_fts WHERE pages_fts MATCH 'warren'"
        ).fetchall()
        self.assertEqual(rows, [(1,)])


if __name__ == "__main__":
    unittest.main()
