#!/usr/bin/env python3
"""
番茄作家 MD 批量上传工具

将本地 Markdown 文件批量上传到番茄作家平台作为小说章节。

用法:
    python fanqie_upload.py login                              登录并保存会话
    python fanqie_upload.py books                              列出你的作品
    python fanqie_upload.py upload ./chapters --book-id ID     批量上传章节(存草稿)
    python fanqie_upload.py upload ./chapters --book-id ID --publish  批量上传并发布
    python fanqie_upload.py upload ./chapters --book-id ID --schedule 2026-03-14 --per-day 3
                                                               定时发布(每天3章)

MD 文件格式:
    文件名: 001_章节标题.md  或  第1章_标题.md  或  任意名称.md
    内容: 纯文本或 Markdown，第一个 # 标题可作为章节标题
    排序: 按文件名自然排序决定上传顺序
"""

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
from urllib.parse import parse_qsl, urlencode, urlparse

from fanqie_core.daily_limit import (
    DAILY_LIMIT_HINTS as _DAILY_LIMIT_HINTS,
    DailyLimitReached,
    daily_limit_stop_message,
    is_daily_limit_exception,
    is_daily_limit_text,
)
from fanqie_core.chapter_match import match_chapters
from fanqie_core.chapter_text import (
    deduplicate_titles,
    extract_chapter_num as _extract_chapter_num,
    get_md_files,
    parse_md_file,
    strip_md_formatting,
)
from fanqie_core.schedule_rules import compute_schedule, validate_times as _validate_times
from fanqie_core.volume_rules import (
    resolve_new_chapter_volume,
    resolve_volume_name,
)
from fanqie_web.js_snippets import (
    BOOKS_JS,
    DETECT_EDITOR_VOLUMES_JS,
    DETECT_VOLUMES_JS,
    LAST_PUBLISH_JS,
    SELECT_EDITOR_VOLUME_JS,
    SELECT_VOLUME_JS,
)
from fanqie_web.volume_ops import (
    detect_editor_volumes as _detect_editor_volumes_impl,
    detect_volumes as _detect_volumes_impl,
    select_editor_volume as _select_editor_volume_impl,
    select_volume as _select_volume_impl,
)
from fanqie_web.manage_ops import (
    click_manage_row_action as _click_manage_row_action_impl,
    go_to_manage_page_number as _go_to_manage_page_number_impl,
    go_to_next_manage_page as _go_to_next_manage_page_impl,
    resolve_edit_url_from_manage as _resolve_edit_url_from_manage_impl,
    scan_manage_row as _scan_manage_row_impl,
    wait_manage_table_ready as _wait_manage_table_ready_impl,
)

try:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
except ImportError:
    print("请先安装依赖:")
    print("  pip install playwright")
    print("  playwright install chromium")
    sys.exit(1)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
BASE_URL = "https://fanqienovel.com"
ZONE_URL = f"{BASE_URL}/writer/zone/"

# 持久化文件（与脚本同目录）
SCRIPT_DIR = Path(__file__).parent
AUTH_FILE = SCRIPT_DIR / ".auth_state.json"
GUI_STATE_FILE = SCRIPT_DIR / ".gui_state.json"
CONFIG_FILE = SCRIPT_DIR / "config.json"

# 页面路径
BOOK_MANAGE_URL = f"{BASE_URL}/main/writer/book-manage"
NEW_CHAPTER_URL_TPL = BASE_URL + "/main/writer/{book_id}/publish/?enter_from=newchapter_1"
CHAPTER_MANAGE_URL_TPL = BASE_URL + "/main/writer/chapter-manage/{book_id}"

# 默认配置
DEFAULT_CONFIG = {
    "delay_between_chapters": 3,   # 章节之间等待秒数
    "headless": False,             # 是否无头模式
    "max_retries": 2,              # 单章失败最大重试次数
    "default_mode": "schedule",    # GUI 默认发布模式
    "default_per_day": 2,          # GUI 默认每天章数
    "default_time": "08:00",       # GUI 默认发布时间（支持逗号分隔多时间）
    "browser_timeout": 15000,      # 浏览器操作超时 (ms)
    "default_new_chapter_volume": "",  # 新建章节默认分卷，如 "第二卷：回归"
    "new_chapter_volume_rules": [],    # 按章节号自动切卷规则
}

# 平台修饰键 (macOS = Meta/Cmd, 其他 = Control)
_MOD_KEY = "Meta" if sys.platform == "darwin" else "Control"
_browser_timeout = DEFAULT_CONFIG["browser_timeout"]  # 模块级超时(ms)


def _safe_filename(name: str, max_len: int = 40) -> str:
    """移除 Windows 文件名非法字符并截断。"""
    return re.sub(r'[\\/:*?"<>|\r\n]', '_', name)[:max_len]


LOG_FILE = SCRIPT_DIR / "fanqie_error.log"

logger = logging.getLogger("fanqie")


def setup_logging(log_file=None, level=logging.INFO):
    """初始化日志: 控制台 + 可选的滚动文件日志。"""
    if logger.handlers:
        return
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if log_file:
        fh = RotatingFileHandler(
            str(log_file), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        fh.setLevel(logging.INFO)
        logger.addHandler(fh)


async def _check_daily_limit(page):
    """检测平台"当日发布字数上限"提示，若存在则抛出 DailyLimitReached。"""
    try:
        body_text = await page.evaluate("() => document.body?.innerText || ''")
        if is_daily_limit_text(body_text):
            raise DailyLimitReached(daily_limit_stop_message())
        for hint in _DAILY_LIMIT_HINTS:
            tip = page.locator(f"text={hint}")
            if await tip.count() > 0:
                raise DailyLimitReached(daily_limit_stop_message())
    except DailyLimitReached:
        raise
    except Exception:
        logger.debug("_check_daily_limit 检测时出错(非致命)", exc_info=True)


# ---------------------------------------------------------------------------
# 配置管理
# ---------------------------------------------------------------------------
def load_config() -> dict:
    global _browser_timeout
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, ValueError):
            logger.warning("config.json 格式错误，使用默认配置")
    val = cfg.get("browser_timeout", DEFAULT_CONFIG["browser_timeout"])
    if not isinstance(val, (int, float)) or val <= 0:
        logger.warning(f"browser_timeout 无效({val})，使用默认值 {DEFAULT_CONFIG['browser_timeout']}")
        val = DEFAULT_CONFIG["browser_timeout"]
    _browser_timeout = int(val)
    return cfg


def get_browser_timeout() -> int:
    """返回当前 browser_timeout 值（ms），供外部模块使用。"""
    return _browser_timeout


def _looks_like_chapter_api(url: str) -> bool:
    low = (url or "").lower()
    if not any(k in low for k in ("chapter", "catalog", "directory", "list", "page")):
        return False
    return any(k in low for k in ("author", "writer", "fanqie", "novel"))


def attach_chapter_network_logger(page, *, tag: str = ""):
    """记录章节管理页相关 XHR/Fetch 响应，便于定位真实数据接口。"""
    if not hasattr(page, "_chapter_api_cache"):
        page._chapter_api_cache = {}
    if not hasattr(page, "_volume_name_to_id"):
        page._volume_name_to_id = {}

    async def _log_response(resp):
        try:
            req = resp.request
            if req.resource_type not in ("xhr", "fetch"):
                return
            url = resp.url
            if not _looks_like_chapter_api(url):
                return
            parsed = urlparse(url)
            qs = dict(parse_qsl(parsed.query))
            body = None
            ctype = (resp.headers or {}).get("content-type", "")
            if "json" in ctype:
                try:
                    body = await resp.json()
                except Exception:
                    body = await resp.text()
            else:
                body = await resp.text()
            preview = body
            if isinstance(body, dict):
                preview = {
                    "keys": list(body.keys())[:12],
                    "code": body.get("code"),
                    "message": body.get("message") or body.get("msg"),
                    "data_keys": list((body.get("data") or {}).keys())[:12] if isinstance(body.get("data"), dict) else None,
                }
                path = parsed.path
                if "/api/author/chapter/chapter_list/" in path:
                    page_index = int(qs.get("page_index", "0") or 0)
                    volume_id = str(qs.get("volume_id", "") or "")
                    page._chapter_api_cache[(volume_id, page_index)] = {
                        "qs": qs,
                        "body": body,
                    }
                    items = (((body.get("data") or {}).get("item_list")) or [])
                    if items and isinstance(items[0], dict):
                        preview["item_keys"] = list(items[0].keys())[:20]
                        preview["item_status_sample"] = {
                            k: items[0].get(k)
                            for k in items[0].keys()
                            if "status" in str(k).lower()
                        }
                elif "/api/author/volume/volume_list/" in path:
                    volumes = (((body.get("data") or {}).get("volume_list")) or [])
                    for vol in volumes:
                        if not isinstance(vol, dict):
                            continue
                        vid = str(
                            vol.get("volume_id")
                            or vol.get("id")
                            or vol.get("item_id")
                            or ""
                        )
                        name = str(
                            vol.get("volume_name")
                            or vol.get("title")
                            or vol.get("name")
                            or ""
                        ).strip()
                        if vid and name:
                            page._volume_name_to_id[name] = vid
                    if volumes and isinstance(volumes[0], dict):
                        preview["volume_item_keys"] = list(volumes[0].keys())[:20]
            logger.info(
                f"[NET{':' + tag if tag else ''}] {resp.status} {parsed.path} qs={qs} preview={str(preview)[:500]}"
            )
        except Exception as e:
            logger.debug(f"网络日志记录失败: {e}")

    def _on_response(resp):
        asyncio.create_task(_log_response(resp))

    page.on("response", _on_response)


# ---------------------------------------------------------------------------
# 浏览器操作
# ---------------------------------------------------------------------------
async def create_context(p, headless=False):
    """创建浏览器上下文，如有已保存的登录状态则加载。"""
    browser = await p.chromium.launch(headless=headless)
    try:
        if AUTH_FILE.exists():
            context = await browser.new_context(storage_state=str(AUTH_FILE))
        else:
            context = await browser.new_context()
    except Exception:
        await browser.close()
        raise
    # 授予剪贴板权限，用于可靠的粘贴操作
    await context.grant_permissions(
        ["clipboard-read", "clipboard-write"], origin=BASE_URL
    )
    return browser, context


async def save_auth(context):
    """保存当前登录状态。"""
    await context.storage_state(path=str(AUTH_FILE))


