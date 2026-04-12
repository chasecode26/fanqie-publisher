"""Chapter text parsing and markdown cleanup helpers."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Callable

WarnFn = Callable[[str], None]


def natural_sort_key(path: Path):
    """Natural sort: 001 < 2 < 10."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]


_CN_DIGITS = {
    "\u96f6": 0,
    "\u3007": 0,
    "\u4e00": 1,
    "\u4e8c": 2,
    "\u4e24": 2,
    "\u4e09": 3,
    "\u56db": 4,
    "\u4e94": 5,
    "\u516d": 6,
    "\u4e03": 7,
    "\u516b": 8,
    "\u4e5d": 9,
    "\u5341": 10,
    "\u767e": 100,
    "\u5343": 1000,
}


def _cn_to_int(cn: str) -> int:
    """Chinese numerals to int: ??->16, ?????->123."""
    result, current = 0, 0
    for ch in cn:
        val = _CN_DIGITS.get(ch)
        if val is None:
            return 0
        if val >= 10:
            if current == 0:
                current = 1
            result += current * val
            current = 0
        else:
            current = val
    return result + current


def extract_chapter_num(text: str) -> str | None:
    """Extract chapter number from filename/title text."""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = normalized.replace("\u200b", "").replace("\ufeff", "").strip()

    match = re.match(r"^(\d+)", normalized)
    if match:
        return str(int(match.group(1)))

    match = re.match(r"^\u7b2c\s*(\d+)\s*[\u7ae0\u56de\u8282\u8bdd]", normalized)
    if match:
        return str(int(match.group(1)))

    match = re.match(
        r"^\u7b2c([\u96f6\u3007\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343]+)[\u7ae0\u56de\u8282\u8bdd]",
        normalized,
    )
    if match:
        num = _cn_to_int(match.group(1))
        if num > 0:
            return str(num)

    match = re.match(r"^chapter[_\-\s]*(\d+)", normalized, re.IGNORECASE)
    if match:
        return str(int(match.group(1)))

    return None


def strip_chapter_prefix(text: str) -> str:
    """Strip leading chapter number markers from title text."""
    original = str(text or "").strip()
    patterns = [
        r"^\u7b2c\s*\d+\s*[\u7ae0\u56de\u8282\u8bdd][\s:\uff1a_\-]*",
        r"^\u7b2c[\u96f6\u3007\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343]+[\u7ae0\u56de\u8282\u8bdd][\s:\uff1a_\-]*",
        r"^\d+[\s:\uff1a_\-]+",
        r"^chapter[\s_\-]*\d+[\s_\-]*",
    ]

    for pattern in patterns:
        cleaned = re.sub(pattern, "", original, flags=re.IGNORECASE).strip()
        if cleaned and cleaned != original:
            return cleaned
    return original


def parse_md_file(fp: Path, warn: WarnFn | None = None) -> tuple[str | None, str, str]:
    """Parse one chapter file and return (chapter_num, title, content)."""
    try:
        text = fp.read_text(encoding="utf-8-sig").strip()
    except UnicodeDecodeError:
        text = fp.read_text(encoding="gbk", errors="replace").strip()
        if "\ufffd" in text and warn:
            warn(f"{fp.name}: \u7f16\u7801\u5f02\u5e38\uff0c\u90e8\u5206\u5185\u5bb9\u53ef\u80fd\u635f\u574f")

    lines = text.split("\n")
    heading = None
    content_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            content_start = i + 1
            break

    content = "\n".join(lines[content_start:]).strip()

    chapter_num = extract_chapter_num(fp.stem)
    if chapter_num is None and heading:
        chapter_num = extract_chapter_num(heading)

    title = strip_chapter_prefix(heading) if heading else strip_chapter_prefix(fp.stem)
    if not title:
        title = fp.stem

    return chapter_num, title, content


def get_md_files(directory: Path, warn: WarnFn | None = None) -> list[Path]:
    """Return md/txt files from root and one-level subdirectories."""
    exts = (".md", ".txt")
    files: list[Path] = []
    subdirs: list[Path] = []

    for item in directory.iterdir():
        if item.is_dir():
            subdirs.append(item)
        elif item.is_file() and item.suffix.lower() in exts:
            files.append(item)

    files.sort(key=natural_sort_key)
    subdirs.sort(key=natural_sort_key)

    for sub in subdirs:
        try:
            sub_files = [f for f in sub.iterdir() if f.is_file() and f.suffix.lower() in exts]
        except OSError:
            if warn:
                warn(f"\u65e0\u6cd5\u8bbf\u95ee\u5b50\u6587\u4ef6\u5939: {sub.name}")
            continue
        sub_files.sort(key=natural_sort_key)
        files.extend(sub_files)

    return files


def strip_md_formatting(text: str) -> str:
    """Remove markdown markers and keep readable plain text paragraphs."""
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}(.*?)_{1,3}", r"\1", text)
    text = re.sub(r"~~(.*?)~~", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"^\s*[-*+]\s+\[[ xX]\]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.)]\s+\[[ xX]\]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def deduplicate_titles(
    parsed_chapters: list[tuple[str | None, str, str]],
) -> list[tuple[str | None, str, str]]:
    """Deduplicate repeated chapter titles by appending numeric suffixes."""
    title_counts = Counter(title for _, title, _ in parsed_chapters)
    dup_titles = {title for title, count in title_counts.items() if count > 1}

    if not dup_titles:
        return parsed_chapters

    used: set[str] = {title for _, title, _ in parsed_chapters if title not in dup_titles}
    seen: dict[str, int] = {}
    result: list[tuple[str | None, str, str]] = []

    for chapter_num, title, content in parsed_chapters:
        if title not in dup_titles:
            result.append((chapter_num, title, content))
            continue

        suffix = chapter_num if chapter_num else str(seen.get(title, 1))
        new_title = f"{title}\uff08{suffix}\uff09"
        seen[title] = seen.get(title, 1) + 1
        while new_title in used:
            new_title = f"{title}\uff08{seen[title]}\uff09"
            seen[title] += 1

        used.add(new_title)
        result.append((chapter_num, new_title, content))

    return result
