# Iris Caption 标志微变体 · 待确认

基准：Iris v1.4 已确认主图标。原 SVG 的三条曲线路径、位置、银灰渐变、96 × 96 画布与黑色圆角底均直接保留；只在曲线下方留白增加独立可编辑提示组。没有修改产品资源或规格。

- **A · 双行字幕（推荐）**：两条长短不同的圆端横线，提示实时字幕。新增细节最少，32 px 下保留两行节奏，最符合“小变体”。
- **B · 轻声波**：四条短音频柱，更强调录音、声音输入与转写。
- **C · 字幕框**：圆角框内两条字幕线，语义最明确；32 px 下细节比 A 更密集。

`contact-sheet.png` 并列原版和 A/B/C，含浅色与深色面板、160 px 主预览及实际 64/32 px 小图。

可编辑源文件：`reference-original.svg`（基准副本）、`iris-caption-A.svg`、`iris-caption-B.svg`、`iris-caption-C.svg`。提示位于各 SVG 末尾的独立命名组，便于调整。`original/A/B/C-32/64.png` 为独立小图。`preview.html` 可离线浏览；无外部资源。

`render_drafts.py` 使用本地 PySide6 QtSvg 离屏渲染，以 PingFang SC 绘制中文标签；同时断言原始路径属性及渐变定义未改变。复现：在项目根执行 `python3 apps/iris-caption/design/logo/render_drafts.py`。

当前仅为设计评审稿。选定方案后再决定是否应用。
