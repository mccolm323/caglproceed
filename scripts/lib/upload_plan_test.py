import unittest

from upload_plan import is_duplicate_copy, plan_uploads


class TestIsDuplicateCopy(unittest.TestCase):
    def test_detects_numbered_suffix_duplicate(self):
        names = {"Proceedings_1850-1854_VOL._I.pdf", "Proceedings_1850-1854_VOL._I (1).pdf"}
        self.assertTrue(is_duplicate_copy("Proceedings_1850-1854_VOL._I (1).pdf", names))

    def test_does_not_flag_the_original(self):
        names = {"Proceedings_1850-1854_VOL._I.pdf", "Proceedings_1850-1854_VOL._I (1).pdf"}
        self.assertFalse(is_duplicate_copy("Proceedings_1850-1854_VOL._I.pdf", names))

    def test_does_not_flag_an_unrelated_file(self):
        names = {"Proceedings_2020.pdf"}
        self.assertFalse(is_duplicate_copy("Proceedings_2020.pdf", names))

    def test_numbered_suffix_without_a_matching_base_is_not_a_duplicate(self):
        names = {"Something (1).pdf"}
        self.assertFalse(is_duplicate_copy("Something (1).pdf", names))


class TestPlanUploads(unittest.TestCase):
    def test_excludes_duplicate_copies(self):
        local = ["A.pdf", "A (1).pdf", "B.pdf"]
        self.assertEqual(plan_uploads(local, already_uploaded=set()), ["A.pdf", "B.pdf"])

    def test_excludes_already_uploaded_files(self):
        local = ["A.pdf", "B.pdf"]
        self.assertEqual(plan_uploads(local, already_uploaded={"A.pdf"}), ["B.pdf"])

    def test_empty_local_list(self):
        self.assertEqual(plan_uploads([], already_uploaded=set()), [])

    def test_result_is_sorted(self):
        local = ["Zebra.pdf", "Apple.pdf"]
        self.assertEqual(plan_uploads(local, already_uploaded=set()), ["Apple.pdf", "Zebra.pdf"])


if __name__ == "__main__":
    unittest.main()
