# fanqie-publisher

一个面向番茄作者日常连载发布的 Python 工具（GUI + CLI）。

> 本仓库在上游项目基础上做了工作流增强，重点优化了：
> - 新建章节分卷（默认卷 / 规则卷 / 手动卷）
> - 立即发布 / 定时发布流程稳定性
> - 修改已有章节与排期的批处理能力
> - 当日字数上限命中后的自动停止与提示

---

## 1. 功能概览

- 登录态保存与复用
- 批量读取 `chapters/` 下 `.md/.txt`
- 三种新建模式：
  - 存草稿
  - 立即发布
  - 定时发布
- 修改模式：
  - 修改内容并发布
  - 修改排期
- 分卷策略：
  - 手动指定
  - 默认分卷
  - 按章节号规则自动切卷
- 自动重试与错误截图
- 日志输出到 `fanqie_error.log`

---

## 2. 项目结构（已优化）

```text
fanqie-publisher/
?? fanqie_gui.py                 # GUI ??
?? fanqie_upload.py              # ????? CLI ??
?? fanqie_core/                  # ??????????
?  ?? daily_limit.py             # ????????
?  ?? chapter_match.py           # ??????
?  ?? schedule_rules.py          # ??????
?  ?? volume_rules.py            # ?????????
?  ?? chapter_text.py            # ?????????
?  ?? __init__.py
?? fanqie_web/                   # ????? JS ??
?  ?? js_snippets.py
?  ?? volume_ops.py
?  ?? manage_ops.py
?  ?? __init__.py
?? run.bat                       # Windows ??
?? run.sh                        # Linux/macOS ??
?? requirements.txt
?? config.json                   # ???????? .gitignore ???
?? chapters/                     # ???????? .gitignore ???
?? tests/                        # ????
?  ?? test_volume_resolution.py
?  ?? test_daily_limit_detection.py
?  ?? test_core_modules.py
?  ?? test_chapter_text.py
?  ?? test_manage_ops.py
?? docs/
   ?? PROJECT_STRUCTURE.md       # ????
   ?? TROUBLESHOOTING.md         # ??????
```

---

## 3. 快速开始

### 3.1 环境

- Python 3.10+
- Playwright Chromium

安装：

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 3.2 启动 GUI

```bash
python fanqie_gui.py
```

Windows 也可直接双击：

```bash
run.bat
```

### 3.3 CLI 示例

```bash
# 登录并保存会话
python fanqie_upload.py login

# 列出作品
python fanqie_upload.py books

# 批量上传为草稿
python fanqie_upload.py upload ./chapters --book-id <BOOK_ID>

# 立即发布
python fanqie_upload.py upload ./chapters --book-id <BOOK_ID> --publish

# 定时发布（示例：每天 3 章）
python fanqie_upload.py upload ./chapters --book-id <BOOK_ID> --schedule 2026-04-08 --per-day 3
```

---

## 4. 关键配置

`config.json` 常用字段：

- `preferred_book_id`：默认作品 ID
- `chapters_dir`：默认章节目录
- `default_mode`：默认模式（draft/publish/schedule/edit/reschedule）
- `default_time`：默认发布时间，可逗号分隔多个时间点
- `default_new_chapter_volume`：新建章节默认分卷（支持 `"1"` 这类写法）
- `new_chapter_volume_rules`：按章节号切卷规则
- `max_retries`：非平台限制错误时的重试次数

---

## 5. 稳定性说明（重要）

- 发布流程已做“下一步 / 发布设置 / 确认发布”多重兜底。
- 当命中番茄**当日发布字数上限**时：
  - 会给出明确提示
  - 会停止后续章节处理
  - 不会继续重试

---

## 6. 开发与验证

语法检查：

```bash
python -m py_compile fanqie_upload.py fanqie_gui.py fanqie_core/__init__.py fanqie_core/daily_limit.py fanqie_core/volume_rules.py fanqie_core/chapter_match.py fanqie_core/schedule_rules.py fanqie_core/chapter_text.py fanqie_web/__init__.py fanqie_web/js_snippets.py fanqie_web/volume_ops.py fanqie_web/manage_ops.py tests/test_volume_resolution.py tests/test_daily_limit_detection.py tests/test_core_modules.py tests/test_chapter_text.py tests/test_manage_ops.py
```

运行测试：

```bash
python -m unittest tests.test_volume_resolution tests.test_daily_limit_detection tests.test_core_modules tests.test_chapter_text tests.test_manage_ops
```

---

## 7. 致谢

- 上游项目：`rockbenben/fanqie-publisher`
- 本仓库面向个人连载工作流进行增强与维护