async def dismiss_overlays(page, *, prefer_continue_edit: bool = True):
    """关闭可能遮挡按钮的弹窗。返回处理到的动作标签。"""
    await page.wait_for_timeout(500)
    handled_label = ""

    # 1. “是否继续编辑”弹窗：已进入目标正文编辑页时应优先继续编辑。
    try:
        dialog_text = page.locator("text=是否继续编辑").first
        draft_text = page.locator("text=有刚刚更新的草稿").first
        has_dialog_text = (
            (await dialog_text.count() > 0 and await dialog_text.is_visible())
            or (await draft_text.count() > 0 and await draft_text.is_visible())
        )
        if has_dialog_text:
            logger.info("检测到“是否继续编辑”弹窗，尝试关闭")

            clicked = False
            labels = ("继续编辑", "取消", "放弃") if prefer_continue_edit else ("取消", "放弃", "继续编辑")
            clicked_label = await _click_visible_action(page, labels, wait_ms=800)
            if clicked_label:
                logger.info(f"已点击弹窗按钮: {clicked_label}")
                handled_label = clicked_label
                clicked = True

            for label in labels:
                if clicked:
                    break
                btn = page.locator("button", has_text=label)
                if await btn.count() == 0:
                    btn = page.locator("[role='button']", has_text=label)
                if await btn.count() == 0:
                    btn = page.locator(f"text={label}")
                if await btn.count() == 0:
                    continue

                try:
                    await btn.first.click(force=True)
                    logger.info(f"已点击弹窗按钮: {label}")
                    handled_label = label
                    clicked = True
                    break
                except Exception as e:
                    logger.debug(f"点击“{label}”失败，继续尝试其他按钮: {e}")

            if not clicked:
                try:
                    clicked_label = await page.evaluate(
                        """(labels) => {
                            const normalize = (text) => String(text || '').split(/\\s+/).filter(Boolean).join(' ').trim();
                            const visible = (el) => {
                                if (!el) return false;
                                const style = window.getComputedStyle(el);
                                if (style.display === 'none' || style.visibility === 'hidden') return false;
                                const rect = el.getBoundingClientRect();
                                return rect.width > 0 && rect.height > 0;
                            };
                            const dialogs = Array.from(document.querySelectorAll(
                                '[role="dialog"], .arco-modal, .semi-modal, [class*="modal"], [class*="dialog"]'
                            )).filter(visible);
                            const roots = dialogs.length ? dialogs : [document.body];
                            const candidates = roots.flatMap((root) => Array.from(root.querySelectorAll(
                                'button, [role="button"], .arco-btn, .semi-button, [class*="btn"], [class*="button"]'
                            ))).filter(visible);

                            for (const label of labels) {
                                const btn = candidates.find((el) => normalize(el.innerText || el.textContent || '') === label);
                                if (!btn) continue;
                                btn.scrollIntoView({ block: 'center', inline: 'center' });
                                ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach((type) => {
                                    btn.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                                });
                                return label;
                            }
                            return '';
                        }""",
                        list(labels),
                    )
                    if clicked_label:
                        logger.info(f"已通过兜底点击弹窗按钮: {clicked_label}")
                        handled_label = str(clicked_label)
                        clicked = True
                except Exception as e:
                    logger.debug(f"兜底点击“是否继续编辑”弹窗失败: {e}")

            if clicked:
                try:
                    if await dialog_text.count() > 0:
                        await dialog_text.wait_for(state="hidden", timeout=3000)
                    elif await draft_text.count() > 0:
                        await draft_text.wait_for(state="hidden", timeout=3000)
                    logger.info("“是否继续编辑”弹窗已关闭")
                except Exception:
                    await page.wait_for_timeout(1000)
                    still_visible = (
                        await dialog_text.count() > 0 and await dialog_text.is_visible()
                    ) or (
                        await draft_text.count() > 0 and await draft_text.is_visible()
                    )
                    if still_visible:
                        logger.warning("“是否继续编辑”弹窗点击后仍可见")
            else:
                logger.warning("检测到“是否继续编辑”弹窗，但未找到可点击的“继续编辑/取消/放弃”按钮")
    except Exception as e:
        logger.debug(f"关闭“是否继续编辑”弹窗失败: {e}")

    # 2. React Tour 新手引导 -> 直接用 JS 移除 DOM 节点（比逐步点击更可靠）
    try:
        await page.evaluate("""() => {
            const tour = document.getElementById('___reactour');
            if (tour) tour.remove();
            // 同时移除可能的遮罩层
            const masks = document.querySelectorAll('[class*="reactour"], [class*="mask"]');
            for (const m of masks) {
                if (m.style && (m.style.position === 'fixed' || m.style.position === 'absolute')) {
                    m.remove();
                }
            }
        }""")
    except Exception:
        pass
    return handled_label


def _normalize_ui_text(text: str | None) -> str:
    return " ".join(str(text or "").split()).strip()


async def _click_visible_action(
    page,
    labels: str | list[str] | tuple[str, ...],
    *,
    wait_ms: int = 800,
) -> str:
    """按可见文案点击动作按钮，兼容 button / role=button / Arco 按钮。"""
    if isinstance(labels, str):
        labels = [labels]
    labels = [_normalize_ui_text(label) for label in labels if _normalize_ui_text(label)]
    if not labels:
        return ""

    clicked = await page.evaluate(
        """(labels) => {
            const normalize = (text) => String(text || '').split(/\\s+/).filter(Boolean).join(' ').trim();
            const visible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const candidates = Array.from(document.querySelectorAll(
                'button, [role="button"], .arco-btn, .semi-button'
            )).filter(visible);

            for (const label of labels) {
                const exact = candidates.find((el) => normalize(el.innerText || el.textContent || '') === label);
                if (exact) {
                    exact.click();
                    return label;
                }
            }
            for (const label of labels) {
                const fuzzy = candidates.find((el) => normalize(el.innerText || el.textContent || '').includes(label));
                if (fuzzy) {
                    fuzzy.click();
                    return label;
                }
            }
            return '';
        }""",
        labels,
    )
    if clicked:
        await page.wait_for_timeout(wait_ms)
    return str(clicked or "")


async def _handle_publish_interruption_dialogs(page) -> str:
    """处理发布流程中插入的确认/检测弹窗，返回已点击的按钮文案。"""
    state = await get_publish_flow_state(page)
    body_text = str(state.get("bodyText") or "")

    if state.get("hasContinueEdit"):
        clicked = await _click_visible_action(page, ("继续编辑", "取消", "放弃"), wait_ms=800)
        if clicked:
            return clicked

    if state.get("hasSubmitConfirm"):
        clicked = await _click_visible_action(page, ("提交", "确认", "确定", "继续发布"), wait_ms=1000)
        if clicked:
            return clicked

    if state.get("hasRiskDialog"):
        # 新版弹窗为“请选择内容检测方式”，基础检测不限次数，适合自动发布。
        risk_labels = ("仅基础检测", "基础检测", "暂不处理", "跳过", "取消")
        if "全面检测" in body_text and "仅基础检测" in body_text:
            risk_labels = ("仅基础检测", "基础检测")
        clicked = await _click_visible_action(page, risk_labels, wait_ms=1200)
        if clicked:
            return clicked

    return ""


async def confirm_publish(page, *, labels: tuple[str, ...] = ("确认发布", "定时发布", "发布")):
    """确认发布/定时发布，并等待发布弹窗关闭。"""
    await _check_daily_limit(page)
    clicked_label = await _click_visible_action(page, list(labels), wait_ms=300)
    if not clicked_label:
        state = await get_publish_flow_state(page)
        raise RuntimeError(
            f"未找到确认发布按钮 | {_format_publish_flow_state(state)}"
        )

    final_label = clicked_label
    dialog_closed = False
    for _ in range(18):
        await page.wait_for_timeout(400)
        state = await get_publish_flow_state(page)
        handled = await _handle_publish_interruption_dialogs(page)
        if handled:
            logger.info(f"  检测到发布确认/检测弹窗，点击: {handled}")
            final_label = handled
            continue
        if not state.get("hasPublishSettings"):
            dialog_closed = True
            break
    if not dialog_closed:
        await page.wait_for_timeout(2000)

    await _check_daily_limit(page)
    logger.info(f"  -> 已点击{final_label}")


async def wait_for_editor_ready(page, timeout=None, *, prefer_continue_edit: bool = True):
    """等待章节编辑器加载完成。"""
    if timeout is None:
        timeout = _browser_timeout
    await page.wait_for_load_state("networkidle", timeout=timeout)
    # 等待 ProseMirror 编辑器出现
    await page.wait_for_selector(".ProseMirror", timeout=timeout)
    # 等待标题输入框出现
    await page.wait_for_selector("input[placeholder='请输入标题']", timeout=timeout)
    await page.wait_for_timeout(500)
    # 关闭弹窗/引导层
    await dismiss_overlays(page, prefer_continue_edit=prefer_continue_edit)


async def _get_word_count(page) -> int:
    """从页面顶部获取正文字数，返回整数。"""
    try:
        el = page.locator("text=正文字数")
        if await el.count() > 0:
            txt = await el.text_content()
            m = re.search(r"(\d+)", txt)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 0


async def fill_chapter(page, chapter_num: str | None, title: str, content: str):
    """
    在编辑器页面填入章节内容。

    全部通过 page.evaluate 直接操作 DOM，不使用 Playwright 的
    locator.click()/fill()，这样即使有弹窗/引导层遮挡也不会失败。
    """
    plain_content = strip_md_formatting(content)

    await page.evaluate(
        """([chapterNum, title, content]) => {
            const nativeSetter = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype, 'value'
            ).set;

            // 1. 填写章节号
            if (chapterNum) {
                const inputs = document.querySelectorAll('input');
                for (const inp of inputs) {
                    if (inp.type === 'text'
                        && inp.placeholder !== '请输入标题'
                        && inp.offsetParent !== null) {
                        nativeSetter.call(inp, chapterNum);
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                        break;
                    }
                }
            }

            // 2. 填写标题
            const titleInput = document.querySelector(
                'input[placeholder="请输入标题"]'
            );
            if (titleInput) {
                nativeSetter.call(titleInput, title);
                titleInput.dispatchEvent(new Event('input', { bubbles: true }));
                titleInput.dispatchEvent(new Event('change', { bubbles: true }));
            }

        }""",
        [chapter_num or "", title, ""],
    )

    # 继续编辑旧草稿时，编辑器可能已有内容。用真实键盘事件清空，确保平台状态被触发。
    editor = page.locator(".ProseMirror").first
    await editor.click()
    await page.wait_for_timeout(150)
    await page.keyboard.press(f"{_MOD_KEY}+a")
    await page.wait_for_timeout(150)
    await page.keyboard.press("Delete")
    await page.wait_for_timeout(200)

    await page.evaluate(
        """(content) => {
            const editor = document.querySelector('.ProseMirror');
            if (!editor) return;
            editor.focus();
            const dt = new DataTransfer();
            dt.setData('text/plain', content);
            const pasteEvt = new ClipboardEvent('paste', {
                clipboardData: dt,
                bubbles: true,
                cancelable: true,
            });
            editor.dispatchEvent(pasteEvt);
            editor.dispatchEvent(new InputEvent('input', {
                bubbles: true,
                cancelable: true,
                inputType: 'insertFromPaste',
                data: content,
            }));
            editor.dispatchEvent(new Event('change', { bubbles: true }));
            editor.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Process' }));
        }""",
        plain_content,
    )
    # 轮询等待正文写入完成（最多 5 秒）
    wc = 0
    for _ in range(10):
        await page.wait_for_timeout(500)
        wc = await _get_word_count(page)
        if wc > 0:
            break
    if wc > 0:
        logger.info(f"    正文字数 {wc}")
    else:
        raise RuntimeError("正文粘贴失败 (字数=0)，请重试")


async def save_draft(page):
    """点击存草稿按钮并等待保存完成。"""
    save_btn = page.locator("button", has_text="存草稿")
    if await save_btn.count() == 0:
        raise RuntimeError("未找到存草稿按钮")
    await save_btn.first.click()
    # 等待 "已保存" 出现
    try:
        await page.wait_for_selector("text=已保存", timeout=_browser_timeout)
    except PWTimeout:
        logger.warning("未检测到保存确认，草稿可能未保存成功")
    await page.wait_for_timeout(1000)


