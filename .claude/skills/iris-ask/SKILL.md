---
name: iris-ask
version: 1.0.0
description: Iris 智能问答 — 基于本地知识库的检索增强问答，支持文本和多模态（图片/PDF）输入。当用户需要查询个人知识库、分析图片内容、结合知识库理解文档时使用。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 scripts/run_cli.py ask --help"
---

# Iris 智能问答

基于 Iris 知识库的检索增强问答（RAG），支持纯文本问答和多模态理解。

## 核心命令

### 纯文本问答

```bash
python3 scripts/run_cli.py ask --query "<问题>"
```

可选的增强参数：
- `--top-k 10`：增加检索块数量（默认 5）
- `--mode llm`：强制 LLM 模式（默认 local，仅证据组合）

### 多模态问答

```bash
python3 scripts/run_cli.py ask --query "<问题>" --image "<图片路径1>,<图片路径2>"
```

Iris 会自动检测 query 中的文件路径，所以也可以：

```bash
python3 scripts/run_cli.py ask --query "分析 /path/to/image.png 中的架构图"
```

### 复杂输入处理

```bash
python3 scripts/run_cli.py process --query "<问题>" --image "<图片路径>"
```

使用三阶段流水线：
- Stage 1 (base model)：生成多模态分析指令
- Stage 2 (adv model)：图片/文档理解
- Stage 3 (base model)：整合润色

## Claude 的工作流程

### 文本问答

用户提问时 Claude 应该：

1. **判断是否需要知识库检索**：
   - 如果问题是关于用户的工作、项目、团队 → 使用 `iris ask`
   - 如果问题是通用知识 → 直接回答，不需要 `iris ask`
   - 如果用户明确要求「查一下」→ 使用 `iris ask`

2. **检索并整合**：

```bash
python3 scripts/run_cli.py ask --query "<用户问题>"
```

3. **解读结果**：
   - 在对话中呈现答案，标注来源
   - 如果答案不够好，尝试 `--top-k 10` 或 `--mode llm`

### 多模态问答

用户发图片时：

1. **判断是否需要用 Iris**：
   - 图片是工作相关（架构图、流程图、截图）→ 可能需要知识库上下文
   - 图片是一般性的（风景、人物）→ Claude 直接理解即可

2. **需要知识库上下文时**：将图片保存到临时文件，然后：

```bash
python3 scripts/run_cli.py ask --query "<问题>" --image "<临时文件路径>"
```

3. **不需要知识库上下文时**：Claude 直接用自己的视觉能力理解图片。

### 搜索 vs 问答

Claude 应区分两个命令：

- `search`：只做检索，返回相关文档块（适合「有哪些关于 XX 的文档？」）
- `ask`：检索 + LLM 回答（适合「XX 是什么？」「总结一下 XX」）

## 问答模式对比

| 模式 | 命令 | 说明 |
|------|------|------|
| local | `ask --query "..." --mode local` | 直接组合证据块，不调用 LLM |
| llm | `ask --query "..." --mode llm` | LLM 理解问题并综合回答 |

默认模式由配置决定，一般不需要手动指定。

## 常见场景

| 用户问法 | Claude 操作 |
|---------|-------------|
| 「XX 项目进展如何？」 | `iris ask --query "XX 项目进展"` |
| 「总结一下上周的讨论」 | `iris ask --query "上周讨论要点"` |
| 「这张架构图有什么问题？」 | 保存图片 → `iris ask --query "分析架构图中的问题" --image "..."` |
| 「有哪些关于产品的文档？」 | `iris search --query "产品"` |
| 「帮我看看这个 PDF」 | `iris process --query "分析这个文档" --image "..."` |

## 常见错误处理

| 错误 | 处理方式 |
|------|----------|
| 检索无结果 | 建议调整查询词，或先运行 `scan-source` 更新索引 |
| 图片太大 | 自动压缩或提示用户缩小图片（上限 20MB） |
| LLM 超时 | 降级到 `local` 模式直接展示证据块 |
