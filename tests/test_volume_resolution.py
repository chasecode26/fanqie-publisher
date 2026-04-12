import unittest

from fanqie_upload import resolve_new_chapter_volume, resolve_volume_name


class VolumeResolutionTests(unittest.TestCase):
    def test_numeric_config_maps_to_first_volume(self):
        volumes = ["第一卷：默认", "第二卷：回归"]
        self.assertEqual(resolve_volume_name("1", volumes), "第一卷：默认")

    def test_prefix_config_maps_to_full_volume_name(self):
        volumes = ["第一卷：默认", "第二卷：回归"]
        self.assertEqual(resolve_volume_name("第二卷", volumes), "第二卷：回归")

    def test_current_volume_can_resolve_single_volume_index(self):
        self.assertEqual(
            resolve_volume_name("1", [], current_volume="第一卷：默认"),
            "第一卷：默认",
        )

    def test_resolve_new_chapter_volume_uses_available_volumes(self):
        cfg = {
            "default_new_chapter_volume": "1",
            "new_chapter_volume_rules": [
                {"min_chapter": 30, "volume": "2"},
            ],
        }
        volumes = ["第一卷：默认", "第二卷：回归"]
        self.assertEqual(
            resolve_new_chapter_volume("5", cfg, volumes=volumes, current_volume="第一卷：默认"),
            "第一卷：默认",
        )
        self.assertEqual(
            resolve_new_chapter_volume("30", cfg, volumes=volumes, current_volume="第一卷：默认"),
            "第二卷：回归",
        )


if __name__ == "__main__":
    unittest.main()