async def dismiss_edit_hint(page):
    """关闭编辑已发布章节时的提示弹窗: '请在发布时间前30分钟提交修改内容'。"""
    try:
        hint = page.locator("text=请在发布时间前30分钟提交修改内容")
        if await hint.count() > 0:
            btn = page.locator("button", has_text="我知道了")
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(800)
    except Exception:
        pass


async def clear_editor(page):
    """清空编辑器中的标题和正文内容（修改模式用）。"""
    await page.evaluate("""() => {
        const nativeSetter = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype, 'value'
        ).set;

        // 清空标题
        const titleInput = document.querySelector('input[placeholder="请输入标题"]');
        if (titleInput) {
            nativeSetter.call(titleInput, '');
            titleInput.dispatchEvent(new Event('input', { bubbles: true }));
            titleInput.dispatchEvent(new Event('change', { bubbles: true }));
        }

        // 清空章节号
        const inputs = document.querySelectorAll('input');
        for (const inp of inputs) {
            if (inp.type === 'text'
                && inp.placeholder !== '请输入标题'
                && inp.offsetParent !== null) {
                nativeSetter.call(inp, '');
                inp.dispatchEvent(new Event('input', { bubbles: true }));
                inp.dispatchEvent(new Event('change', { bubbles: true }));
                break;
            }
        }

        // 选中 ProseMirror 编辑器全部内容
        const editor = document.querySelector('.ProseMirror');
        if (editor) {
            editor.focus();
        }
    }""")
    # 全选并删除正文
    await page.keyboard.press(f"{_MOD_KEY}+a")
    await page.wait_for_timeout(200)
    await page.keyboard.press("Delete")
    await page.wait_for_timeout(500)


async def inspect_editor_prefill(page) -> dict:
    """读取当前编辑页预填信息，判断是否真的进入已有章节编辑。"""
    return await page.evaluate("""() => {
        const titleInput = document.querySelector('input[placeholder="请输入标题"]');
        const title = titleInput ? (titleInput.value || '').trim() : '';
        let chapterNum = '';
        for (const inp of document.querySelectorAll('input')) {
            if (inp.type === 'text'
                && inp.placeholder !== '请输入标题'
                && inp.offsetParent !== null) {
                chapterNum = (inp.value || '').trim();
                break;
            }
        }
        const editor = document.querySelector('.ProseMirror');
        const contentText = editor ? (editor.innerText || '').trim() : '';
        return {
            title,
            chapterNum,
            contentLength: contentText.length,
            hasContent: contentText.length > 0,
        };
    }""")


async def _settle_editor_before_publish(page):
    """让编辑器失焦，给平台一点时间完成内部状态同步和自动保存。"""
    try:
        await page.evaluate("""() => {
            const active = document.activeElement;
            if (active && typeof active.blur === 'function') {
                active.blur();
            }
            const editor = document.querySelector('.ProseMirror');
            if (editor) {
                editor.dispatchEvent(new Event('blur', { bubbles: true }));
            }
            document.body?.click?.();
        }""")
    except Exception:
        pass
    for _ in range(12):
        state = await page.evaluate("""() => {
            const text = document.body.innerText || '';
            return {
                saving: /保存中|正在保存|同步中|提交中/.test(text),
                saved: /已保存|保存成功/.test(text),
            };
        }""")
        if not state.get("saving"):
            break
        await page.wait_for_timeout(500)
    await page.wait_for_timeout(500)


async def _get_editor_submit_debug_state(page) -> dict:
    """采集“下一步”禁用时的编辑器状态，便于定位平台校验卡点。"""
    try:
        return await page.evaluate("""() => {
            const norm = (text) => String(text || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const titleInput = document.querySelector('input[placeholder="请输入标题"]');
            const inputs = Array.from(document.querySelectorAll('input'))
                .filter(visible)
                .map((el) => ({
                    placeholder: el.getAttribute('placeholder') || '',
                    value: el.value || '',
                    type: el.type || '',
                }))
                .slice(0, 8);
            const editor = document.querySelector('.ProseMirror');
            const next = Array.from(document.querySelectorAll('button.auto-editor-next, button, [role="button"]'))
                .filter((el) => visible(el) && norm(el.innerText || el.textContent || '').includes('下一步'))[0] || null;
            const bodyText = norm(document.body.innerText || '');
            const saveMatch = bodyText.match(/(保存中|正在保存|已保存|保存成功|未保存|同步中)/);
            return {
                title: titleInput ? titleInput.value || '' : '',
                inputs,
                editorLength: editor ? norm(editor.innerText || '').length : 0,
                saveText: saveMatch ? saveMatch[1] : '',
                nextText: next ? norm(next.innerText || next.textContent || '') : '',
                nextDisabled: next ? !!next.disabled : null,
                nextAriaDisabled: next ? next.getAttribute('aria-disabled') || '' : '',
                nextClass: next ? String(next.className || '') : '',
                nextTitle: next ? next.getAttribute('title') || '' : '',
            };
        }""")
    except Exception as e:
        return {"error": str(e)}


async def _get_next_step_button_state(page) -> dict:
    """获取当前最像主操作的“下一步”按钮状态。"""
    try:
        return await page.evaluate("""() => {
            const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const candidates = Array.from(document.querySelectorAll('button.auto-editor-next, button, [role="button"]'))
                .filter((el) => visible(el) && norm(el.innerText || el.textContent || '').includes('下一步'));
            const pickScore = (el) => {
                const rect = el.getBoundingClientRect();
                const primary = (el.className || '').includes('auto-editor-next') ? 1000000 : 0;
                const enabled = (!el.disabled
                    && el.getAttribute('aria-disabled') !== 'true'
                    && !el.classList.contains('disabled')) ? 100000 : 0;
                return primary + enabled + Math.round(rect.bottom * 10) + Math.round(rect.width * rect.height);
            };
            const ranked = candidates.slice().sort((a, b) => pickScore(b) - pickScore(a));
            const best = ranked[0] || null;
            const hints = Array.from(document.querySelectorAll(
                '.arco-form-item-message, .arco-form-item-explain, .byte-form-item-message, .byte-form-item-explain, [class*="error"], [class*="warning"]'
            ))
                .filter(visible)
                .map((el) => norm(el.innerText || el.textContent || ''))
                .filter(Boolean)
                .slice(0, 8);
            return {
                found: !!best,
                totalMatches: candidates.length,
                text: best ? norm(best.innerText || best.textContent || '') : '',
                disabled: !!best && (
                    !!best.disabled
                    || best.getAttribute('aria-disabled') === 'true'
                    || best.classList.contains('disabled')
                ),
                className: best ? (best.className || '') : '',
                rankedTexts: ranked.slice(0, 5).map((el) => norm(el.innerText || el.textContent || '')),
                hints,
            };
        }""")
    except Exception as e:
        return {"found": False, "error": str(e), "hints": []}


async def _click_best_next_step_button(page, *, force: bool = False) -> bool:
    """只点击当前评分最高的“下一步”按钮，避免误点其他同名按钮。"""
    try:
        return bool(await page.evaluate("""(forceClick) => {
            const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const candidates = Array.from(document.querySelectorAll('button.auto-editor-next, button, [role="button"]'))
                .filter((el) => visible(el) && norm(el.innerText || el.textContent || '').includes('下一步'));
            const pickScore = (el) => {
                const rect = el.getBoundingClientRect();
                const primary = (el.className || '').includes('auto-editor-next') ? 1000000 : 0;
                const enabled = (!el.disabled
                    && el.getAttribute('aria-disabled') !== 'true'
                    && !el.classList.contains('disabled')) ? 100000 : 0;
                return primary + enabled + Math.round(rect.bottom * 10) + Math.round(rect.width * rect.height);
            };
            const btn = candidates.slice().sort((a, b) => pickScore(b) - pickScore(a))[0] || null;
            if (!btn) return false;
            btn.scrollIntoView({ block: 'center', inline: 'center' });
            if (!forceClick && (
                btn.disabled
                || btn.getAttribute('aria-disabled') === 'true'
                || btn.classList.contains('disabled')
            )) {
                return false;
            }
            if (typeof btn.click === 'function') {
                btn.click();
                return true;
            }
            ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach((type) => {
                btn.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
            });
            return true;
        }""", force))
    except Exception as e:
        logger.debug(f"JS点击主“下一步”失败(force={force}): {e}")
        return False


def _format_publish_flow_state(state: dict) -> str:
    if not state:
        return "[empty]"
    if state.get("error"):
        return f"error={state['error']}"
    parts = [
        f"editor={'Y' if state.get('editorVisible') else 'N'}",
        f"next={'Y' if state.get('hasNextStep') else 'N'}",
        f"publish={'Y' if state.get('hasPublishSettings') else 'N'}",
    ]
    if state.get("hasNextStep"):
        parts.append(f"nextDisabled={'Y' if state.get('nextStepDisabled') else 'N'}")
        if state.get("nextStepCount", 0) > 1:
            parts.append(f"nextCount={state.get('nextStepCount')}")
    flags = []
    if state.get("hasContinueEdit"):
        flags.append("continue")
    if state.get("hasSubmitConfirm"):
        flags.append("submit")
    if state.get("hasRiskDialog"):
        flags.append("risk")
    if state.get("hasDetectionChoice"):
        flags.append("detect")
    if state.get("hasIgnoreAll"):
        flags.append("ignore")
    if flags:
        parts.append("flags=" + ",".join(flags))
    if state.get("dialogTitles"):
        parts.append("dialogs=" + " | ".join(state["dialogTitles"][:3]))
    if state.get("visibleButtons"):
        parts.append("buttons=" + " | ".join(state["visibleButtons"][:6]))
    return " | ".join(parts)


async def log_publish_flow_state(page, stage: str) -> dict:
    state = await get_publish_flow_state(page)
    logger.info(f"  流程状态[{stage}]: {_format_publish_flow_state(state)}")
    return state


