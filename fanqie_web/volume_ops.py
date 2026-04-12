"""Volume-related web operations for chapter/manage pages."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable


async def detect_volumes(page, *, logger, detect_volumes_js: str) -> dict:
    """Detect volume selector on chapter manage page."""
    try:
        info = await page.evaluate(detect_volumes_js)
        vols = info.get("volumes", []) or []
        if vols:
            logger.info(
                "?????: " + " | ".join(
                    f"{v.get('text', '')}{' [??]' if v.get('isActive') else ''}"
                    for v in vols
                )
            )
        else:
            logger.info("??????????????")
        return info
    except Exception as e:
        logger.debug(f"???????: {e}")
        return {"hasVolumes": False, "volumes": [], "currentVolume": ""}


async def select_volume(
    page,
    volume_text: str,
    *,
    logger,
    detect_volumes_js: str,
    resolve_volume_name: Callable,
    wait_manage_table_ready: Callable[..., Awaitable[dict]],
    browser_timeout: int,
) -> bool:
    """Select target volume on chapter manage page and wait page stable."""
    try:
        info = await detect_volumes(
            page,
            logger=logger,
            detect_volumes_js=detect_volumes_js,
        )
        resolved_text = resolve_volume_name(
            volume_text,
            info.get("volumes", []),
            info.get("currentVolume", ""),
        )
        if resolved_text != volume_text:
            logger.info(f"  ???????: {volume_text} -> {resolved_text}")

        prev_state = await wait_manage_table_ready(page, 2000)
        prev_title = str(prev_state.get("firstTitle", "") or "")

        select_el = page.locator(
            ".chapter-select-left .serial-select.byte-select:not(.chapter-status-select)"
        ).first
        if await select_el.count() == 0:
            logger.warning("  ???????")
            return False

        await select_el.click()
        option = page.locator(
            ".byte-select-option.chapter-select-option",
            has_text=resolved_text,
        ).first
        if await option.count() == 0:
            logger.warning(f"  ????: {volume_text}")
            await select_el.click()
            return False
        await option.click()
        await page.wait_for_timeout(1200)

        page_one = page.locator("li.arco-pagination-item", has_text="1").first
        if await page_one.count() > 0:
            active_cls = await page_one.get_attribute("class") or ""
            if "arco-pagination-item-active" not in active_cls:
                await page_one.click()
                await page.wait_for_timeout(1200)

        deadline = asyncio.get_event_loop().time() + max(browser_timeout / 1000, 8)
        state = {}
        while asyncio.get_event_loop().time() < deadline:
            state = await page.evaluate(
                """(targetText) => {
                    const selectEl = document.querySelector(
                        '.chapter-select-left .serial-select.byte-select:not(.chapter-status-select)');
                    const currentVolume = selectEl
                        ?.querySelector('.byte-select-view-value')
                        ?.textContent?.trim() || '';
                    const firstTitle = document.querySelector('tr td')?.textContent?.trim() || '';
                    const bodyText = document.body?.innerText || '';
                    const hasEmpty = /????|????|?????|????/.test(bodyText);
                    const activePage = document.querySelector('li.arco-pagination-item-active')
                        ?.textContent?.trim() || '';
                    return { currentVolume, firstTitle, hasEmpty, activePage, matches: currentVolume === targetText };
                }""",
                resolved_text,
            )
            cur_title = str(state.get("firstTitle", "") or "")
            if state.get("matches") and (
                state.get("hasEmpty") or (cur_title and cur_title != prev_title) or state.get("activePage") == "1"
            ):
                break
            await page.wait_for_timeout(300)
        logger.info(
            f"  ????: {resolved_text} | ?????: {state.get('currentVolume', '')} | ??: {state.get('activePage', '?')} | ??: {str(state.get('firstTitle', '') or '') or '[?]'}"
        )
        return bool(state.get("matches"))
    except Exception as e:
        logger.warning(f"?????: {e}")
        return False


async def detect_editor_volumes(page, *, logger, detect_editor_volumes_js: str) -> dict:
    """Detect volume selector on chapter editor page."""
    try:
        return await page.evaluate(detect_editor_volumes_js)
    except Exception as e:
        logger.debug(f"??????????: {e}")
        return {"hasVolumes": False, "volumes": [], "currentVolume": ""}


async def select_editor_volume(
    page,
    volume_text: str,
    *,
    logger,
    resolve_volume_name: Callable,
    detect_editor_volumes_js: str,
    select_editor_volume_js: str,
) -> bool:
    """Select target volume on chapter editor page."""
    try:
        info = await detect_editor_volumes(
            page,
            logger=logger,
            detect_editor_volumes_js=detect_editor_volumes_js,
        )
        resolved_text = resolve_volume_name(
            volume_text,
            info.get("volumes", []),
            info.get("currentVolume", ""),
        )
        if resolved_text != volume_text:
            logger.info(f"  ???????: {volume_text} -> {resolved_text}")

        current_volume = str(info.get("currentVolume", "") or "")
        if current_volume == resolved_text and not info.get("hasVolumes"):
            logger.info(f"  ?????????????: {resolved_text}")
            return True

        ok = await page.evaluate(select_editor_volume_js, resolved_text)
        if ok:
            await page.wait_for_timeout(1000)
            logger.info(f"  ??????: {resolved_text}")
        else:
            logger.warning(f"  ????????: {volume_text}")
        return ok
    except Exception as e:
        logger.warning(f"?????????: {e}")
        return False


__all__ = [
    "detect_volumes",
    "select_volume",
    "detect_editor_volumes",
    "select_editor_volume",
]
