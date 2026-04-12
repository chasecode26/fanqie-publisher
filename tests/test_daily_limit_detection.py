import unittest

from fanqie_upload import (
    DailyLimitReached,
    daily_limit_stop_message,
    is_daily_limit_exception,
    is_daily_limit_text,
)


class DailyLimitDetectionTests(unittest.TestCase):
    def test_daily_limit_text_match(self):
        self.assertTrue(is_daily_limit_text("提示：已到达当日发布字数上限，请明日再试"))

    def test_daily_limit_text_not_match(self):
        self.assertFalse(is_daily_limit_text("网络超时，请稍后重试"))

    def test_daily_limit_exception_by_type(self):
        self.assertTrue(is_daily_limit_exception(DailyLimitReached("x")))

    def test_daily_limit_exception_by_message(self):
        self.assertTrue(is_daily_limit_exception(RuntimeError("今日发布字数已达上限，无法提交")))

    def test_daily_limit_stop_message_not_empty(self):
        self.assertIn("当日", daily_limit_stop_message())


if __name__ == "__main__":
    unittest.main()
