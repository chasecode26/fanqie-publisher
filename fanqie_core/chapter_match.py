"""本地章节与平台章节匹配纯逻辑。"""

from __future__ import annotations

import re


def _chapter_status_priority(ch: dict) -> int:
    status = str((ch or {}).get("status", "") or "")
    if "已发布" in status:
        return 0
    if "待发布" in status:
        return 1
    if "审核中" in status:
        return 2
    if "已拒绝" in status:
        return 3
    if "草稿" in status:
        return 9
    return 5


def _is_draft_status(ch: dict) -> bool:
    status = str((ch or {}).get("status", "") or "")
    return "草稿" in status


def _normalize_title(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def match_chapters(
    local_parsed: list[tuple],
    platform_chapters: list[dict],
    *,
    number_only: bool = False,
    exclude_draft: bool = False,
) -> tuple[list, list]:
    """匹配本地文件与平台章节。"""
    platform_map: dict[object, dict] = {}
    for ch in platform_chapters:
        num = ch.get("chapterNum")
        if num is None:
            continue
        key = num if number_only else (num, _normalize_title(ch.get("title", "")))
        if key not in platform_map or _chapter_status_priority(ch) < _chapter_status_priority(platform_map[key]):
            platform_map[key] = ch

    matched = []
    unmatched = []
    for i, (num, title, content) in enumerate(local_parsed):
        int_num = int(num) if num else None
        key = int_num if number_only else ((int_num, _normalize_title(title)) if int_num is not None else None)
        if key is not None and key in platform_map and not (exclude_draft and _is_draft_status(platform_map[key])):
            matched.append((i, platform_map[key], int_num, title, content))
        else:
            unmatched.append((i, num, title))
    return matched, unmatched

