# Changelog

本文件记录这个 fork 相对上游 `rockbenben/fanqie-publisher` 的主要定制改动。

## Unreleased

### Added

- 新增个人工作流默认配置：
  - 默认章节目录为 `C:\Users\lc\Desktop\novel`
  - 默认模式为 `draft`
  - 默认发布时间为 `08:00,12:00,20:00`
  - 默认书号支持配置为 `7621903661345541144`
- 新增新建章节分卷能力：
  - 支持在 GUI 手动选择新建章节目标分卷
  - 支持配置新建章节默认分卷
  - 支持按章节号自动切卷
- 新增 GUI “工作流预设”区域：
  - 默认书号
  - 新建章节默认分卷
  - 章节号分卷规则
- 新增预览区分卷展示：
  - 预览摘要显示本次上传目标分卷
  - 章节列表逐章显示最终落卷结果

### Changed

- README 已重写为个人 fork 版本，不再沿用上游通用介绍
- GUI 刷新作品后，优先按 `preferred_book_id` 自动选中目标作品
- 上传主链中，新建章节分卷优先级调整为：
  1. GUI 手动选择分卷
  2. `default_new_chapter_volume`
  3. `new_chapter_volume_rules`
  4. 平台当前默认卷
- `fanqie_upload.py` 已扩展为支持新建章节页的编辑器分卷切换，而不只章节管理页切卷

### Fixed

- 修复 GUI 在卷信息回填时触发 `_show_volumes()` 与 `_on_mode_change()` 相互调用导致的递归崩溃问题
- 修复新建章节分卷能力只覆盖上传逻辑、不覆盖 GUI 预览的问题

## Notes

- 这是个人工作流 fork，不以“完全通用”作为第一目标
- 后续若要公开分发，建议补充版本号与发布日期
