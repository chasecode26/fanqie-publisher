"""当日发布字数上限相关的纯逻辑。"""

from __future__ import annotations


class DailyLimitReached(RuntimeError):
    """当日发布字数已达平台上限，无法继续发布。"""


DAILY_LIMIT_HINTS = (
    "已到达当日发布字数上限",
    "当日发布字数已达上限",
    "今日发布字数已达上限",
    "今日发布字数已用完",
    "当日发布上限",
)


def is_daily_limit_text(text: str | None) -> bool:
    plain = str(text or "")
    if not plain:
        return False
    return any(hint in plain for hint in DAILY_LIMIT_HINTS)


def daily_limit_stop_message() -> str:
    return "已到达当日发布字数上限，已停止后续发布与重试"


def is_daily_limit_exception(exc: BaseException) -> bool:
    if isinstance(exc, DailyLimitReached):
        return True
    return is_daily_limit_text(str(exc))