def _is_same_publish_flow_state(a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return False
    keys = (
        "editorVisible",
        "hasNextStep",
        "hasPublishSettings",
        "hasContinueEdit",
        "hasSubmitConfirm",
        "hasRiskDialog",
        "hasDetectionChoice",
        "hasIgnoreAll",
    )
    if any(a.get(k) != b.get(k) for k in keys):
        return False
    return (
        (a.get("dialogTitles") or [])[:3] == (b.get("dialogTitles") or [])[:3]
        and (a.get("visibleButtons") or [])[:6] == (b.get("visibleButtons") or [])[:6]
    )


async def get_publish_flow_state(page) -> dict:
    """采集编辑到发布设置之间的页面状态，便于日志诊断。"""
    try:
        return await page.evaluate("""() => {
            const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const takeTexts = (selector, limit = 8) => Array.from(document.querySelectorAll(selector))
                .filter(visible)
                .map((el) => norm(el.innerText || el.textContent || ''))
                .filter(Boolean)
                .slice(0, limit);
            const visibleButtons = Array.from(document.querySelectorAll('button, [role="button"]'))
                .filter(visible)
                .map((el) => norm(el.innerText || el.textContent || ''))
                .filter(Boolean)
                .slice(0, 10);
            const bodyText = document.body.innerText || '';
            const nextBtns = Array.from(document.querySelectorAll('button.auto-editor-next, button, [role="button"]'))
                .filter((el) => visible(el) && norm(el.innerText || el.textContent || '').includes('下一步'));
            const pickScore = (el) => {
                const rect = el.getBoundingClientRect();
                const primary = (el.className || '').includes('auto-editor-next') ? 1000000 : 0;
                const enabled = (!el.disabled
                    && el.getAttribute('aria-disabled') !== 'true'
                    && !el.classList.contains('disabled')) ? 100000 : 0;
                return primary + enabled + Math.round(rect.bottom * 10) + Math.round(rect.width * rect.height);
            };
            const nextBtn = nextBtns.slice().sort((a, b) => pickScore(b) - pickScore(a))[0] || null;
            const editor = document.querySelector('.ProseMirror');
            return {
                editorVisible: visible(editor),
                editorTextLength: editor ? norm(editor.innerText || '').length : 0,
                hasNextStep: !!nextBtn,
                nextStepText: nextBtn ? norm(nextBtn.innerText || nextBtn.textContent || '') : '',
                nextStepDisabled: !!nextBtn && (
                    !!nextBtn.disabled
                    || nextBtn.getAttribute('aria-disabled') === 'true'
                    || nextBtn.classList.contains('disabled')
                ),
                nextStepCount: nextBtns.length,
                hasPublishSettings: bodyText.includes('发布设置')
                    || bodyText.includes('是否使用AI')
                    || visibleButtons.some((t) => ['确认发布', '定时发布'].includes(t)),
                hasContinueEdit: bodyText.includes('是否继续编辑'),
                hasSubmitConfirm: bodyText.includes('是否确定提交') || bodyText.includes('发布提示'),
                hasRiskDialog: bodyText.includes('是否进行内容风险检测')
                    || bodyText.includes('请选择内容检测方式'),
                hasDetectionChoice: bodyText.includes('请选择内容检测方式')
                    || (bodyText.includes('仅基础检测') && bodyText.includes('全面检测')),
                hasIgnoreAll: visibleButtons.some((t) => ['忽略全部', '全部忽略', '暂不处理', '关闭', '我知道了', '知道了'].includes(t)),
                dialogTitles: takeTexts('[role="dialog"] h1, [role="dialog"] h2, [role="dialog"] h3, .arco-modal-title, .semi-modal-title, .semi-drawer-title', 6),
                sidePanelTexts: takeTexts('[class*="drawer"], [class*="panel"], [class*="modal"], [class*="dialog"]', 6),
                bodyText: norm(bodyText),
                visibleButtons,
            };
        }""")
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# JS: 获取作品列表（CLI 和 GUI 共用）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# JS: 从章节管理页提取最新一条发布时间（仅当前页，不翻页）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 章节列表提取（修改模式用）— 当前页提取
# ---------------------------------------------------------------------------
EXTRACT_CURRENT_PAGE_JS = r"""() => {
    const dateRe = /(\d{4}[-\/]\d{2}[-\/]\d{2})\s+(\d{2}:\d{2})/;
    const chapters = [];
    let lastPub = null;
    let lastPubKey = '';

    for (const row of document.querySelectorAll('tr')) {
        const cells = row.querySelectorAll('td');
        if (cells.length < 2) continue;
        const title = cells[0].textContent.trim();
        if (!title) continue;

        let editUrl = null;
        for (const a of row.querySelectorAll('a')) {
            const href = a.getAttribute('href') || '';
            if (/\/publish\//.test(href) || /chapter_id/.test(href)) {
                editUrl = href; break;
            }
            const text = a.textContent.trim();
            if (text === '编辑' || text === '修改') {
                editUrl = href; break;
            }
        }

        let chapterNum = null;
        let m = title.match(/^第\s*(\d+)\s*[章回节话]/);
        if (m) chapterNum = parseInt(m[1], 10);
        else { m = title.match(/^(\d+)/); if (m) chapterNum = parseInt(m[1], 10); }

        let status = '';
        for (let ci = 1; ci < cells.length; ci++) {
            const ct = cells[ci].textContent.trim();
            if (/待发布|已发布|审核中|草稿|已拒绝/.test(ct)) {
                status = ct; break;
            }
        }

        chapters.push({ title, chapterNum, editUrl, status });

        const dm = row.textContent.match(dateRe);
        if (dm) {
            const d = dm[1].replace(/\//g, '-');
            const t = dm[2];
            const pk = d + ' ' + t;
            if (pk > lastPubKey) {
                lastPub = { date: d, time: t, chapter: title };
                lastPubKey = pk;
            }
        }
    }

    const totalPages = Math.max(
        1,
        ...[...document.querySelectorAll('li.arco-pagination-item')]
            .map(el => parseInt(el.textContent.trim(), 10))
            .filter(n => !Number.isNaN(n))
    );
    const activePage = document.querySelector('li.arco-pagination-item-active')
        ?.textContent?.trim() || '1';
    return { chapters, lastPublish: lastPub, totalPages, activePage };
}"""


# ---------------------------------------------------------------------------
# JS: 检测章节管理页的卷列表
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# JS: 选择指定卷（直接展开 → 点击目标 → 等待刷新）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# JS: 检测新建章节页的卷列表
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# JS: 新建章节页选择指定卷
# ---------------------------------------------------------------------------


async def detect_volumes(page) -> dict:
    """?????????????"""
    return await _detect_volumes_impl(
        page,
        logger=logger,
        detect_volumes_js=DETECT_VOLUMES_JS,
    )


async def select_volume(page, volume_text: str) -> bool:
    """??????????????"""
    return await _select_volume_impl(
        page,
        volume_text,
        logger=logger,
        detect_volumes_js=DETECT_VOLUMES_JS,
        resolve_volume_name=resolve_volume_name,
        wait_manage_table_ready=_wait_manage_table_ready,
        browser_timeout=_browser_timeout,
    )


async def detect_editor_volumes(page) -> dict:
    """??????????????"""
    return await _detect_editor_volumes_impl(
        page,
        logger=logger,
        detect_editor_volumes_js=DETECT_EDITOR_VOLUMES_JS,
    )


async def _wait_manage_table_ready(page, timeout_ms: int | None = None):
    return await _wait_manage_table_ready_impl(
        page,
        timeout_ms,
        browser_timeout=_browser_timeout,
    )

async def _go_to_next_manage_page(page) -> bool:
    return await _go_to_next_manage_page_impl(
        page,
        wait_manage_table_ready_fn=_wait_manage_table_ready,
        browser_timeout=_browser_timeout,
        logger=logger,
    )

async def _go_to_manage_page_number(page, target_page: int) -> bool:
    return await _go_to_manage_page_number_impl(
        page,
        target_page,
        wait_manage_table_ready_fn=_wait_manage_table_ready,
        browser_timeout=_browser_timeout,
        logger=logger,
    )

async def _scan_manage_row(page, target_title: str, target_num) -> dict:
    return await _scan_manage_row_impl(page, target_title, target_num)

async def _click_manage_row_action(page, target_title: str, target_num) -> bool:
    return await _click_manage_row_action_impl(
        page,
        target_title,
        target_num,
        browser_timeout=_browser_timeout,
        logger=logger,
    )

async def resolve_edit_url_from_manage(page, book_id: str, platform_ch: dict) -> str | None:
    return await _resolve_edit_url_from_manage_impl(
        page,
        book_id,
        platform_ch,
        chapter_manage_url_tpl=CHAPTER_MANAGE_URL_TPL,
        base_url=BASE_URL,
        select_volume_fn=select_volume,
        go_to_manage_page_number_fn=_go_to_manage_page_number,
        scan_manage_row_fn=_scan_manage_row,
        click_manage_row_action_fn=_click_manage_row_action,
        logger=logger,
    )

def _parse_chapter_api_items(items: list, book_id: str) -> list[dict]:
    chapters: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(
            item.get("title")
            or item.get("chapter_title")
            or item.get("chapter_name")
            or item.get("name")
            or ""
        ).strip()
        if not title:
            continue
        raw_num = (
            item.get("chapter_num")
            or item.get("chapter_index")
            or item.get("index")
            or item.get("serial_num")
        )
        chapter_num = None
        if raw_num not in (None, ""):
            try:
                chapter_num = int(raw_num)
            except (TypeError, ValueError):
                chapter_num = None
        if chapter_num is None:
            num = _extract_chapter_num(title)
            if num is not None:
                chapter_num = int(num)
        edit_item_id = (
            item.get("item_id")
            or item.get("chapter_id")
            or item.get("id")
        )
        edit_url = None
        if edit_item_id not in (None, ""):
            edit_url = f"/main/writer/{book_id}/publish/{edit_item_id}/?enter_from=modifychapter"
        status = ""
        for key in (
            "status_desc",
            "chapter_status_desc",
            "audit_status_desc",
            "publish_status_desc",
            "status_name",
            "chapter_status_name",
            "publish_status_name",
            "audit_status_name",
            "display_status",
            "display_status_desc",
            "status_text",
        ):
            value = item.get(key)
            status = _format_status_value(key, value)
            if status:
                break
        if not status:
            for key, value in item.items():
                low = str(key).lower()
                if "status" not in low:
                    continue
                status = _format_status_value(str(key), value)
                if status:
                    break
        chapters.append({
            "title": title,
            "chapterNum": chapter_num,
            "editUrl": edit_url,
            "status": status,
        })
    return chapters


def _format_status_value(key: str, value) -> str:
    key = str(key)
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
        return ""
    if not isinstance(value, (int, float)) or value is None:
        return ""
    num = int(value)
    if key == "display_status":
        mapping = {
            1: "已发布",
        }
        label = mapping.get(num)
        return f"{label} ({key}={num})" if label else f"{key}={num}"
    return f"{key}={num}"


async def _wait_for_cached_chapter_api(page, volume_id: str, page_index: int, timeout_ms: int | None = None):
    timeout_ms = timeout_ms or _browser_timeout
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        hit = getattr(page, "_chapter_api_cache", {}).get((volume_id, page_index))
        if hit:
            return hit
        await page.wait_for_timeout(200)
    return None


async def _fetch_chapter_api_variant(page, qs: dict, *, status_value: str, page_index: int):
    params = dict(qs)
    params["status"] = status_value
    params["page_index"] = str(page_index)
    base = (
        f"{BASE_URL}/api/author/chapter/chapter_list/v1?"
        + urlencode(params)
    )
    await page.evaluate(
        """async (url) => {
            try { await fetch(url, { credentials: 'include' }); } catch (e) {}
        }""",
        base,
    )
    volume_id = str(params.get("volume_id", "") or "")
    hit = await _wait_for_cached_chapter_api(page, volume_id, page_index, 4000)
    return hit


async def _extract_chapters_via_api(page, book_id: str) -> tuple[list[dict], dict | None] | None:
    current_volume = await page.evaluate(
        """() => document.querySelector(
            '.chapter-select-left .serial-select.byte-select:not(.chapter-status-select) .byte-select-view-value'
        )?.textContent?.trim() || ''"""
    )
    volume_id = getattr(page, "_volume_name_to_id", {}).get(current_volume, "")
    if not volume_id:
        return None

    first = await _wait_for_cached_chapter_api(page, volume_id, 0, 4000)
    if not first:
        logger.info(f"  API章节列表未命中缓存，回退 DOM: {current_volume}")
        return None

    data = (first.get("body") or {}).get("data") or {}
    per_page = int((first.get("qs") or {}).get("page_count", "15") or 15)
    total_count = int(data.get("total_count") or 0)
    if total_count == 0:
        base_qs = dict(first.get("qs") or {})
        for alt_status in ("1", "2", "3", "-1"):
            if str(base_qs.get("status", "")) == alt_status:
                continue
            alt = await _fetch_chapter_api_variant(page, base_qs, status_value=alt_status, page_index=0)
            if not alt:
                continue
            alt_data = (alt.get("body") or {}).get("data") or {}
            alt_total = int(alt_data.get("total_count") or 0)
            logger.info(f"  API扩查状态 status={alt_status} -> total_count={alt_total} | 卷={current_volume}")
            if alt_total > 0:
                first = alt
                data = alt_data
                total_count = alt_total
                break
    total_pages = max(1, (total_count + per_page - 1) // per_page) if total_count else 1

    all_chapters: list[dict] = []
    seen_keys: set[str] = set()
    last_pub: dict | None = None

    for page_index in range(total_pages):
        payload = first if page_index == 0 else None
        if payload is None:
            next_btn = page.locator("li.arco-pagination-item-next:not(.arco-pagination-item-disabled)").first
            if await next_btn.count() == 0:
                next_btn = page.locator("button[aria-label='next'], .next-page").first
            if await next_btn.count() == 0:
                logger.warning(f"  API翻页缺少下一页按钮: 期望第{page_index + 1}页")
                break
            await next_btn.click()
            payload = await _wait_for_cached_chapter_api(page, volume_id, page_index, 6000)
            if payload is None:
                logger.warning(f"  API未等到第{page_index + 1}页响应: volume_id={volume_id}")
                break

        body = payload.get("body") or {}
        data = body.get("data") or {}
        items = data.get("item_list") or []
        page_chapters = _parse_chapter_api_items(items, book_id)
        for ch in page_chapters:
            key = f"{ch.get('chapterNum')}|{ch.get('title', '')}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            ch["rowIndex"] = len(all_chapters)
            ch["pageIndex"] = page_index + 1
            ch["volumeText"] = current_volume
            all_chapters.append(ch)

        logger.info(
            f"  API第{page_index + 1}页: {len(page_chapters)} 条 | volume_id={volume_id} | 卷={current_volume}"
        )

    logger.info(f"  API共 {len(all_chapters)} 个章节 | 卷={current_volume}")
    return all_chapters, last_pub


async def select_editor_volume(page, volume_text: str) -> bool:
    """??????????????"""
    return await _select_editor_volume_impl(
        page,
        volume_text,
        logger=logger,
        resolve_volume_name=resolve_volume_name,
        detect_editor_volumes_js=DETECT_EDITOR_VOLUMES_JS,
        select_editor_volume_js=SELECT_EDITOR_VOLUME_JS,
    )


async def extract_chapters_from_page(
    page, book_id: str = "",
) -> tuple[list[dict], dict | None]:
    """从章节管理页提取全部章节列表（优先 API，其次 DOM）。

    返回 (chapters, last_publish_info)。
    last_publish_info: {date, time, chapter} 或 None。
    """
    api_result = await _extract_chapters_via_api(page, book_id)
    if api_result is not None:
        return api_result

    all_chapters: list[dict] = []
    seen_keys: set[str] = set()
    last_pub: dict | None = None
    last_pub_key = ""
    page_count = 0
    total_pages = 1

    while True:
        page_state = await _wait_manage_table_ready(page)
        result = await page.evaluate(EXTRACT_CURRENT_PAGE_JS)
        total_pages = int(result.get("totalPages") or 1)
        active_page = str(result.get("activePage") or "1")
        page_rows = result.get("chapters", []) or []
        page_count += 1

        for ch in page_rows:
            if ch.get("chapterNum") is None:
                num = _extract_chapter_num(str(ch.get("title", "") or ""))
                if num is not None:
                    try:
                        ch["chapterNum"] = int(num)
                    except ValueError:
                        pass
            key = f"{ch.get('chapterNum')}|{ch.get('title', '')}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            ch["rowIndex"] = len(all_chapters)
            all_chapters.append(ch)

        cur_last_pub = result.get("lastPublish")
        if cur_last_pub:
            pk = f"{cur_last_pub.get('date', '')} {cur_last_pub.get('time', '')}"
            if pk > last_pub_key:
                last_pub_key = pk
                last_pub = cur_last_pub

        logger.info(
            f"  第{active_page}页抓取: {len(page_rows)} 条 | 首行: {page_state.get('firstTitle') or '[空]'}"
        )

        if active_page == str(total_pages):
            break
        if not await _go_to_next_manage_page(page):
            break

    missing_num_titles = [
        str(ch.get("title", "") or "")
        for ch in all_chapters
        if ch.get("chapterNum") is None
    ]
    logger.info(f"  共 {page_count}/{total_pages} 页, {len(all_chapters)} 个章节")
    if missing_num_titles:
        preview = " | ".join(missing_num_titles[:5])
        logger.info(
            f"  仍有 {len(missing_num_titles)} 个平台章节未解析出章节号: {preview}"
        )

    return all_chapters, last_pub


async def click_next_step(page):
    """点击下一步按钮（进入发布流程）。"""
    await dismiss_overlays(page)
    await _settle_editor_before_publish(page)
    before_state = await get_publish_flow_state(page)
    next_state = await _get_next_step_button_state(page)
    if next_state.get("found") and next_state.get("totalMatches", 0) > 1:
        logger.info(
            f"  检测到多个“下一步”按钮，优先点击主按钮: count={next_state.get('totalMatches')}"
        )
        ranked_texts = next_state.get("rankedTexts") or []
        if ranked_texts:
            logger.info(f"  下一步候选按钮: {' | '.join(ranked_texts)}")
    if next_state.get("found") and next_state.get("disabled"):
        logger.info("  “下一步”当前不可点击，等待编辑器状态稳定")
        for _ in range(10):
            await _settle_editor_before_publish(page)
            next_state = await _get_next_step_button_state(page)
            if not next_state.get("disabled"):
                break
            await page.wait_for_timeout(400)
        if next_state.get("disabled"):
            hints = " | ".join(next_state.get("hints") or [])
            if hints:
                logger.warning(f"  “下一步”仍不可点击，页面提示: {hints}")
            else:
                debug_state = await _get_editor_submit_debug_state(page)
                inputs = debug_state.get("inputs") or []
                input_preview = " | ".join(
                    f"{str(i.get('placeholder') or i.get('type') or 'input')[:12]}={str(i.get('value') or '')[:20]}"
                    for i in inputs[:5]
                )
                logger.warning(
                    "  “下一步”仍不可点击，未发现明显表单提示"
                    f" | title={str(debug_state.get('title') or '')[:30]}"
                    f" | editorLen={debug_state.get('editorLength')}"
                    f" | save={debug_state.get('saveText') or '-'}"
                    f" | nextDisabled={debug_state.get('nextDisabled')}"
                    f" | aria={debug_state.get('nextAriaDisabled') or '-'}"
                    f" | class={str(debug_state.get('nextClass') or '')[:60]}"
                    f" | inputs={input_preview}"
                )
    async def _wait_after_click() -> bool:
        for _ in range(12):
            await page.wait_for_timeout(400)
            cur_state = await get_publish_flow_state(page)
            if cur_state.get("hasPublishSettings"):
                return True
            if (
                cur_state.get("hasContinueEdit")
                or cur_state.get("hasSubmitConfirm")
                or cur_state.get("hasRiskDialog")
                or cur_state.get("hasDetectionChoice")
                or cur_state.get("hasIgnoreAll")
            ):
                return True
            if not _is_same_publish_flow_state(before_state, cur_state):
                return True
        return False

    if await _click_best_next_step_button(page) and await _wait_after_click():
        return

    next_btn = page.locator("button.auto-editor-next")
    if await next_btn.count() == 0:
        next_btn = page.locator("button", has_text="下一步").locator(
            "visible=true"
        ).first

    async def _wait_after_click() -> bool:
        for _ in range(12):
            await page.wait_for_timeout(400)
            cur_state = await get_publish_flow_state(page)
            if cur_state.get("hasPublishSettings"):
                return True
            if (
                cur_state.get("hasContinueEdit")
                or cur_state.get("hasSubmitConfirm")
                or cur_state.get("hasRiskDialog")
                or cur_state.get("hasDetectionChoice")
                or cur_state.get("hasIgnoreAll")
            ):
                return True
            if not _is_same_publish_flow_state(before_state, cur_state):
                return True
        return False

    async def _click_locator(locator, *, force: bool = False) -> bool:
        try:
            if await locator.count() == 0:
                return False
            await locator.first.scroll_into_view_if_needed()
        except Exception:
            pass
        try:
            await locator.first.click(force=force)
            return True
        except Exception as e:
            logger.debug(f"点击“下一步”失败(force={force}): {e}")
            return False

    if await _click_locator(next_btn) and await _wait_after_click():
        return

    logger.info("  点击“下一步”后页面仍停留原位，尝试强制点击")
    if await _click_locator(next_btn, force=True) and await _wait_after_click():
        return

    js_clicked = await page.evaluate("""() => {
        const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
        const visible = (el) => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        };
        const candidates = Array.from(document.querySelectorAll('button.auto-editor-next, button, [role="button"]'))
            .filter((el) => visible(el) && norm(el.innerText || el.textContent || '').includes('下一步'));
        const pickScore = (el) => {
            const rect = el.getBoundingClientRect();
            const primary = (el.className || '').includes('auto-editor-next') ? 1000000 : 0;
            const enabled = (!el.disabled
                && el.getAttribute('aria-disabled') !== 'true'
                && !el.classList.contains('disabled')) ? 100000 : 0;
            return primary + enabled + Math.round(rect.bottom * 10) + Math.round(rect.width * rect.height);
        };
        const btn = candidates.slice().sort((a, b) => pickScore(b) - pickScore(a))[0] || null;
        if (!btn) return false;
        btn.scrollIntoView({ block: 'center', inline: 'center' });
        ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach((type) => {
            btn.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
        });
        return true;
    }""")
    if js_clicked and await _wait_after_click():
        return

    logger.warning(
        f"  点击“下一步”后页面状态未明显变化: {_format_publish_flow_state(before_state)}"
    )


async def _navigate_to_publish_settings(page, *, use_ai: bool = False):
    """
    从编辑器完整走到"发布设置"对话框。

    点击"下一步"后可能出现两种流程:
      A) 直接弹出对话框序列（常见）:
         发布提示(错别字确认) -> 是否进行内容风险检测 -> 发布设置
      B) 先打开右侧智能纠错面板:
         纠错面板 -> 忽略全部 -> 再次下一步 -> 对话框序列

    本函数统一处理两种情况。
    """
    await click_next_step(page)
    stagnant_editor_rounds = 0
    last_state: dict | None = None

    for step in range(10):
        handled_overlay = await dismiss_overlays(page)
        if handled_overlay == "继续编辑":
            logger.info("已继续编辑，补点一次“下一步”继续进入发布流程")
            await click_next_step(page)
            await page.wait_for_timeout(1200)

        await _check_daily_limit(page)
        state = await log_publish_flow_state(page, f"publish-loop-{step + 1}")

        if state.get("hasPublishSettings") or await page.locator("text=发布设置").count() > 0:
            await _apply_publish_options(page, use_ai=use_ai)
            return

        handled_dialog = await _handle_publish_interruption_dialogs(page)
        if handled_dialog:
            logger.info(f"  检测到发布流程弹窗，点击: {handled_dialog}")
            await page.wait_for_timeout(1000)
            if handled_dialog == "继续编辑":
                await click_next_step(page)
                await page.wait_for_timeout(1200)
            stagnant_editor_rounds = 0
            last_state = None
            continue

        try:
            ignore_labels = ("忽略全部", "全部忽略", "暂不处理", "关闭", "我知道了", "知道了")
            handled_ignore = False
            clicked_label = await _click_visible_action(page, ignore_labels)
            if clicked_label:
                logger.info(f"  检测到侧栏/弹窗按钮，点击: {clicked_label}")
                await click_next_step(page)
                await page.wait_for_timeout(1200)
                handled_ignore = True
                stagnant_editor_rounds = 0
                last_state = None
            if handled_ignore:
                continue
        except Exception:
            pass

        if await page.locator("text=是否确定提交").count() > 0:
            if await _click_visible_action(page, ["提交", "确认"]):
                await page.wait_for_timeout(1000)
                continue

        if await page.locator("text=是否进行内容风险检测").count() > 0:
            if await _click_visible_action(page, ["取消", "暂不处理", "跳过"]):
                await page.wait_for_timeout(1000)
                continue

        if await page.locator("text=请选择内容检测方式").count() > 0:
            if await _click_visible_action(page, ["仅基础检测", "基础检测"]):
                await page.wait_for_timeout(1000)
                continue

        is_editor_still_waiting = (
            state.get("editorVisible")
            and state.get("hasNextStep")
            and not state.get("hasPublishSettings")
            and not state.get("hasContinueEdit")
            and not state.get("hasSubmitConfirm")
            and not state.get("hasRiskDialog")
            and not state.get("hasDetectionChoice")
            and not state.get("hasIgnoreAll")
        )
        if is_editor_still_waiting:
            if _is_same_publish_flow_state(last_state, state):
                stagnant_editor_rounds += 1
            else:
                stagnant_editor_rounds = 1
            last_state = state
            if stagnant_editor_rounds >= 2:
                logger.info("  仍停留在编辑页，补点一次“下一步”")
                await click_next_step(page)
                await page.wait_for_timeout(1200)
                stagnant_editor_rounds = 0
                last_state = None
                continue
        else:
            stagnant_editor_rounds = 0
            last_state = state

        await page.wait_for_timeout(1000)

    await log_publish_flow_state(page, "publish-timeout-before-wait")
    await page.wait_for_selector("text=发布设置", timeout=_browser_timeout)
    await _apply_publish_options(page, use_ai=use_ai)
    return


async def _apply_publish_options(page, *, use_ai: bool = False):
    """在发布设置对话框中，设置各选项。"""
    # 是否使用AI
    target = "否" if not use_ai else "是"
    await page.evaluate("""(target) => {
        const labels = document.querySelectorAll('label, span');
        for (const el of labels) {
            const text = el.textContent.trim();
            if (text === target) {
                let parent = el;
                for (let i = 0; i < 6; i++) {
                    if (!parent.parentElement) break;
                    parent = parent.parentElement;
                    if (parent.textContent.includes('是否使用AI')) {
                        const radio = el.querySelector('input[type="radio"]');
                        if (radio) { radio.click(); return; }
                        el.click();
                        return;
                    }
                }
            }
        }
    }""", target)
    await page.wait_for_timeout(500)


async def publish_scheduled(page, date_str: str, time_str: str, *, use_ai: bool = False):
    """
    完整的定时发布流程:
    1. 通过纠错面板和弹窗走到"发布设置"对话框
    2. 开启定时发布开关
    3. 设置日期和时间（Arco DatePicker/TimePicker）
    4. 点击确认发布
    """
    # 1. 走完纠错流程，到达发布设置对话框
    await _navigate_to_publish_settings(page, use_ai=use_ai)

    # 2. 开启定时发布 (Arco Switch)
    #    精确定位: 找到"定时发布"文字旁边的 switch，避免点到"是否使用AI"等其他开关
    switched = await page.evaluate("""() => {
        // 找到包含"定时发布"文字的元素
        const walker = document.createTreeWalker(
            document.body, NodeFilter.SHOW_TEXT, null
        );
        while (walker.nextNode()) {
            if (walker.currentNode.textContent.includes('定时发布')) {
                // 从该文本节点向上找共同父容器，再在其中找 switch
                let parent = walker.currentNode.parentElement;
                for (let i = 0; i < 5; i++) {
                    if (!parent) break;
                    const sw = parent.querySelector('button[role="switch"]');
                    if (sw) {
                        if (sw.getAttribute('aria-checked') !== 'true') {
                            sw.click();
                            return 'clicked';
                        }
                        return 'already_on';
                    }
                    parent = parent.parentElement;
                }
            }
        }
        // 兜底: 点击第一个 switch
        const sw = document.querySelector('button[role="switch"]');
        if (sw && sw.getAttribute('aria-checked') !== 'true') {
            sw.click();
            return 'clicked_fallback';
        }
        return 'not_found';
    }""")
    logger.info(f"    定时发布开关: {switched}")
    # 等待日期输入框出现
    try:
        await page.wait_for_selector("input[placeholder='请选择日期']", timeout=_browser_timeout)
    except PWTimeout:
        raise RuntimeError("等待日期输入框超时")
    await page.wait_for_timeout(300)

    # 3. 填写日期 (Arco DatePicker)
    #    键盘方式: 点击输入框 -> 全选 -> 输入日期 -> Enter 确认
    date_input = page.locator("input[placeholder='请选择日期']")
    if await date_input.count() == 0:
        raise RuntimeError("未找到日期输入框")
    else:
        await date_input.click()
        await page.wait_for_timeout(300)
        await page.keyboard.press(f"{_MOD_KEY}+a")
        await page.keyboard.type(date_str, delay=50)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)
        # Escape 关闭可能残留的日期选择下拉面板
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)

    # 4. 填写时间 (Arco TimePicker)
    time_input = page.locator("input[placeholder='请选择时间']")
    if await time_input.count() == 0:
        raise RuntimeError("未找到时间输入框")
    else:
        await time_input.click()
        await page.wait_for_timeout(300)
        await page.keyboard.press(f"{_MOD_KEY}+a")
        await page.keyboard.type(time_str, delay=50)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)
        # Escape 关闭可能残留的时间选择下拉面板
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)

    # 5. 确认发布
    await confirm_publish(page)


