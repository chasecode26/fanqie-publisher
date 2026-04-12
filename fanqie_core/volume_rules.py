"""分卷解析与按章节号选卷规则（纯逻辑）。"""

from __future__ import annotations

import re
import unicodedata

_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def _cn_to_int(cn: str) -> int:
    cn = re.sub(r"\s+", "", cn)
    result, current = 0, 0
    for ch in cn:
        val = _CN_DIGITS.get(ch)
        if val is not None:
            current = val
            continue
        unit = _CN_UNITS.get(ch)
        if unit:
            if current == 0:
                current = 1
            result += current * unit
            current = 0
    return result + current


def _volume_option_texts(volumes) -> list[str]:
    texts: list[str] = []
    for item in volumes or []:
        if isinstance(item, dict):
            text = str(item.get("text", "") or "").strip()
        else:
            text = str(item or "").strip()
        if text and text not in texts:
            texts.append(text)
    return texts


def _normalize_volume_text(text: str | None) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("\u200b", "").replace("\ufeff", "").strip()
    return re.sub(r"\s+", "", value)


def _extract_volume_index(text: str | None) -> int | None:
    value = _normalize_volume_text(text)
    if not value:
        return None
    if value.isdigit():
        num = int(value)
        return num if num > 0 else None

    patterns = (
        r"^第?(\d+)卷(?:[:：].*)?$",
        r"^卷(\d+)(?:[:：].*)?$",
        r"^第?([零〇一二两三四五六七八九十百千]+)卷(?:[:：].*)?$",
        r"^卷([零〇一二两三四五六七八九十百千]+)(?:[:：].*)?$",
    )
    for pattern in patterns[:2]:
        m = re.match(pattern, value)
        if m:
            num = int(m.group(1))
            return num if num > 0 else None
    for pattern in patterns[2:]:
        m = re.match(pattern, value)
        if not m:
            continue
        num = _cn_to_int(m.group(1))
        return num if num > 0 else None
    return None


def resolve_volume_name(
    target: str | None,
    volumes,
    current_volume: str | None = "",
) -> str:
    """将配置中的卷标识解析为平台真实卷名。"""
    raw_target = str(target or "").strip()
    if not raw_target:
        return ""

    texts = _volume_option_texts(volumes)
    current = str(current_volume or "").strip()
    if current and current not in texts:
        texts.insert(0, current)
    if not texts:
        return raw_target

    normalized_target = _normalize_volume_text(raw_target)
    for text in texts:
        if _normalize_volume_text(text) == normalized_target:
            return text

    prefix_matches = [
        text
        for text in texts
        if _normalize_volume_text(text).startswith(normalized_target)
        or normalized_target.startswith(_normalize_volume_text(text))
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]

    target_index = _extract_volume_index(raw_target)
    if target_index is None:
        return raw_target

    indexed_matches = [text for text in texts if _extract_volume_index(text) == target_index]
    if len(indexed_matches) == 1:
        return indexed_matches[0]

    if 1 <= target_index <= len(texts):
        return texts[target_index - 1]

    return raw_target


def resolve_new_chapter_volume(
    chapter_num: str | None,
    cfg: dict,
    volumes=None,
    current_volume: str | None = "",
) -> str:
    """根据配置与章节号决定新建章节应落入的分卷。"""
    chosen = resolve_volume_name(
        cfg.get("default_new_chapter_volume", ""),
        volumes,
        current_volume,
    )
    if not chapter_num:
        return chosen
    try:
        num = int(chapter_num)
    except (TypeError, ValueError):
        return chosen

    rules = cfg.get("new_chapter_volume_rules", [])
    if not isinstance(rules, list):
        return chosen

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        volume = resolve_volume_name(
            rule.get("volume", ""),
            volumes,
            current_volume,
        )
        if not volume:
            continue
        min_ch = rule.get("min_chapter")
        max_ch = rule.get("max_chapter")
        try:
            if min_ch is not None and num < int(min_ch):
                continue
            if max_ch is not None and num > int(max_ch):
                continue
        except (TypeError, ValueError):
            continue
        return volume
    return chosen

