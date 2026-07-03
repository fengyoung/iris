---
name: iris-feishu-import
version: 1.0.0
description: 飞书知识导入 — 将飞书文档和聊天记录提炼为本地 Markdown 知识库。当用户需要从飞书导入文档、提炼群聊讨论、批量抓取飞书内容时使用。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 scripts/run_cli.py feishu-doc-convert --help"
---

# Iris 飞书知识导入

从飞书生态导入知识到本地 SOURCE 知识库，包含两大管道：文档转换和聊天提炼。

## 管道 1：飞书文档转换

将飞书文档（Docx/Wiki）转换为本地 Markdown 文件。

### 基本用法

```bash
# 单篇文档
python3 scripts/run_cli.py feishu-doc-convert --url "<飞书文档URL>"

# 批量
python3 scripts/run_cli.py feishu-doc-convert --url "<URL1>" --url "<URL2>"

# 从配置文件批量导入
python3 scripts/run_cli.py feishu-doc-convert --from-config
```

### 预览模式（推荐先用）

```bash
python3 scripts/run_cli.py feishu-doc-convert --url "<URL>" --dry-run
```

`--dry-run` 会显示：
- 文档标题、作者、创建时间
- 路由目标（归档到哪个 SOURCE 子目录）
- 是否已存在（排重索引检查）
- 不会实际下载或写入文件

### Claude 的工作流程

**步骤 1：预览**

用户提供 URL 后，先用 `--dry-run` 预览：

```bash
python3 scripts/run_cli.py feishu-doc-convert --url "<用户提供的URL>" --dry-run
```

**步骤 2：呈现预览结果**

向用户展示：
- 📄 文档标题、作者、创建日期
- 📂 将归档到：`SOURCE/XX-分类/`
- ⚠️ 如果已存在：告知用户已有版本，询问是否覆盖（`--force`）

**步骤 3：确认并执行**

用户确认后执行实际转换：

```bash
python3 scripts/run_cli.py feishu-doc-convert --url "<URL>"
```

**步骤 4：处理结果**

- 成功：展示生成的文件路径和简要内容摘要
- 失败：根据错误类型给出建议

### 批量导入场景

用户说「帮我导入上周的所有飞书文档」时：

1. 查看 `config/feishu_ingest.json` 中预配置的文档列表
2. 逐个 `--dry-run` 预览
3. 呈现汇总表格，让用户勾选
4. 批量执行

### 路由目标说明

文档会根据内容关键词自动路由到 SOURCE 子目录：

| 路由目标 | 判定条件 |
|---------|---------|
| `05-会议纪要/` | 多人（≥3）正式会议 |
| `04-讨论思考/` | 1对1/双人讨论 |
| `03-方案报告/` | 正式方案或技术结论 |
| `08-参考资料/` | 外部学习资料 |

路由规则定义在 `config/meeting_routes.json` 中。

## 管道 2：聊天记录提炼

从飞书群聊或单聊中提炼结构化知识。

### 基本用法

```bash
# 按群聊名称 + 时间范围
python3 scripts/run_cli.py chat-digest --group "<群聊名>" --range 7

# 按用户单聊
python3 scripts/run_cli.py chat-digest --user "<用户名>" --range 3

# 交互模式（列出可用群聊供选择）
python3 scripts/run_cli.py chat-digest --interactive

# 从配置文件
python3 scripts/run_cli.py chat-digest --from-config
```

### Claude 的工作流程

**场景 A：用户指定了群聊名**

1. 解析时间范围（「最近一周」→ `--range 7`，「6月20日到现在」→ 计算天数）
2. 先用 `--dry-run` 预览
3. 确认后执行提炼
4. 在对话中展示提炼结果的关键内容（主题、决策、待办）

**场景 B：用户不确定群聊名**

1. 运行 `--interactive` 模式，捕获输出中的群聊列表
2. 以表格形式呈现给用户选择
3. 确认时间范围后执行

**场景 C：批量提炼**

1. 查看 `config/feishu_ingest.json` 中的 `chat_digest` 配置
2. 逐个群聊展示预览
3. 批量或逐个执行

### 时间范围解析

Claude 自动将自然语言时间转换为 `--range` 参数：
- 「今天」→ `1`
- 「最近一周」→ `7`
- 「上周」→ 计算上周一到周日的天数
- 「6月20日到现在」→ 计算日期差
- 「6月1日到6月30日」→ 使用 `2026-06-01~2026-06-30` 格式

## 常见错误处理

| 错误 | 处理方式 |
|------|----------|
| 飞书 API 限流 | 等待 3 秒后自动重试，最多 3 次 |
| 文档权限不足 | 告知用户该文档需要特定权限，建议在飞书中打开检查 |
| URL 格式无效 | 提示用户提供正确的飞书文档 URL 格式 |
| 群聊未找到 | 列出相似的群聊名供用户选择 |
| 排重命中 | 展示已有文件信息，询问是否 `--force` 覆盖 |
| 子进程超时 | 检查网络连接，建议单篇逐个导入 |

## 关键路径

- 排重索引：`data/dedup/feishu_doc_index.json` 和 `chat_digest_index.json`
- 图片存储：`Pic/{stem}/feishu_xxx.png`
- 配置：`config/feishu_ingest.json`
