"""Chapter-manage page operations extracted from upload orchestration."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

WaitManageTableReadyFn = Callable[[object, int | None], Awaitable[dict]]
SelectVolumeFn = Callable[[object, str], Awaitable[bool]]
GoToManagePageNumberFn = Callable[[object, int], Awaitable[bool]]
ScanManageRowFn = Callable[[object, str, object], Awaitable[dict]]
ClickManageRowActionFn = Callable[[object, str, object], Awaitable[bool]]


async def wait_manage_table_ready(
    page,
    timeout_ms: int | None = None,
    *,
    browser_timeout: int,
) -> dict:
    timeout_ms = timeout_ms or browser_timeout
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        state = await page.evaluate(
            """() => {
                const rows = [...document.querySelectorAll('tr')];
                let rowCount = 0;
                let firstTitle = '';
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 2) continue;
                    const title = cells[0].textContent.trim();
                    if (!title) continue;
                    rowCount++;
                    if (!firstTitle) firstTitle = title;
                }
                const bodyText = document.body?.innerText || '';
                const hasEmpty = /????|????|?????|????/.test(bodyText);
                const activePage = document.querySelector('li.arco-pagination-item-active')
                    ?.textContent?.trim() || '1';
                const titleSig = rows
                    .map(row => {
                        const cells = row.querySelectorAll('td');
                        if (cells.length < 2) return '';
                        return cells[0].textContent.trim();
                    })
                    .filter(Boolean)
                    .join('||');
                return { rowCount, firstTitle, hasEmpty, activePage, titleSig };
            }"""
        )
        if state.get("rowCount", 0) > 0 or state.get("hasEmpty"):
            return state
        await page.wait_for_timeout(300)

    return await page.evaluate(
        """() => {
            const bodyText = document.body?.innerText || '';
            return {
                rowCount: 0,
                firstTitle: '',
                hasEmpty: /????|????|?????|????/.test(bodyText),
                activePage: document.querySelector('li.arco-pagination-item-active')
                    ?.textContent?.trim() || '1',
                titleSig: [...document.querySelectorAll('tr')]
                    .map(row => {
                        const cells = row.querySelectorAll('td');
                        if (cells.length < 2) return '';
                        return cells[0].textContent.trim();
                    })
                    .filter(Boolean)
                    .join('||'),
            };
        }"""
    )


async def go_to_next_manage_page(
    page,
    *,
    wait_manage_table_ready_fn: WaitManageTableReadyFn,
    browser_timeout: int,
    logger,
) -> bool:
    state = await page.evaluate(
        """() => {
            const activePage = document.querySelector('li.arco-pagination-item-active')
                ?.textContent?.trim() || '1';
            const firstTitle = document.querySelector('tr td')?.textContent?.trim() || '';
            const titleSig = [...document.querySelectorAll('tr')]
                .map(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 2) return '';
                    return cells[0].textContent.trim();
                })
                .filter(Boolean)
                .join('||');
            const nextBtn = document.querySelector(
                'li.arco-pagination-item-next:not(.arco-pagination-item-disabled)')
                || document.querySelector("button[aria-label='next'], .next-page");
            const disabled = !nextBtn || nextBtn.disabled || nextBtn.classList.contains('disabled');
            if (disabled) return { clicked: false, activePage, firstTitle, titleSig };
            return { clicked: true, activePage, firstTitle, titleSig };
        }"""
    )
    if not state.get("clicked"):
        return False

    next_btn = page.locator("li.arco-pagination-item-next:not(.arco-pagination-item-disabled)").first
    if await next_btn.count() == 0:
        next_btn = page.locator("button[aria-label='next'], .next-page").first
    if await next_btn.count() == 0:
        return False
    await next_btn.click()

    prev_page = state.get("activePage", "1")
    prev_title = state.get("firstTitle", "")
    prev_sig = state.get("titleSig", "")
    deadline = asyncio.get_event_loop().time() + max(browser_timeout / 1000, 8)
    while asyncio.get_event_loop().time() < deadline:
        cur = await wait_manage_table_ready_fn(page, 1200)
        cur_page = str(cur.get("activePage", "1"))
        cur_title = str(cur.get("firstTitle", "") or "")
        cur_sig = str(cur.get("titleSig", "") or "")
        if cur_page != prev_page and cur_sig and cur_sig != prev_sig:
            logger.info(f"  ????: {prev_page} -> {cur_page} | ??: {cur_title or '[?]'}")
            return True
        if cur_page != prev_page and cur_title and cur_title != prev_title:
            logger.info(f"  ????: {prev_page} -> {cur_page} | ??: {cur_title or '[?]'}")
            return True
        if cur_page != prev_page and cur.get("hasEmpty"):
            logger.info(f"  ????: {prev_page} -> {cur_page} | ??: [?]")
            return True
        await page.wait_for_timeout(300)

    logger.warning(
        f"  ??????????: ?? {prev_page} -> {cur_page} | ???? {cur_title or '[?]'}"
    )
    return False


async def go_to_manage_page_number(
    page,
    target_page: int,
    *,
    wait_manage_table_ready_fn: WaitManageTableReadyFn,
    browser_timeout: int,
    logger,
) -> bool:
    current = await wait_manage_table_ready_fn(page, 2000)
    current_page = str(current.get("activePage", "1") or "1")
    current_sig = str(current.get("titleSig", "") or "")
    current_title = str(current.get("firstTitle", "") or "")
    if str(target_page) == current_page and (current_sig or current.get("hasEmpty")):
        return True

    clicked = await page.evaluate(
        """(targetPage) => {
            const items = [...document.querySelectorAll('li.arco-pagination-item')]
                .filter(el => (el.textContent || '').trim() === String(targetPage) && el.offsetParent !== null);
            const pager = items[items.length - 1];
            if (!pager) return false;
            pager.click();
            return true;
        }""",
        str(target_page),
    )
    if not clicked:
        logger.warning(f"  ????{target_page}?????")
        return False

    deadline = asyncio.get_event_loop().time() + max(browser_timeout / 1000, 10)
    last_state = current
    while asyncio.get_event_loop().time() < deadline:
        state = await wait_manage_table_ready_fn(page, 1200)
        last_state = state
        active_page = str(state.get("activePage", "1") or "1")
        title_sig = str(state.get("titleSig", "") or "")
        first_title = str(state.get("firstTitle", "") or "")
        has_empty = bool(state.get("hasEmpty"))
        content_changed = (
            (title_sig and title_sig != current_sig)
            or (has_empty and not current.get("hasEmpty"))
            or (current.get("hasEmpty") and not has_empty)
        )
        if active_page == str(target_page) and content_changed:
            logger.info(f"  ????{target_page}? | ??: {first_title or '[?]'}")
            return True
        await page.wait_for_timeout(300)

    logger.warning(
        f"  ???{target_page}???????: ????={last_state.get('activePage', '?')} | "
        f"?????={current_title or '[?]'} | ?????={str(last_state.get('firstTitle', '') or '') or '[?]'}"
    )
    return False


async def scan_manage_row(page, target_title: str, target_num) -> dict:
    return await page.evaluate(
        r"""([targetTitle, targetNum]) => {
            const result = {
                found: false,
                title: '',
                chapterNum: null,
                href: '',
                hasAction: false,
                rowCount: 0,
                titles: [],
            };
            for (const row of document.querySelectorAll('tr')) {
                const cells = row.querySelectorAll('td');
                if (cells.length < 2) continue;
                const title = (cells[0].textContent || '').trim();
                if (!title) continue;
                result.rowCount += 1;
                if (result.titles.length < 8) result.titles.push(title);
                let chapterNum = null;
                const m = title.match(/(\d+)/);
                if (m) chapterNum = parseInt(m[1], 10);
                const sameTitle = !!targetTitle && title === targetTitle;
                const sameNum = targetNum !== null && targetNum !== undefined
                    && chapterNum === Number(targetNum);
                if (!sameTitle && !sameNum) continue;
                result.found = true;
                result.title = title;
                result.chapterNum = chapterNum;

                const actionCell = cells[cells.length - 1] || row;
                const hrefEl = actionCell.querySelector('a[href*="/publish/"], a[href*="chapter_id"], a[href]');
                if (hrefEl) {
                    result.href = hrefEl.getAttribute('href') || '';
                    result.hasAction = true;
                    return result;
                }
                const clickEl = actionCell.querySelector(
                    'button, [role="button"], svg, i[class], span[class*="icon"], a'
                );
                if (clickEl) result.hasAction = true;
                return result;
            }
            return result;
        }""",
        [target_title, target_num],
    )


async def click_manage_row_action(
    page,
    target_title: str,
    target_num,
    *,
    browser_timeout: int,
    logger,
) -> bool:
    clicked = await page.evaluate(
        r"""([targetTitle, targetNum]) => {
            for (const row of document.querySelectorAll('tr')) {
                const cells = row.querySelectorAll('td');
                if (cells.length < 2) continue;
                const title = (cells[0].textContent || '').trim();
                if (!title) continue;
                let chapterNum = null;
                const m = title.match(/(\d+)/);
                if (m) chapterNum = parseInt(m[1], 10);
                const sameTitle = !!targetTitle && title === targetTitle;
                const sameNum = targetNum !== null && targetNum !== undefined
                    && chapterNum === Number(targetNum);
                if (!sameTitle && !sameNum) continue;

                const actionCell = cells[cells.length - 1] || row;
                const el = actionCell.querySelector(
                    'a[href], button, [role="button"], svg, i[class], span[class*="icon"], a'
                );
                if (!el) return false;
                const target = el.closest('a,button,[role="button"]') || el;
                target.dispatchEvent(new MouseEvent('click', {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                }));
                return true;
            }
            return false;
        }""",
        [target_title, target_num],
    )
    if not clicked:
        return False

    deadline = asyncio.get_event_loop().time() + max(browser_timeout / 1000, 10)
    while asyncio.get_event_loop().time() < deadline:
        if "chapter-manage" not in page.url:
            return True
        try:
            if await page.locator(".ProseMirror").count() > 0:
                return True
        except Exception:
            pass
        await page.wait_for_timeout(300)

    logger.warning("  ??????????????????????")
    return False


async def resolve_edit_url_from_manage(
    page,
    book_id: str,
    platform_ch: dict,
    *,
    chapter_manage_url_tpl: str,
    base_url: str,
    select_volume_fn: SelectVolumeFn,
    go_to_manage_page_number_fn: GoToManagePageNumberFn,
    scan_manage_row_fn: ScanManageRowFn,
    click_manage_row_action_fn: ClickManageRowActionFn,
    logger,
) -> str | None:
    chapter_manage_url = chapter_manage_url_tpl.format(book_id=book_id)
    await page.goto(chapter_manage_url)
    await page.wait_for_load_state("networkidle")

    volume_text = str(platform_ch.get("volumeText", "") or "").strip()
    if volume_text:
        ok = await select_volume_fn(page, volume_text)
        if not ok:
            logger.warning(f"  ?????????: {volume_text}")
            return None

    target_title = str(platform_ch.get("title", "") or "").strip()
    target_num = platform_ch.get("chapterNum")
    guessed_page = int(platform_ch.get("pageIndex") or 1)
    total_pages = await page.evaluate(
        """() => Math.max(
            1,
            ...[...document.querySelectorAll('li.arco-pagination-item')]
                .map(el => parseInt((el.textContent || '').trim(), 10))
                .filter(n => !Number.isNaN(n))
        )"""
    )
    pages_to_try: list[int] = []
    if 1 <= guessed_page <= total_pages:
        pages_to_try.append(guessed_page)
    for p in range(1, total_pages + 1):
        if p not in pages_to_try:
            pages_to_try.append(p)

    last_meta = None
    for page_no in pages_to_try:
        ok = await go_to_manage_page_number_fn(page, page_no)
        if not ok:
            continue
        await page.wait_for_timeout(500)
        meta = await scan_manage_row_fn(page, target_title, target_num)
        last_meta = meta
        if not meta.get("found"):
            logger.info(
                f"  ?{page_no}????????: ?{target_num}? {target_title} | "
                f"?????: {', '.join(meta.get('titles') or []) or '[?]'}"
            )
            continue

        href = str(meta.get("href", "") or "").strip()
        if href:
            if href.startswith("/"):
                return base_url + href
            return href

        if meta.get("hasAction") and await click_manage_row_action_fn(page, target_title, target_num):
            return "__ALREADY_OPENED__"

        logger.warning(
            f"  ??????????????????: ?{target_num}? {meta.get('title') or target_title}"
        )
        return None

    logger.warning(
        f"  ?????????????: ?{target_num}? {target_title} | "
        f"??????: {', '.join((last_meta or {}).get('titles') or []) or '[?]'}"
    )
    return None


__all__ = [
    "click_manage_row_action",
    "go_to_manage_page_number",
    "go_to_next_manage_page",
    "resolve_edit_url_from_manage",
    "scan_manage_row",
    "wait_manage_table_ready",
]