# ---------------------------------------------------------------------------
# 命令: login
# ---------------------------------------------------------------------------
async def cmd_login():
    logger.info("正在打开浏览器，请在网页中完成登录...")
    async with async_playwright() as p:
        browser, context = await create_context(p, headless=False)
        page = await context.new_page()
        await page.goto(ZONE_URL)
        await page.wait_for_load_state("networkidle")

        logger.info("")
        logger.info("=" * 50)
        logger.info("  请在浏览器中登录番茄作家账号")
        logger.info("  登录成功后回到此处按 Enter 保存会话")
        logger.info("=" * 50)
        await asyncio.get_running_loop().run_in_executor(None, input)

        await save_auth(context)
        await browser.close()
        logger.info("登录状态已保存。")


# ---------------------------------------------------------------------------
# 命令: books
# ---------------------------------------------------------------------------
async def cmd_books():
    if not AUTH_FILE.exists():
        logger.warning("请先运行 login 命令登录。")
        return

    async with async_playwright() as p:
        browser, context = await create_context(p, headless=True)
        page = await context.new_page()

        await page.goto(BOOK_MANAGE_URL)
        await page.wait_for_load_state("networkidle")
        try:
            await page.wait_for_selector('a[href*="chapter-manage/"]', timeout=5000)
        except PWTimeout:
            pass

        books = await page.evaluate(BOOKS_JS)

        logger.info("")
        if not books:
            logger.error("未找到作品，请检查登录状态 (重新运行 login)")
        else:
            logger.info(f"找到 {len(books)} 部作品:")
            logger.info("-" * 60)
            for i, b in enumerate(books):
                logger.info(f"  {i+1}. {b['name']}")
                logger.info(f"     ID: {b['bookId']}")
                logger.info(f"     {b['chapters']}章 | {b['words']}字 | {b['status']}")
                logger.info("")
            logger.info("-" * 60)
            logger.info("上传时使用:  python fanqie_upload.py upload <目录> --book-id <ID>")

        await save_auth(context)
        await browser.close()


