import unittest

from fanqie_gui import parse_chapter_selector


class ChapterSelectorTests(unittest.TestCase):
    def test_parse_single(self):
        self.assertEqual(parse_chapter_selector("5"), ("single", 5))

    def test_parse_range(self):
        self.assertEqual(parse_chapter_selector("10-5"), ("range", (5, 10)))

    def test_parse_set_and_ranges(self):
        kind, values = parse_chapter_selector("1, 5, 8-10")
        self.assertEqual(kind, "set")
        self.assertEqual(values, {1, 5, 8, 9, 10})

    def test_parse_invalid(self):
        with self.assertRaises(ValueError):
            parse_chapter_selector("1,a")


if __name__ == "__main__":
    unittest.main()
