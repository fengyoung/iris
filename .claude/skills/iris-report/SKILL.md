---
name: iris-report
version: 1.0.0
description: Iris 报告生成 — 分析报告、思维导图、双周报。当用户需要生成专题分析报告、创建思维导图、写双周报时使用。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 scripts/run_cli.py build-report --help"
---

# Iris 报告生成

从知识库中检索相关内容，生成三种类型的报告产出。

## 报告类型

### 1. 分析报告 (`build-report`)

```bash
python3 scripts/run_cli.py build-report --query "<主题>" [选项]
```

**选项**：
- `--top-k 10`：检索更多证据（默认 5）
- `--output-format md`：输出 Markdown（默认）
- `--output-format docx`：输出 Word 文档
- `--output-format standard`：标准格式，含摘要+正文+参考
- `--output-format compact`：紧凑格式
- `--output-file <路径>`：保存到指定文件
- `--two-stage`：两阶段生成（先大纲后正文，质量更高但更慢）

### 2. 思维导图 (`build-mindmap`)

```bash
python3 scripts/run_cli.py build-mindmap --query "<主题>" [选项]
```

**选项**：
- `--format mermaid`：输出 Mermaid 代码（可在 Markdown 中渲染）
- `--format xmind`：输出 XMind 文件（`.xmind`）
- `--format both`：两种都输出
- `--output-file <路径>`：保存到指定文件

### 3. 双周报 (`build-biweekly-report`)

```bash
python3 scripts/run_cli.py build-biweekly-report [选项]
```

**选项**：
- `--query "<补充说明>"`：额外的重点关注内容
- `--to-source`：归档到 `SOURCE/06-我的周报/`
- `--output-file <路径>`：手动指定输出路径

## Claude 的工作流程

### 分析报告

**步骤 1：精炼查询**

用户的想法往往是模糊的（「帮我分析一下产品方向」），Claude 应该：
1. 追问 1-2 个关键问题明确范围
2. 将模糊需求转化为精准的检索查询

**步骤 2：选择格式**

| 用户说 | 选择 |
|--------|------|
| 「写个报告」 | `--output-format standard --output-file <路径>.md` |
| 「导出 Word」 | `--output-format docx --output-file <路径>.docx` |
| 「快速总结」 | `--output-format compact` |
| 「深度分析」 | `--two-stage --output-format standard` |

**步骤 3：执行并展示**

```bash
python3 scripts/run_cli.py build-report --query "<精炼后的查询>" --output-format standard
```

在对话中展示报告摘要，标注来源和关键发现。

### 思维导图

**步骤 1：确定格式**

- 如果用户想在对话中查看 → `--format mermaid`（Claude 可以渲染）
- 如果用户想导入 XMind → `--format xmind`
- 不确定 → `--format both`

**步骤 2：执行**

```bash
python3 scripts/run_cli.py build-mindmap --query "<查询>" --format both
```

Mermaid 格式的导图可以在对话中直接展示。

### 双周报

**步骤 1：确认时间范围**

双周报覆盖最近两周。Claude 自动计算日期范围。

**步骤 2：执行**

```bash
python3 scripts/run_cli.py build-biweekly-report --mode llm --to-source
```

**可选输出路径：**

```bash
python3 scripts/run_cli.py build-biweekly-report --mode llm --output-file output/双周报.md
```

**步骤 3：展示摘要**

在对话中展示报告关键内容，提醒用户文件已归档到 SOURCE。

## 常见场景

| 用户说 | Claude 操作 |
|--------|-------------|
| 「帮我写一份关于微服务的分析报告」 | `iris build-report --query "微服务架构 最佳实践 经验总结" --output-format standard` |
| 「画个产品架构的思维导图」 | `iris build-mindmap --query "产品架构 模块设计" --format mermaid` |
| 「这周的双周报」 | `iris build-biweekly-report --to-source` |
| 「导出 Word 版本」 | 先生成 md，再用 `--output-format docx` 转换 |

## 常见错误处理

| 错误 | 处理方式 |
|------|----------|
| 检索结果太少 | 建议扩大检索范围或调整查询词 |
| 报告质量不高 | 建议使用 `--two-stage` 模式重新生成 |
| DOCX 转换失败 | 检查 `python-docx` 是否安装：`pip install python-docx` |
| XMind 生成失败 | 检查输出目录写权限 |