# ---------------------------------------------------------------------------
# 命令: upload
# ---------------------------------------------------------------------------
async def cmd_upload(directory: Path, book_id: str, publish: bool, args):
    if not AUTH_FILE.exists():
        logger.warning("请先运行 login 命令登录。")
        return

    cfg = load_config()
    headless = args.headless or cfg.get("headless", False)
    delay = args.delay if args.delay is not None else cfg.get("delay_between_chapters", 3)

    # 定时发布参数
    schedule_date = getattr(args, "schedule", None)
    schedule_time = getattr(args, "time", "08:00") or "08:00"
    per_day = getattr(args, "per_day", 1) or 1
    unique_titles = getattr(args, "unique_titles", False)
    use_ai = getattr(args, "use_ai", False)

    if not directory.is_dir():
        logger.error(f"目录不存在: {directory}")
        return

    files = get_md_files(directory, warn=logger.warning)
    if not files:
        logger.warning(f"在 {directory} 及其子文件夹中没有找到 .md/.txt 文件")
        return

    # 解析所有文件
    parsed = [parse_md_file(f, warn=logger.warning) for f in files]

    # 检测重复标题
    title_counts = Counter(title for _, title, _ in parsed)
    dup_titles = {t: c for t, c in title_counts.items() if c > 1}

    if dup_titles:
        logger.warning("检测到重复标题 (番茄作家不允许同名章节):")
        for t, c in dup_titles.items():
            indices = [
                i + 1 for i, (_, title, _) in enumerate(parsed) if title == t
            ]
            logger.info(f'  "{t}" × {c} 次  (第 {", ".join(map(str, indices))} 章)')

        if unique_titles:
            parsed = deduplicate_titles(parsed)
            logger.info("  -> 已自动追加章节号后缀去重")
        else:
            logger.info("  提示: 使用 --unique-titles 可自动追加章节号去重")

    # 计算排期
    schedule = None
    if schedule_date:
        try:
            datetime.strptime(schedule_date, "%Y-%m-%d")
        except ValueError:
            logger.error(f"日期格式错误: {schedule_date}  (应为 YYYY-MM-DD)")
            return
        schedule = compute_schedule(len(parsed), schedule_date, schedule_time, per_day)

    # 确定模式
    if schedule:
        validated = _validate_times(schedule_time)
        eff = max(per_day, len(validated)) if validated else per_day
        mode_str = f"定时发布 (从 {schedule_date} 起, 每天 {eff} 章, {schedule_time})"
    elif publish:
        mode_str = "立即发布"
    else:
        mode_str = "存草稿"

    # 预览文件列表
    logger.info(f"找到 {len(files)} 个 MD 文件:")
    logger.info("-" * 60)
    total_words = 0
    for i, (num, title, content) in enumerate(parsed):
        wc = len(strip_md_formatting(content))
        total_words += wc
        num_str = f"第{num}章" if num else "   ?  "
        sched_str = f"  [{schedule[i][0]} {schedule[i][1]}]" if schedule else ""
        logger.info(f"  {i+1:3d}. {num_str} {title}  ({wc} 字){sched_str}")
    logger.info("-" * 60)
    logger.info(f"总计: {len(files)} 章, {total_words} 字")
    logger.info(f"目标: Book ID {book_id}")
    logger.info(f"模式: {mode_str}")
    if schedule:
        last_date = schedule[-1][0]
        total_days = (datetime.strptime(last_date, "%Y-%m-%d")
                      - datetime.strptime(schedule_date, "%Y-%m-%d")).days + 1
        logger.info(f"排期: {schedule_date} ~ {last_date} ({total_days} 天)")
    logger.info("")

    confirm = input("确认上传? (y/N): ").strip().lower()
    if confirm != "y":
        logger.info("已取消。")
        return

    # 构造新建章节 URL（直接导航即可创建，无需点按钮）
    new_chapter_url = NEW_CHAPTER_URL_TPL.format(book_id=book_id)

    async with async_playwright() as p:
        browser, context = await create_context(p, headless=headless)
        page = await context.new_page()

        # 先验证登录态：打开新建章节页看是否能进入编辑器
        await page.goto(new_chapter_url)
        try:
            await wait_for_editor_ready(page, prefer_continue_edit=True)
        except PWTimeout:
            logger.error("无法进入编辑器，请检查:")
            logger.info("  1. Book ID 是否正确")
            logger.info("  2. 登录状态是否有效 (重新运行 login)")
            await page.screenshot(path=str(SCRIPT_DIR / "error_navigate.png"))
            await browser.close()
            return

        success = 0
        failed = 0
        max_retries = cfg.get("max_retries", 2)
        daily_limit_reason = ""

        for i, file in enumerate(files):
            chapter_num, title, content = parsed[i]
            num_str = f"第{chapter_num}章 " if chapter_num else ""
            sched_info = f" -> {schedule[i][0]} {schedule[i][1]}" if schedule else ""
            logger.info(f"[{i+1}/{len(files)}] {num_str}{title}{sched_info}")
            target_volume = resolve_new_chapter_volume(chapter_num, cfg)

            ok = False
            daily_limit = False
            for attempt in range(1, max_retries + 2):
                try:
                    # 首章首次复用当前页面，其余情况导航到新建 URL
                    if i > 0 or attempt > 1:
                        await page.goto(new_chapter_url)
                        await wait_for_editor_ready(page, prefer_continue_edit=True)

                    if target_volume:
                        await select_editor_volume(page, target_volume)
                    await fill_chapter(page, chapter_num, title, content)

                    if schedule:
                        date_str, time_str = schedule[i]
                        await publish_scheduled(page, date_str, time_str, use_ai=use_ai)
                        logger.info(f"  -> 定时发布 {date_str} {time_str}")
                    elif publish:
                        await _navigate_to_publish_settings(page, use_ai=use_ai)
                        await confirm_publish(page)
                        logger.info(f"  -> 已发布")
                    else:
                        await save_draft(page)
                        logger.info(f"  -> 已存草稿")

                    ok = True
                    break

                except DailyLimitReached as e:
                    logger.warning(f"{e}")
                    logger.warning("触发平台当日字数上限，停止当前任务，不再重试。")
                    daily_limit_reason = str(e)
                    daily_limit = True
                    break

                except Exception as e:
                    if is_daily_limit_exception(e):
                        daily_limit_reason = daily_limit_stop_message()
                        logger.warning(f"{daily_limit_reason}")
                        logger.warning("触发平台当日字数上限，停止当前任务，不再重试。")
                        daily_limit = True
                        break
                    if attempt <= max_retries:
                        logger.warning(f"第{attempt}次失败: {e}，重试中...")
                        await page.wait_for_timeout(2000)
                    else:
                        logger.error(f"失败: {e}")
                        try:
                            err_path = SCRIPT_DIR / f"error_{i}_{file.stem}.png"
                            await page.screenshot(path=str(err_path))
                            logger.error(f"截图: {err_path}")
                        except Exception:
                            pass

            if daily_limit:
                failed += 1
                break

            if ok:
                success += 1
            else:
                failed += 1

            if i < len(files) - 1 and delay > 0:
                await page.wait_for_timeout(delay * 1000)

        await save_auth(context)
        await browser.close()

        if daily_limit_reason:
            logger.warning(f"{daily_limit_reason}，已提前结束上传流程。")

        logger.info("")
        logger.info("=" * 40)
        logger.info(f"  上传完成!")
        logger.info(f"  成功: {success}  失败: {failed}")
        logger.info("=" * 40)


