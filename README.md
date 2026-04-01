# fanqie-publisher

个人 fork 仓库：[`chasecode26/fanqie-publisher`](https://github.com/chasecode26/fanqie-publisher)

这是一个基于 [`rockbenben/fanqie-publisher`](https://github.com/rockbenben/fanqie-publisher) 的个人 fork，已经按我的连载工作流做过定制。

当前目标不是做通用的“番茄作家批量上传工具”，而是做一套更贴合我自己小说发布、草稿整理、分卷管理、定时排期的发布器。

## 这个 fork 做了什么

- 保留原项目的核心上传能力
  - 登录态保存
  - 批量上传 `.md/.txt`
  - 存草稿 / 立即发布 / 定时发布
  - 修改内容 / 修改排期
  - Markdown 清理、失败重试、日志导出
- 增加了我的工作流定制
  - 默认章节目录指向 `C:\Users\lc\Desktop\novel`
  - 默认模式改为 `存草稿`
  - 默认发布时间改为 `08:00,12:00,20:00`
  - 默认书号支持在 GUI 中配置
  - 新建章节支持手动选择分卷
  - 新建章节支持默认分卷 / 按章节号自动切卷
  - 预览区会显示每章最终将落入的分卷

## 当前默认规则

当前 `config.json` 里已经配置了这套规则：

- 默认书号：`7621903661345541144`
- 默认章节目录：`C:\Users\lc\Desktop\novel`
- 默认模式：`draft`
- 默认发布时间：`08:00,12:00,20:00`
- 自动分卷规则：
  - `第21章及以后 -> 第二卷：回归`

如果 GUI 里手动选择了“新建章节分卷”，则以手动选择为准，高于自动规则。

## 和上游仓库的区别

这个 fork 现在更偏“个人发布器”，不是完全通用工具。

主要差异：

- 增加“工作流预设”面板
- 支持默认书号
- 支持新建章节手动分卷
- 支持新建章节默认分卷 / 规则分卷
- 预览区直接显示章节落卷结果

如果你想回到更通用的用法，请参考上游项目的 README。

## 仓库定位

- 上游仓库：[`rockbenben/fanqie-publisher`](https://github.com/rockbenben/fanqie-publisher)
- 当前 fork：[`chasecode26/fanqie-publisher`](https://github.com/chasecode26/fanqie-publisher)
- 当前定位：个人工作流发布器，而不是完全通用的番茄上传工具

## 快速开始

### 依赖

- Python 3.10+
- Playwright Chromium
- Windows 推荐直接双击 `run.bat`

首次运行需要安装：

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 启动 GUI

```bash
python fanqie_gui.py
```

或直接运行：

```bash
run.bat
```

## 推荐使用流程

### 1. 登录

- 打开 GUI
- 点击 `登录/新建`
- 在番茄作家后台完成登录
- 保存会话

### 2. 刷新作品列表

- 点击 `刷新作品列表`
- 如果设置了默认书号，会优先选中对应作品

### 3. 检查工作流预设

GUI 中可直接配置：

- 默认书号
- 新建章节默认分卷
- 分卷规则：`第 N 章及以后 -> 某卷`

### 4. 选择章节目录

默认会读取：

```text
C:\Users\lc\Desktop\novel
```

支持子目录中的 `.md` / `.txt` 文件，按自然顺序上传。

### 5. 选择操作模式

- `存草稿`
- `立即发布`
- `定时发布`
- `修改内容`
- `修改排期`

我当前的日常工作流默认使用：

- `存草稿`

### 6. 手动选择新建章节分卷

当作品存在多卷时，在 `存草稿 / 立即发布 / 定时发布` 模式下，GUI 会显示：

- `新建章节分卷`

规则优先级：

1. 手动选择分卷
2. 默认分卷
3. 按章节号自动切卷
4. 平台当前默认卷

### 7. 查看预览并开始上传

预览区会显示：

- 章节号
- 标题
- 字数
- 排期
- 最终分卷，例如：

```text
第21章 旧部见主 <第二卷：回归>
```

确认后点击 `开始上传`。

## 配置文件

配置文件为：

- [config.json](D:\git\fanqie-publisher\config.json)

当前示例：

```json
{
  "delay_between_chapters": 3,
  "headless": false,
  "max_retries": 2,
  "default_mode": "draft",
  "default_per_day": 3,
  "default_time": "08:00,12:00,20:00",
  "browser_timeout": 15000,
  "default_new_chapter_volume": "",
  "new_chapter_volume_rules": [
    {
      "min_chapter": 21,
      "volume": "第二卷：回归"
    }
  ],
  "chapters_dir": "C:\\Users\\lc\\Desktop\\novel",
  "preferred_book_id": "7621903661345541144"
}
```

关键字段说明：

- `preferred_book_id`
  - 默认选中的作品书号
- `chapters_dir`
  - 默认章节目录
- `default_mode`
  - 默认操作模式
- `default_time`
  - 默认发布时间，支持多个时间点
- `default_new_chapter_volume`
  - 新建章节默认分卷
- `new_chapter_volume_rules`
  - 按章节号切卷规则

## 目录结构

```text
fanqie-publisher/
├── fanqie_gui.py
├── fanqie_upload.py
├── config.json
├── requirements.txt
├── run.bat
├── run.sh
├── .auth_state.json
├── .auth_*.json
├── .gui_state.json
└── fanqie_error.log
```

## 当前更适合谁

这份 fork 更适合：

- 我自己长期连载使用
- 有固定书号、固定目录、固定排期习惯的人
- 需要频繁存草稿、调分卷、调排期的人

如果你需要完全通用的分发工具，请直接参考上游项目。

## 已知限制

- GUI 当前仍是单配置模型，不是多 profile 系统
- 分卷逻辑已针对我的番茄工作流做了增强，但还不是“多本书全自动模板中心”
- 某些番茄页面 DOM 变化后，仍可能需要继续维护

## 后续计划

- 支持多本书 profile
- 支持更完整的分卷规则编辑
- 支持更清晰的上传结果汇总
- 支持更稳定的 GUI 实跑验证链

## 维护说明

- 这份仓库当前按我的连载发布流程维护
- 新增能力优先服务“固定目录 + 固定书号 + 固定分卷规则 + 草稿优先”的工作流
- 如果后续要公开给更多人使用，建议再抽离通用配置层与多 profile 支持

## 致谢

- 上游项目：[`rockbenben/fanqie-publisher`](https://github.com/rockbenben/fanqie-publisher)
- 本 fork 在其基础上继续做个人工作流定制
