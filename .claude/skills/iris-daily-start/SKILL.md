---
name: iris-daily-start
version: 1.0.0
description: Iris 每日启动维护 — 记忆同步、扫描切块、向量索引、Wiki 自动更新、知识图谱刷新、LLM 用量概要。当用户需要执行日常维护、启动知识库更新、查看 LLM 用量时使用。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 scripts/run_cli.py --call-source skill daily-start --help"
---

# Iris 每日启动维护

一键执行 Iris 知识库的完整日常维护管道，涵盖记忆、索引、Wiki、图谱、用量五大模块。

## 执行命令

```bash
python3 scripts/run_cli.py --call-source skill daily-start
```

如需 JSON 格式输出（便于解析）：

```bash
python3 scripts/run_cli.py --call-source skill daily-start --pretty
```

## 管道流程（5 步）

```
1. 记忆同步与维护
   ├── 长期记忆同步（扫描 memory/ 目录）
   └── 自治维护（老化检测 / 冲突检测 / 合并）

2. 扫描 + 切块 + 向量索引
   ├── MarkdownScanner 扫描所有启用数据源
   ├── MarkdownChunker 增量切块（复用未变化的文档）
   └── 向量索引增量更新（仅当 embedding 启用时）

3. Wiki 自动维护
   ├── 自动发现候选主题（基于新增/变更文档数）
   ├── WikiGenerator.update_all_pages() 增量更新已有页面
   ├── PersonEnricher 飞书通讯录补充人物信息
   └── 知识图谱增量刷新（节点 + wikilink 边，零 LLM 成本）

4. Wiki 导航索引
   ├── WikiNavigationBuilder 重建 index.md
   └── 追加 changelog 条目

5. LLM 用量概要
   ├── 今日 / 本周 / 本月调用次数与 token 汇总
   └── 预算预警（超过 monthly_token_limit 时告警）
```

## 使用场景

| 场景 | 说明 |
|------|------|
| **每日启动** | 每天开始工作时跑一次，确保知识库最新 |
| **知识库刷新** | 新增了一批数据源文档后，手动触发全量刷新 |
| **用量检查** | 查看今日/本周/本月的 LLM 调用量和 token 消耗 |
| **前置依赖** | 运行 `iris-wiki` 前如果缺 chunk 索引，先跑此命令 |

## 与 iris-wiki 的关系

`daily-start` 是 `iris-wiki` 的**前置依赖**和**互补工具**：

- `daily-start`：全自动管道，更新已有 Wiki、刷新索引和图谱，不生成新页面
- `iris-wiki`：交互式策展，发现候选 → 人工审核 → 生成新 Wiki 页面

**典型工作流**：
1. 新增数据源文档 → 先跑 `daily-start` 刷新索引
2. 再跑 `iris-wiki` 的 `wiki-pipeline` 发现新候选并生成页面

## 输出解读

执行完成后，关注以下关键字段：

- `scan[N].document_count` — 各数据源扫描到的文档数
- `chunks[N].rebuilt_documents` — 有变更需重新切块的文档数（>0 意味着有新内容）
- `wiki_discover.candidates` — 自动发现的 Wiki 候选数（有候选时建议后续跑 iris-wiki）
- `wiki_update.status` — Wiki 增量更新是否成功
- `usage_summary.budget_warning` — 如果出现，说明本月 token 已超预算

## 关键路径

- 项目根目录：`scripts/run_cli.py` 所在目录
- 数据源目录：由 `.env` 中 `${IRIS_WORK_DOCS_DIR}` 配置
- Wiki 输出目录：由 `.env` 中 `${IRIS_WIKI_ROOT}` 配置
- 用量数据库：`data/usage.db`

## 常见错误处理

| 错误 | 处理方式 |
|------|----------|
| 找不到 .env 配置 | 确认当前在 iris3 项目根目录，检查 `.env` 文件存在 |
| 扫描无结果 | 检查 `${IRIS_WORK_DOCS_DIR}` 路径是否正确，数据源目录是否有文件 |
| embedding 跳过 | 正常行为 — 如未配置 embedding 提供商会自动跳过向量索引 |
| Wiki update 跳过 | 无 chunk 数据或 wiki_root 未配置时会跳过，不影响其他步骤 |
| 人物丰富失败 | 静默失败，不影响主流程 — 可能是飞书 token 过期或网络问题 |
| 用量 DB 为空 | 尚未发生任何 LLM 调用，显示 `{"status": "empty"}` |
