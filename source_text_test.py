"""Regression guard for source-text/mapping.json completeness.

Every committed volume .txt file must have a mapping.json entry pointing
to a real PDF, or that volume's search results silently render with
"(source PDF not identified)" instead of a working link.
"""
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE_TEXT_DIR = os.path.join(HERE, "source-text")
MAPPING_PATH = os.path.join(SOURCE_TEXT_DIR, "mapping.json")


class TestMappingCoversEveryVolume(unittest.TestCase):
    def test_every_txt_file_has_a_mapping_entry(self):
        with open(MAPPING_PATH) as f:
            mapping = json.load(f)
        mapped_txt_files = set(mapping.values())

        txt_files = {f for f in os.listdir(SOURCE_TEXT_DIR) if f.endswith(".txt")}

        missing = sorted(txt_files - mapped_txt_files)
        self.assertEqual(
            missing, [], f"{len(missing)} volume(s) have no mapping.json entry: {missing}"
        )

    def test_every_mapping_entry_points_to_a_file_that_exists(self):
        with open(MAPPING_PATH) as f:
            mapping = json.load(f)

        missing = [
            txt_filename
            for txt_filename in mapping.values()
            if not os.path.exists(os.path.join(SOURCE_TEXT_DIR, txt_filename))
        ]
        self.assertEqual(
            missing, [], f"mapping.json points to {len(missing)} non-existent file(s): {missing}"
        )


if __name__ == "__main__":
    unittest.main()
