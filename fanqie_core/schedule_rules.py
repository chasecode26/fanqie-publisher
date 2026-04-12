"""发布排期相关纯逻辑。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta


def validate_times(raw: str) -> list[str]:
    """解析、校验、排序、去重时间字符串，输出 HH:MM 列表。"""
    raw = str(raw or "")
    raw = raw.replace("\uff0c", ",").replace("\uff1b", ",").replace(";", ",")
    result: list[str] = []
    for t in raw.split(","):
        t = t.strip().replace("\uff1a", ":")
        m = re.match(r"^(\d{1,2}):(\d{2})$", t)
        if not m:
            continue
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            result.append(f"{h:02d}:{mi:02d}")
    return list(dict.fromkeys(sorted(result)))


def compute_schedule(
    file_count: int,
    start_date: str,
    pub_time: str,
    per_day: int,
) -> list[tuple[str, str]]:
    """计算每章的发布日期与时间。"""
    per_day = max(1, per_day)
    base = datetime.strptime(start_date, "%Y-%m-%d")
    times = validate_times(pub_time) or ["08:00"]
    effective = max(per_day, len(times))

    if len(times) < effective:
        n_times = len(times)
        cap_global = datetime.strptime("23:59", "%H:%M")
        parsed_times = [datetime.strptime(t, "%H:%M") for t in times]
        expanded: list[str] = []
        for t_idx in range(n_times):
            count = effective // n_times + (1 if t_idx < effective % n_times else 0)
            base_t = parsed_times[t_idx]
            slot_cap = (
                parsed_times[t_idx + 1] - timedelta(minutes=1)
                if t_idx + 1 < n_times
                else cap_global
            )
            for j in range(count):
                nxt = base_t + timedelta(minutes=j)
                if nxt > slot_cap:
                    nxt = slot_cap
                expanded.append(nxt.strftime("%H:%M"))
        times = expanded

    schedule: list[tuple[str, str]] = []
    for i in range(file_count):
        day_offset = i // effective
        d = base + timedelta(days=day_offset)
        slot = i % effective
        schedule.append((d.strftime("%Y-%m-%d"), times[slot]))
    return schedule