# ---------------------------------------------------------------------------
# 修改单章（CLI 和 GUI 共用）
# ---------------------------------------------------------------------------
async def edit_one_chapter(
    page, book_id: str, platform_ch: dict, ch_num: int, title: str, content: str,
    *, use_ai: bool = False, max_retries: int = 2, cancel_check=None,
) -> bool:
    """编辑单个已有章节（含重试）。成功返回 True，失败返回 False。

    DailyLimitReached 不在此处捕获，直接向上抛出以停止整个循环。
    cancel_check 返回 True 时立即中止，避免 GUI 点击“停止”后仍继续重试。
    """
    for attempt in range(1, max_retries + 2):
        if cancel_check and cancel_check():
            logger.info("用户取消修改。")
            return False
        try:
            edit_url = str(platform_ch.get("editUrl", "") or "").strip()
            if edit_url.startswith("/"):
                edit_url = BASE_URL + edit_url
            if not edit_url:
                edit_url = await resolve_edit_url_from_manage(page, book_id, platform_ch)
            if not edit_url:
                raise RuntimeError("未找到真实编辑链接")
            if edit_url != "__ALREADY_OPENED__":
                await page.goto(edit_url)
            await dismiss_overlays(page)
            await wait_for_editor_ready(page)
            await dismiss_overlays(page)
            await dismiss_edit_hint(page)
            prefill = await inspect_editor_prefill(page)
            loaded_num = str(prefill.get("chapterNum", "") or "").strip()
            if not prefill.get("hasContent") and not str(prefill.get("title", "") or "").strip():
                raise RuntimeError("进入了空白编辑页，疑似新建/草稿页，已中止以避免误创建草稿")
            if loaded_num and loaded_num != str(ch_num):
                fallback_url = await resolve_edit_url_from_manage(page, book_id, platform_ch)
                if fallback_url and fallback_url != "__ALREADY_OPENED__":
                    await page.goto(fallback_url)
                    await dismiss_overlays(page)
                    await wait_for_editor_ready(page)
                    await dismiss_overlays(page)
                    await dismiss_edit_hint(page)
                    prefill = await inspect_editor_prefill(page)
                    loaded_num = str(prefill.get("chapterNum", "") or "").strip()
                if loaded_num and loaded_num != str(ch_num):
                    raise RuntimeError(
                        f"进入的编辑页章节号不匹配: 当前={loaded_num}, 目标={ch_num}"
                    )
            await clear_editor(page)
            await fill_chapter(page, str(ch_num), title, content)
            await _navigate_to_publish_settings(page, use_ai=use_ai)
            await confirm_publish(page)
            logger.info("  -> 已保存修改")
            return True
        except DailyLimitReached:
            raise
        except Exception as e:
            if is_daily_limit_exception(e):
                raise DailyLimitReached(daily_limit_stop_message()) from e
            if attempt <= max_retries:
                logger.warning(f"第{attempt}次失败: {e}，重试中...")
                await page.wait_for_timeout(2000)
            else:
                logger.error(f"失败: {e}")
                try:
                    err_path = SCRIPT_DIR / f"error_edit_{ch_num}.png"
                    await page.screenshot(path=str(err_path))
                    logger.error(f"截图: {err_path}")
                except Exception:
                    pass
    return False


async def reschedule_on_manage_page(
    page,
    book_id: str,
    schedule_map: dict[str, tuple[str, str]],
    *,
    max_retries: int = 2,
    delay: float = 1,
    cancel_check=None,
    progress_cb=None,
    volume_text: str = "",
    volume_texts: list[str] | None = None,
) -> tuple[int, int]:
    """在章节管理页上批量修改待发布章节的定时发布设置。

    schedule_map: {章节标题: (date_str, time_str), ...}
    cancel_check: 返回 True 时中止
    progress_cb:  (done, total) 回调
    volume_text:  多卷时选择的卷名（空字符串表示不切换）
    volume_texts: 多卷索引模式时传入所有卷名列表（优先级高于 volume_text）
    返回 (success, failed)。
    """
    total = len(schedule_map)
    success = 0
    failed = 0
    remaining = dict(schedule_map)  # 未处理的

    chapter_manage_url = CHAPTER_MANAGE_URL_TPL.format(book_id=book_id)
    await page.goto(chapter_manage_url)
    await page.wait_for_load_state("networkidle")

    # 等待表格出现
    try:
        await page.wait_for_selector("tr td", timeout=_browser_timeout)
    except Exception:
        logger.error("章节管理页表格未加载")
        return 0, total

    # 多卷索引模式: 逐卷处理
    if volume_texts:
        for vi, vt in enumerate(volume_texts):
            if not remaining:
                break
            if cancel_check and cancel_check():
                break
            logger.info(f"切换到分卷 ({vi+1}/{len(volume_texts)}): {vt}")
            await select_volume(page, vt)
            s, f = await _reschedule_current_volume(
                page, remaining, total,
                max_retries=max_retries, delay=delay,
                cancel_check=cancel_check, progress_cb=progress_cb,
                success_so_far=success, failed_so_far=failed)
            success += s
            failed += f
        if remaining:
            for title in remaining:
                logger.error(f"未处理: {title}")
            failed += len(remaining)
        return success, failed

    # 单卷模式
    if volume_text:
        await select_volume(page, volume_text)

    s, f = await _reschedule_current_volume(
        page, remaining, total,
        max_retries=max_retries, delay=delay,
        cancel_check=cancel_check, progress_cb=progress_cb,
        success_so_far=success, failed_so_far=failed)
    success += s
    failed += f

    if remaining:
        for title in remaining:
            logger.error(f"未处理: {title}")
        failed += len(remaining)

    return success, failed


