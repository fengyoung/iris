---
name: iris-ask
version: 1.0.0
description: Iris 智能问答 — 基于本地知识库的检索增强问答（纯文本）。当用户需要查询个人知识库、搜索项目/会议/讨论内容时使用。含图片/PDF/文档等富媒体时改用 iris-process。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 scripts/run_cli.py --call-source skill ask --help"
---

# Iris 智能问答

基于 Iris 知识库的检索增强问答（RAG），专注纯文本问答。含图片/PDF/文档等富媒体时，使用 **iris-process** Skill。

## 核心命令

### 纯文本问答

```bash
python3 scripts/run_cli.py --call-source skill ask --query "<问题>"
```

可选的增强参数：
- `--top-k 10`：增加检索块数量（默认 5）
- `--mode llm`：强制 LLM 模式（默认 local，仅证据组合）

## Claude 的工作流程

### 文本问答

用户提问时 Claude 应该：

1. **判断是否需要知识库检索**：
   - 如果问题是关于用户的工作、项目、团队 → 使用 `iris ask`
   - 如果问题是通用知识 → 直接回答，不需要 `iris ask`
   - 如果用户明确要求「查一下」→ 使用 `iris ask`

2. **检索并整合**：

```bash
python3 scripts/run_cli.py --call-source skill ask --query "<用户问题>"
```

3. **解读结果**：
   - 在对话中呈现答案，标注来源
   - 如果答案不够好，尝试 `--top-k 10` 或 `--mode llm`

### 富媒体输入

用户消息中含有图片/PDF/文档路径时 → **转交 iris-process Skill**，不在此处处理。

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
| 「有哪些关于产品的文档？」 | `iris search --query "产品"` |
| 「这张架构图有什么问题？」 | 转交 **iris-process** |
| 「帮我看看这个 PDF」 | 转交 **iris-process** |

## 常见错误处理

| 错误 | 处理方式 |
|------|----------|
| 检索无结果 | 建议调整查询词，或先运行 `scan-source` 更新索引 |
| 图片太大 | 自动压缩或提示用户缩小图片（上限 20MB） |
| LLM 超时 | 降级到 `local` 模式直接展示证据块 |
