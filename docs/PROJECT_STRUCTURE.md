# 项目结构

这个项目保留两个入口：图形界面给日常使用，命令行给批处理或临时排查使用。

```text
fanqie-publisher/
├─ fanqie_gui.py          # 图形界面入口
├─ fanqie_upload.py       # 命令行入口和发布流程编排
├─ fanqie_core/           # 不依赖浏览器的纯逻辑
├─ fanqie_web/            # 浏览器页面操作和 JS 片段
├─ docs/                  # 使用说明和排查文档
├─ chapters/              # 默认章节目录，本地使用
├─ run.bat                # Windows 启动脚本
├─ run.sh                 # macOS / Linux 启动脚本
├─ requirements.txt       # Python 依赖
└─ config.json            # 本地配置
```

## 主要模块

- `fanqie_gui.py`：负责界面、预览、章节筛选、任务启动和日志展示。
- `fanqie_upload.py`：负责登录、上传、保存草稿、发布、修改正文、修改排期。
- `fanqie_core/chapter_text.py`：章节标题、章节号和正文清洗。
- `fanqie_core/chapter_match.py`：本地章节和平台章节匹配。
- `fanqie_core/schedule_rules.py`：定时发布排期计算。
- `fanqie_core/volume_rules.py`：分卷名称解析和自动切卷规则。
- `fanqie_core/daily_limit.py`：平台当日发布上限识别。
- `fanqie_web/js_snippets.py`：页面内执行的 JS 片段。
- `fanqie_web/volume_ops.py`：章节管理页和编辑器里的分卷选择。
- `fanqie_web/manage_ops.py`：章节管理页翻页、定位章节和修改排期。

## 本地运行文件

以下文件是运行时产生或保存的个人配置，不建议提交：

- `.auth_*.json`
- `.auth_state.json`
- `.gui_state.json`
- `config.json`
- `fanqie_error.log`
- `chapters/`
- `__pycache__/`

## 整理原则

1. 登录态、配置和章节目录优先保留。
2. 缓存、运行日志、临时截图可以随时删除。
3. 新功能优先放到现有模块中，只有明显变大或复用价值高时再拆新文件。

