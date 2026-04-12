# 项目结构说明

本项目保持“单仓库、双入口（GUI + CLI）”的轻量结构，核心纯逻辑下沉到 `fanqie_core/`，
避免 `fanqie_upload.py` 持续膨胀为超大单文件。

## 核心文件

- `fanqie_upload.py`
  - 核心业务逻辑（上传、发布、定时、编辑、排期）
  - CLI 入口
- `fanqie_gui.py`
  - Tkinter GUI 入口
  - 调用 `fanqie_upload.py` 的核心能力
- `fanqie_core/`
  - `daily_limit.py`：当日字数上限识别策略
  - `chapter_match.py`：本地章节与平台章节匹配策略
  - `schedule_rules.py`：排期计算与时间解析策略
  - `volume_rules.py`：分卷解析与按章节号选卷规则
  - `chapter_text.py`????/?????Markdown ??????????
  - 仅包含纯逻辑，便于单元测试与复用
- `fanqie_web/`
  - `js_snippets.py`??? evaluate ?? JS ???????/?????
  - `volume_ops.py`?????/?????????
  - `manage_ops.py`????????/????/????????
  - `__init__.py`?????

## 运行与配置

- `config.json`：本地工作流配置
- `chapters/`：章节输入目录
- `.auth_state.json` / `.auth_*.json`：登录态
- `fanqie_error.log`：运行日志

## 质量保障

- `tests/test_volume_resolution.py`：分卷解析回归测试
- `tests/test_daily_limit_detection.py`：当日字数上限识别测试
- `tests/test_core_modules.py`：核心模块（匹配/排期/分卷/上限）回归测试
- `tests/test_chapter_text.py`?????????????
- `tests/test_manage_ops.py`????????????????

## 结构优化原则

1. 不破坏现有用户路径（尤其是 `config.json`、认证文件、`run.bat`）。
2. 纯逻辑优先放在 `fanqie_core/`，`fanqie_upload.py` 负责流程编排，GUI 只做调用与展示。
3. 每次功能修复都补充最小回归测试。
