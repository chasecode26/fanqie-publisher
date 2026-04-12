"""核心纯逻辑模块（可被 GUI/CLI 共用）。"""

from .chapter_text import (
    deduplicate_titles,
    extract_chapter_num,
    get_md_files,
    natural_sort_key,
    parse_md_file,
    strip_chapter_prefix,
    strip_md_formatting,
)
from .daily_limit import (
    DAILY_LIMIT_HINTS,
    DailyLimitReached,
    daily_limit_stop_message,
    is_daily_limit_exception,
    is_daily_limit_text,
)
from .chapter_match import match_chapters
from .schedule_rules import compute_schedule, validate_times
from .volume_rules import resolve_new_chapter_volume, resolve_volume_name

__all__ = [
    "DAILY_LIMIT_HINTS",
    "DailyLimitReached",
    "deduplicate_titles",
    "daily_limit_stop_message",
    "extract_chapter_num",
    "get_md_files",
    "is_daily_limit_exception",
    "is_daily_limit_text",
    "natural_sort_key",
    "parse_md_file",
    "compute_schedule",
    "match_chapters",
    "resolve_new_chapter_volume",
    "resolve_volume_name",
    "strip_chapter_prefix",
    "strip_md_formatting",
    "validate_times",
]
