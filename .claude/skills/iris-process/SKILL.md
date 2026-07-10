---
name: iris-process
version: 1.0.0
description: Iris 富媒体处理 — 自动检测图片/PDF/文档输入，调用 route-model 确认路由，走三阶段 process 流水线处理。当用户消息中含有图片、PDF、文档、截图路径时使用。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 scripts/run_cli.py process --help"
---

# Iris 富媒体处理

当用户输入包含图片、PDF、文档等非文本内容时，自动完成：路由决策 → 三阶段流水线处理 → 结果呈现。

## 支持的文件类型

| 类型 | 扩展名 | 支持程度 |
|------|--------|----------|
| 图片 | `.png` `.jpg` `.jpeg` `.gif` `.webp` `.bmp` | ✅ 完整三阶段 |
| PDF | `.pdf` | ⚠️ Stage 2 跳过，Stage 3 基于文件名回答，建议先转图片 |
| 文档 | `.doc` `.docx` | ⚠️ 同 PDF |
| 视频 | `.mp4` `.mov` `.avi` `.mkv` `.webm` | ❌ 需先提取帧，见下文 |

## Claude 的完整工作流

### 步骤 1：检测 & 提取文件路径

从用户消息中识别文件路径信号：

- **路径信号**：消息中包含带上述扩展名的路径字符串（绝对路径或 `~/` 开头）
- **截图信号**：用户说"截图"、"图片"、"这张图"但未给路径 → 询问文件路径，或提示保存方式：
  ```bash
  # macOS 截图默认保存到桌面，直接提供路径即可
  # 或者从剪贴板保存：
  osascript -e 'set theFile to (open for access POSIX file "/tmp/screenshot.png" with write permission)' ...
  ```
- **多文件**：`--image` 参数支持逗号分隔多路径。注意：用户消息中若写 `/a.png,/b.jpg`（无空格），无法自动提取，Claude 应手动按逗号拆分后传入

验证文件存在：

```python
# Claude 用 Bash 工具验证
ls -lh "<文件路径>"
```

如果文件不存在，告知用户并停止，不进入路由阶段。

### 步骤 2：调用 route-model

```bash
python scripts/run_cli.py route-model --context '{"input_type": "multimodal"}' --pretty
```

解析输出中的 `selected_role` 和 `matched_rule`，向用户透明展示：

> 📡 路由决策：`adv_model`（规则：`multimodal_input_go_adv`）

如果路由结果是 `base_model`（说明配置有变化），仍继续执行但提示用户当前模型可能不支持多模态。

### 步骤 3：执行 process 流水线

```bash
python scripts/run_cli.py process \
  --query "<用户的问题或指令>" \
  --image "<路径1,路径2,...>"
```

**关于 `--query` 的构建**：
- 用户有明确问题 → 直接用
- 用户只提供了文件没有问题 → 默认用「请分析这份文件，提取关键信息」
- 用户想结合知识库 → 在 query 中体现（process 会自动检索 Wiki 上下文）

**三阶段流水线说明**（运行时打印进度，Claude 无需干预）：
- Stage 1 (base_model)：动态生成针对该文件类型的分析指令
- Stage 2 (adv_model)：多模态理解图片/PDF 内容
- Stage 3 (base_model)：整合知识库上下文，润色输出

### 步骤 4：呈现结果

在对话中展示：
1. 使用模型（来自路由结果）
2. 文件类型和路径
3. process 输出的完整分析结果

---

## 特殊情况处理

### 视频文件

`process` 命令不直接支持视频流。遇到视频文件时：

- **如果是会议录音** → 建议转用 `iris-meeting` Skill
- **如果是需要视觉理解的视频** → 提示用户先提取关键帧：
  ```bash
  # 每秒提取 1 帧，保存到 /tmp/frames/
  ffmpeg -i input.mp4 -vf fps=1 /tmp/frames/frame_%04d.png
  ```
  再对关键帧执行 process

### 用户想要快速轻量分析

用户明确说「快速看一下」「不用深度分析」时，降级到单阶段：

```bash
python scripts/run_cli.py ask --query "<问题>" --image "<路径>"
```

### 多张图片对比

多张图片一起送入时，`--image` 用逗号分隔，process 会在 Stage 2 一起处理：

```bash
python scripts/run_cli.py process \
  --query "对比这两张架构图的差异" \
  --image "/path/a.png,/path/b.png"
```

### 用户给了 URL 而非本地文件

URL 无法直接传给 process。建议用 `curl` 或 `wget` 先下载：

```bash
curl -L "<URL>" -o /tmp/downloaded_image.png
```
再执行 process。

---

## 常见场景

| 用户说 | Claude 操作 |
|--------|-------------|
| 「帮我分析 /path/report.pdf」 | 验证路径 → route-model → `process --query "分析这份报告" --image "/path/report.pdf"` |
| 「看看这张截图有什么问题」 | 询问截图路径 → route-model → process |
| 「这两张图哪里不同？」 | 提取两个路径 → route-model → `process --image "path1,path2"` |
| 「分析一下这个架构图，结合我们项目的情况」 | route-model → `process --query "结合知识库中的项目信息分析架构图" --image "..."` |
| 「快速看一下这张图」 | route-model → 降级到 `ask --image "..."` |
| 「这个会议录音帮我整理一下」 | 转交 `iris-meeting` Skill |

## 常见错误处理

| 错误 | 处理方式 |
|------|----------|
| 文件路径不存在 | 停止流程，提示用户确认路径或用 `find` 搜索 |
| 文件过大（>20MB） | 提示压缩图片后重试，或裁剪相关区域 |
| adv_model 不可用 | route-model 输出会提示；建议检查 `config/llm.json` 中 adv_model 的 `enabled` 状态 |
| process 超时 | Stage 2 可能因大文件超时；建议压缩或拆分文件 |
| 视频格式不支持 | 提示用 ffmpeg 提取帧后重试 |
