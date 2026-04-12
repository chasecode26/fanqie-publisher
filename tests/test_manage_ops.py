import unittest

from fanqie_web.manage_ops import resolve_edit_url_from_manage


class _DummyLogger:
    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


class _FakePage:
    def __init__(self, total_pages: int = 1):
        self.total_pages = total_pages
        self.gotos: list[str] = []
        self.wait_states: list[str] = []

    async def goto(self, url: str):
        self.gotos.append(url)

    async def wait_for_load_state(self, state: str):
        self.wait_states.append(state)

    async def evaluate(self, script: str):
        if "Math.max(" in script:
            return self.total_pages
        raise AssertionError("Unexpected evaluate call")

    async def wait_for_timeout(self, _ms: int):
        return None

class ResolveEditUrlFromManageTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_absolute_url_when_href_is_relative(self):
        page = _FakePage(total_pages=3)
        logger = _DummyLogger()
        tried_pages: list[int] = []

        async def select_volume_fn(_page, _volume_text):
            return True

        async def go_to_manage_page_number_fn(_page, page_no: int):
            tried_pages.append(page_no)
            return True

        async def scan_manage_row_fn(_page, _title, _num):
            if tried_pages[-1] == 2:
                return {"found": False, "titles": ["??A"]}
            return {
                "found": True,
                "title": "?2?",
                "href": "/main/writer/123/publish/456/",
                "hasAction": True,
                "titles": ["?2?"],
            }

        async def click_manage_row_action_fn(_page, _title, _num):
            return False

        result = await resolve_edit_url_from_manage(
            page,
            "123",
            {"title": "?2?", "chapterNum": 2, "pageIndex": 2},
            chapter_manage_url_tpl="https://site/chapter-manage/{book_id}",
            base_url="https://site",
            select_volume_fn=select_volume_fn,
            go_to_manage_page_number_fn=go_to_manage_page_number_fn,
            scan_manage_row_fn=scan_manage_row_fn,
            click_manage_row_action_fn=click_manage_row_action_fn,
            logger=logger,
        )

        self.assertEqual(result, "https://site/main/writer/123/publish/456/")
        self.assertEqual(page.gotos, ["https://site/chapter-manage/123"])
        self.assertEqual(page.wait_states, ["networkidle"])
        self.assertEqual(tried_pages, [2, 1])

    async def test_returns_opened_marker_when_action_click_succeeds(self):
        page = _FakePage(total_pages=1)
        logger = _DummyLogger()

        async def select_volume_fn(_page, _volume_text):
            return True

        async def go_to_manage_page_number_fn(_page, _page_no):
            return True

        async def scan_manage_row_fn(_page, _title, _num):
            return {
                "found": True,
                "title": "?3?",
                "href": "",
                "hasAction": True,
                "titles": ["?3?"],
            }

        async def click_manage_row_action_fn(_page, _title, _num):
            return True

        result = await resolve_edit_url_from_manage(
            page,
            "123",
            {"title": "?3?", "chapterNum": 3},
            chapter_manage_url_tpl="https://site/chapter-manage/{book_id}",
            base_url="https://site",
            select_volume_fn=select_volume_fn,
            go_to_manage_page_number_fn=go_to_manage_page_number_fn,
            scan_manage_row_fn=scan_manage_row_fn,
            click_manage_row_action_fn=click_manage_row_action_fn,
            logger=logger,
        )

        self.assertEqual(result, "__ALREADY_OPENED__")

    async def test_returns_none_when_volume_switch_fails(self):
        page = _FakePage(total_pages=1)
        logger = _DummyLogger()

        async def select_volume_fn(_page, _volume_text):
            return False

        async def go_to_manage_page_number_fn(_page, _page_no):
            raise AssertionError("should not be called")

        async def scan_manage_row_fn(_page, _title, _num):
            raise AssertionError("should not be called")

        async def click_manage_row_action_fn(_page, _title, _num):
            raise AssertionError("should not be called")

        result = await resolve_edit_url_from_manage(
            page,
            "123",
            {"title": "?1?", "chapterNum": 1, "volumeText": "???"},
            chapter_manage_url_tpl="https://site/chapter-manage/{book_id}",
            base_url="https://site",
            select_volume_fn=select_volume_fn,
            go_to_manage_page_number_fn=go_to_manage_page_number_fn,
            scan_manage_row_fn=scan_manage_row_fn,
            click_manage_row_action_fn=click_manage_row_action_fn,
            logger=logger,
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
