import unittest

from fanqie_core.chapter_match import match_chapters
from fanqie_core.daily_limit import is_daily_limit_text
from fanqie_core.schedule_rules import compute_schedule, validate_times
from fanqie_core.volume_rules import resolve_new_chapter_volume, resolve_volume_name


class CoreModulesTests(unittest.TestCase):
    def test_volume_rules_module(self):
        cfg = {
            "default_new_chapter_volume": "1",
            "new_chapter_volume_rules": [{"min_chapter": 30, "volume": "第二卷：京城见锋"}],
        }
        volumes = ["第一卷：默认", "第二卷：京城见锋"]
        self.assertEqual(resolve_new_chapter_volume("1", cfg, volumes=volumes), "第一卷：默认")
        self.assertEqual(resolve_new_chapter_volume("30", cfg, volumes=volumes), "第二卷：京城见锋")
        self.assertEqual(resolve_volume_name("第二卷", volumes), "第二卷：京城见锋")

    def test_daily_limit_module(self):
        self.assertTrue(is_daily_limit_text("今日发布字数已达上限"))
        self.assertFalse(is_daily_limit_text("接口超时"))

    def test_schedule_rules_module(self):
        self.assertEqual(validate_times("20:00, 8:00, 12:00"), ["08:00", "12:00", "20:00"])
        schedule = compute_schedule(3, "2026-04-08", "08:00,12:00", 2)
        self.assertEqual(schedule[0], ("2026-04-08", "08:00"))
        self.assertEqual(schedule[1], ("2026-04-08", "12:00"))
        self.assertEqual(schedule[2], ("2026-04-09", "08:00"))

    def test_chapter_match_module(self):
        local = [
            ("1", "开局", "a"),
            ("2", "转折", "b"),
            ("3", "收束", "c"),
        ]
        platform = [
            {"chapterNum": 1, "title": "开局", "status": "已发布"},
            {"chapterNum": 2, "title": "转折", "status": "草稿"},
            {"chapterNum": 3, "title": "收束", "status": "待发布"},
        ]
        matched, unmatched = match_chapters(local, platform, exclude_draft=True)
        self.assertEqual([m[2] for m in matched], [1, 3])
        self.assertEqual([u[1] for u in unmatched], ["2"])


if __name__ == "__main__":
    unittest.main()