async def _reschedule_current_volume(
    page,
    remaining: dict[str, tuple[str, str]],
    total: int,
    *,
    max_retries: int = 2,
    delay: float = 1,
    cancel_check=None,
    progress_cb=None,
    success_so_far: int = 0,
    failed_so_far: int = 0,
) -> tuple[int, int]:
    """扫描当前卷的所有页面，处理 remaining 中匹配到的章节。

    会直接从 remaining 中删除已处理的条目。
    返回本轮 (success, failed)。
    """
    success = 0
    failed = 0

    # 诊断行结构，找出时钟图标的选择器
    icon_selector = await page.evaluate(r"""() => {
        for (const row of document.querySelectorAll('tr')) {
            const cells = row.querySelectorAll('td');
            if (cells.length < 3) continue;
            for (let i = 1; i < cells.length - 1; i++) {
                const cell = cells[i];
                const el = cell.querySelector('svg')
                    || cell.querySelector('i[class]')
                    || cell.querySelector('span[class*="icon"]')
                    || cell.querySelector('button')
                    || cell.querySelector('[role="button"]')
                    || cell.querySelector('[role="img"]');
                if (el) {
                    const tag = el.tagName.toLowerCase();
                    const cls = el.className || '';
                    if (tag === 'svg') return 'svg';
                    if (tag === 'i' && cls) return 'i.' + cls.split(' ')[0];
                    if (cls) return tag + '.' + cls.split(' ')[0];
                    return tag;
                }
            }
        }
        return null;
    }""")
    logger.debug(f"  时钟图标元素: {icon_selector or '未检测到'}")

    page_num = 0
    while remaining:
        page_num += 1
        if cancel_check and cancel_check():
            logger.info("用户取消修改定时。")
            break

        # 扫描当前页所有行的标题
        page_titles = await page.evaluate(r"""() => {
            const result = [];
            for (const row of document.querySelectorAll('tr')) {
                const cells = row.querySelectorAll('td');
                if (cells.length < 3) continue;
                const title = cells[0].textContent.trim();
                if (title) result.push(title);
            }
            return result;
        }""")

        matched_on_page = [t for t in page_titles if t in remaining]

        for title in matched_on_page:
            if cancel_check and cancel_check():
                logger.info("用户取消修改定时。")
                break

            date_str, time_str = remaining[title]
            done_so_far = success_so_far + failed_so_far + success + failed
            logger.info(f"[{done_so_far + 1}/{total}] {title} -> {date_str} {time_str}")

            ok = False
            for attempt in range(1, max_retries + 2):
                try:
                    # 点击时钟图标: 在匹配行的中间列中查找可点击元素
                    clicked = await page.evaluate(r"""(targetTitle) => {
                        for (const row of document.querySelectorAll('tr')) {
                            const cells = row.querySelectorAll('td');
                            if (cells.length < 3) continue;
                            if (cells[0].textContent.trim() !== targetTitle)
                                continue;
                            for (let i = 1; i < cells.length - 1; i++) {
                                const cell = cells[i];
                                const el = cell.querySelector('svg')
                                    || cell.querySelector('i[class]')
                                    || cell.querySelector('span[class*="icon"]')
                                    || cell.querySelector('button')
                                    || cell.querySelector('[role="button"]')
                                    || cell.querySelector('[role="img"]');
                                if (el) { el.click(); return true; }
                            }
                            return false;
                        }
                        return false;
                    }""", title)

                    if not clicked:
                        raise RuntimeError("未找到时钟图标")

                    # 等待"修改定时"对话框出现
                    confirm_btn = page.locator(
                        "button", has_text="确认修改")
                    await confirm_btn.wait_for(timeout=_browser_timeout)
                    await page.wait_for_timeout(300)

                    # 填写日期
                    date_input = page.locator(
                        "input[placeholder='请选择日期']")
                    await date_input.click()
                    await page.wait_for_timeout(200)
                    await page.keyboard.press(f"{_MOD_KEY}+a")
                    await page.keyboard.type(date_str, delay=50)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(500)

                    # 填写时间（点击时间输入框会自动关闭日期面板）
                    time_input = page.locator(
                        "input[placeholder='请选择时间']")
                    await time_input.click()
                    await page.wait_for_timeout(200)
                    await page.keyboard.press(f"{_MOD_KEY}+a")
                    await page.keyboard.type(time_str, delay=50)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(500)

                    # 点击"确认修改"
                    await confirm_btn.click(no_wait_after=True)
                    try:
                        await confirm_btn.wait_for(state="hidden", timeout=_browser_timeout)
                    except Exception:
                        await page.wait_for_timeout(1000)

                    logger.info(f"  -> 已修改定时 {date_str} {time_str}")
                    ok = True
                    break

                except Exception as e:
                    # 尝试关闭可能残留的弹窗
                    try:
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(300)
                    except Exception:
                        pass
                    if attempt <= max_retries:
                        logger.warning(f"第{attempt}次失败: {e}，重试中...")
                        await page.wait_for_timeout(1000)
                    else:
                        logger.error(f"失败: {e}")
                        try:
                            err_path = SCRIPT_DIR / f"error_resched_{_safe_filename(title, 20)}.png"
                            await page.screenshot(path=str(err_path))
                            logger.error(f"截图: {err_path}")
                        except Exception:
                            pass

            if ok:
                success += 1
            else:
                failed += 1
            del remaining[title]

            if progress_cb:
                progress_cb(success_so_far + failed_so_far + success + failed, total)

            if delay > 0 and remaining:
                await page.wait_for_timeout(int(delay * 1000))

        # cancel_check 在内部 break 后也需要退出外层
        if cancel_check and cancel_check():
            break

        if not remaining:
            break

        # 翻页
        next_btn = page.locator(
            "li.arco-pagination-item-next:not(.arco-pagination-item-disabled)")
        if await next_btn.count() == 0:
            break
        first_title = await page.evaluate(
            "() => document.querySelector('tr td')?.textContent?.trim() || ''")
        await next_btn.click()
        # 等待表格内容变化
        for _ in range(30):
            await page.wait_for_timeout(300)
            cur = await page.evaluate(
                "() => document.querySelector('tr td')?.textContent?.trim() || ''")
            if cur and cur != first_title:
                break

    return success, failed


# ---------------------------------------------------------------------------
# 命令: edit (修改已有章节)
# ---------------------------------------------------------------------------
async def cmd_edit(directory: Path, book_id: str, args):
    """按章节号匹配并修改已有章节内容。"""
    if not AUTH_FILE.exists():
        logger.warning("请先运行 login 命令登录。")
        return

    cfg = load_config()
    headless = args.headless or cfg.get("headless", False)
    delay = args.delay if args.delay is not None else cfg.get("delay_between_chapters", 3)
    unique_titles = getattr(args, "unique_titles", False)
    use_ai = getattr(args, "use_ai", False)
    match_number_only = getattr(args, "match_number_only", False)

    if not directory.is_dir():
        logger.error(f"目录不存在: {directory}")
        return

    files = get_md_files(directory, warn=logger.warning)
    if not files:
        logger.warning(f"在 {directory} 及其子文件夹中没有找到 .md/.txt 文件")
        return

    parsed = [parse_md_file(f, warn=logger.warning) for f in files]
    if unique_titles:
        parsed = deduplicate_titles(parsed)

    # 获取平台章节列表
    logger.info("正在获取平台章节列表...")
    chapter_manage_url = CHAPTER_MANAGE_URL_TPL.format(book_id=book_id)

    async with async_playwright() as p:
        browser, context = await create_context(p, headless=headless)
        page = await context.new_page()

        await page.goto(chapter_manage_url)
        await page.wait_for_load_state("networkidle")

        platform_chapters, _ = await extract_chapters_from_page(page, book_id)

        if not platform_chapters:
            logger.warning("未在平台找到章节。请检查 Book ID 和登录状态。")
            await browser.close()
            return

        logger.info(f"平台共有 {len(platform_chapters)} 个章节。")

        # 匹配
        matched, unmatched = match_chapters(
            parsed,
            platform_chapters,
            number_only=match_number_only,
            exclude_draft=True,
        )

        if not matched:
            logger.warning("没有匹配到任何章节！请检查本地文件是否包含章节号。")
            await browser.close()
            return

        # 预览
        logger.info(f"匹配到 {len(matched)} 个章节:")
        logger.info("-" * 60)
        total_words = 0
        for local_idx, plat_ch, ch_num, title, content in matched:
            wc = len(strip_md_formatting(content))
            total_words += wc
            logger.info(f"  第{ch_num}章 {title} ({wc}字) -> {plat_ch['title']}")
        logger.info("-" * 60)
        logger.info(f"总计: {len(matched)} 章, {total_words} 字")
        logger.info(
            "匹配规则: 仅章节号" if match_number_only else "匹配规则: 章节号+标题"
        )

        if unmatched:
            logger.warning(f"未匹配 (跳过) {len(unmatched)} 个本地文件:")
            for local_idx, ch_num, title in unmatched:
                reason = "无章节号" if ch_num is None else "平台无此章"
                logger.info(f"  {title} ({reason})")

        logger.info("")
        confirm = input("确认修改? (y/N): ").strip().lower()
        if confirm != "y":
            logger.info("已取消。")
            await browser.close()
            return

        # 执行修改
        success = 0
        failed = 0
        total = len(matched)

        for i, (local_idx, plat_ch, ch_num, title, content) in enumerate(matched):
            logger.info(f"[{i+1}/{total}] 修改第{ch_num}章 {title}")

            try:
                if await edit_one_chapter(page, book_id, plat_ch, ch_num, title, content,
                                          use_ai=use_ai,
                                          max_retries=cfg.get("max_retries", 2)):
                    success += 1
                else:
                    failed += 1
            except DailyLimitReached as e:
                logger.warning(f"{e}")
                logger.warning("触发平台当日字数上限，停止当前任务，不再重试。")
                failed += 1
                break

            if i < total - 1 and delay > 0:
                await page.wait_for_timeout(delay * 1000)

        await save_auth(context)
        await browser.close()

        logger.info("")
        logger.info("=" * 40)
        logger.info(f"  修改完成! 成功: {success}  失败: {failed}")
        logger.info("=" * 40)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="番茄作家 MD 批量上传工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s login                               登录番茄作家
  %(prog)s books                               列出你的作品
  %(prog)s upload ./chapters --book-id 12345   上传章节(存草稿)
  %(prog)s upload ./chapters --book-id 12345 --publish  上传并发布

定时发布:
  %(prog)s upload ./chapters --book-id 12345 --schedule 2026-03-14
      从 3/14 起每天 1 章, 默认 08:00 发布

  %(prog)s upload ./chapters --book-id 12345 --schedule 2026-03-14 --per-day 3
      从 3/14 起每天 3 章

修改已有章节:
  %(prog)s upload ./chapters --book-id 12345 --edit
      按章节号匹配并修改已有章节内容
        """,
    )
    sub = parser.add_subparsers(dest="command")

    # login
    sub.add_parser("login", help="登录番茄作家并保存会话")

    # books
    sub.add_parser("books", help="列出你的作品及 Book ID")

    # upload
    up = sub.add_parser("upload", help="批量上传 MD 文件到指定作品")
    up.add_argument("directory", type=Path, help="MD 文件所在目录")
    up.add_argument("--book-id", required=True, help="目标作品 ID")
    up.add_argument("--publish", action="store_true", help="直接发布 (默认仅存草稿)")
    up.add_argument("--headless", action="store_true", help="无头模式 (不显示浏览器)")
    up.add_argument(
        "--delay", type=int, default=None, help="章节间等待秒数 (默认 3)"
    )
    up.add_argument(
        "--schedule", metavar="DATE",
        help="定时发布起始日期, 格式 YYYY-MM-DD (如 2026-03-14)",
    )
    up.add_argument(
        "--time", default="08:00",
        help="定时发布时间, 如 08:00 或 08:00,12:00,20:00 (多时间逗号分隔)",
    )
    up.add_argument(
        "--per-day", type=int, default=1,
        help="每天发布章数 (默认 1)",
    )
    up.add_argument(
        "--unique-titles", action="store_true",
        help="自动给重复标题追加章节号后缀 (如 '选择' -> '选择（39）')",
    )
    up.add_argument(
        "--use-ai", action="store_true",
        help="发布时选择使用AI (默认不使用)",
    )
    up.add_argument(
        "--edit", action="store_true",
        help="修改已有章节 (默认按章节号+标题匹配, 不可与 --publish/--schedule 同时使用)",
    )
    up.add_argument(
        "--match-number-only", action="store_true",
        help="修改内容时仅按章节号匹配, 忽略标题差异",
    )

    args = parser.parse_args()
    setup_logging(LOG_FILE)

    if args.command == "login":
        asyncio.run(cmd_login())
    elif args.command == "books":
        asyncio.run(cmd_books())
    elif args.command == "upload":
        if getattr(args, "edit", False):
            if getattr(args, "publish", False) or getattr(args, "schedule", None):
                parser.error("--edit 不可与 --publish 或 --schedule 同时使用")
            asyncio.run(cmd_edit(args.directory, args.book_id, args))
        else:
            asyncio.run(
                cmd_upload(args.directory, args.book_id, args.publish, args)
            )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
