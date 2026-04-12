import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fanqie_core.chapter_text import (
    deduplicate_titles,
    extract_chapter_num,
    get_md_files,
    parse_md_file,
    strip_chapter_prefix,
    strip_md_formatting,
)


class ChapterTextModuleTests(unittest.TestCase):
    def test_extract_chapter_num_formats(self):
        self.assertEqual(extract_chapter_num("001_\u6807\u9898"), "1")
        self.assertEqual(extract_chapter_num("\u7b2c27\u7ae0 \u6807\u9898"), "27")
        self.assertEqual(extract_chapter_num("\u7b2c\u5341\u516d\u7ae0 \u53d1\u5e03\u4f1a"), "16")
        self.assertEqual(extract_chapter_num("Chapter 3 - Title"), "3")
        self.assertIsNone(extract_chapter_num("\u5e8f\u7ae0"))

    def test_strip_chapter_prefix(self):
        self.assertEqual(
            strip_chapter_prefix("\u7b2c 27 \u7ae0 \u91cd\u65b0\u5f00\u59cb"),
            "\u91cd\u65b0\u5f00\u59cb",
        )
        self.assertEqual(strip_chapter_prefix("001\uff1a\u65b0\u7684\u65c5\u7a0b"), "\u65b0\u7684\u65c5\u7a0b")
        self.assertEqual(strip_chapter_prefix("Chapter 3 - Hello"), "Hello")

    def test_parse_md_file_with_heading(self):
        with TemporaryDirectory() as td:
            fp = Path(td) / "001_\u5f00\u573a.md"
            fp.write_text("# \u7b2c1\u7ae0 \u5f00\u573a\n\n\u6b63\u6587\u5185\u5bb9", encoding="utf-8")
            chapter_num, title, content = parse_md_file(fp)

        self.assertEqual(chapter_num, "1")
        self.assertEqual(title, "\u5f00\u573a")
        self.assertEqual(content, "\u6b63\u6587\u5185\u5bb9")

    def test_parse_md_file_gbk_fallback(self):
        with TemporaryDirectory() as td:
            fp = Path(td) / "\u7b2c2\u7ae0_\u6d4b\u8bd5.md"
            fp.write_bytes("# \u7b2c2\u7ae0 \u6d4b\u8bd5\n\u6b63\u6587".encode("gbk"))
            chapter_num, title, content = parse_md_file(fp)

        self.assertEqual(chapter_num, "2")
        self.assertEqual(title, "\u6d4b\u8bd5")
        self.assertEqual(content, "\u6b63\u6587")

    def test_get_md_files_one_level_only(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "10.md").write_text("10", encoding="utf-8")
            (root / "2.md").write_text("2", encoding="utf-8")
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / "ignore.doc").write_text("x", encoding="utf-8")

            sub = root / "\u5377\u4e00"
            sub.mkdir()
            (sub / "1.md").write_text("1", encoding="utf-8")

            deep = sub / "\u66f4\u6df1"
            deep.mkdir()
            (deep / "3.md").write_text("3", encoding="utf-8")

            files = get_md_files(root)
            rels = [f.relative_to(root).as_posix() for f in files]

        self.assertEqual(rels, ["2.md", "10.md", "a.txt", "\u5377\u4e00/1.md"])

    def test_strip_md_formatting(self):
        raw = "# \u6807\u9898\n- [x] \u4efb\u52a1\n**\u7c97\u4f53** [\u94fe\u63a5](https://example.com)"
        self.assertEqual(
            strip_md_formatting(raw),
            "\u6807\u9898\n\u4efb\u52a1\n\u7c97\u4f53 \u94fe\u63a5",
        )

    def test_deduplicate_titles(self):
        rows = [
            ("1", "\u9009\u62e9", "a"),
            ("2", "\u9009\u62e9", "b"),
            (None, "\u5e8f\u7ae0", "c"),
            (None, "\u5e8f\u7ae0", "d"),
        ]
        deduped = deduplicate_titles(rows)
        self.assertEqual(deduped[0][1], "\u9009\u62e9\uff081\uff09")
        self.assertEqual(deduped[1][1], "\u9009\u62e9\uff082\uff09")
        self.assertEqual(deduped[2][1], "\u5e8f\u7ae0\uff081\uff09")
        self.assertEqual(deduped[3][1], "\u5e8f\u7ae0\uff082\uff09")


if __name__ == "__main__":
    unittest.main()
