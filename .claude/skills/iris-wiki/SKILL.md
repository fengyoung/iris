---
name: iris-wiki
version: 1.0.0
description: Iris Wiki 知识管道 — 从数据源发现候选主题、生成 Wiki 页面、健康检查。当用户需要更新知识库、生成 Wiki 页面、发现新主题、检查 Wiki 质量时使用。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 scripts/run_cli.py --call-source skill --help"
---

# Iris Wiki 知识管道

管理 Iris 个人知识库 Wiki 页面的发现、生成和质量检查。

## 核心工作流

### 流程 1：发现 → 审核 → 生成（全流程）

这是最常见的场景：从数据源中发现可以生成 Wiki 页面的候选主题。

**步骤 1：运行发现**

```bash
python3 scripts/run_cli.py --call-source skill wiki-pipeline --limit 20
```

这会生成三个文件：
- `temp/wiki_pipeline/candidates.jsonl` — 所有候选
- `temp/wiki_pipeline/review.jsonl` — 待审核（需用户标记 selected 字段）
- `temp/wiki_pipeline/review.md` — 人类可读的审核清单

**步骤 2：呈现候选列表**

用 Read 工具读取 `temp/wiki_pipeline/review.md`，在对话中以表格形式呈现候选列表：

| # | 标题 | 类型 | 评分 | 证据数 | 已有 Wiki? |
|---|------|------|------|--------|-----------|
| 1 | XXX | concept | 12 | 5 | ❌ 新建 |
| 2 | YYY | person | 8 | 3 | ⚠️ 过期 |

并向用户解释关键信息：
- **评分**：基于路径权重和证据数量的综合得分
- **已有 Wiki**：如果已存在，会标注是否过期（优先按 source_fingerprint 判定：任一引用源文档变化即过期；无指纹的旧页面按超过 30 天未更新兜底）
- **页面类型**：domain / concept / project / person 四种

**步骤 3：用户选择**

询问用户希望生成哪些页面。用户可以用自然语言回应：
- 「全部生成」
- 「只生成 person 类型的」
- 「生成 1、3、5 号」
- 「跳过郑十相关的」

**步骤 4：标记并生成**

根据用户选择，用 Edit 工具修改 `temp/wiki_pipeline/review.jsonl`，将选中项的 `selected` 设为 `true`，然后执行：

```bash
python3 scripts/run_cli.py --call-source skill build-wiki --review-file temp/wiki_pipeline/review.jsonl --write --backup
```

生成完成后，自动运行 lint 检查：

```bash
python3 scripts/run_cli.py --call-source skill wiki-lint
```

如果 lint 发现问题，呈现给用户并询问是否需要 `--fix` 自动修复。

### 流程 2：单页生成

用户直接指定要生成的页面：

```bash
python3 scripts/run_cli.py --call-source skill build-wiki --query "<搜索查询>" --title "<页面标题>" --page-type <类型> --write
```

**参数说明**：
- `--query`：用于检索证据的搜索词
- `--title`：页面标题（不含前缀，如「产品规划」而非「领域-产品规划」）
- `--page-type`：`domain` / `concept` / `project` / `person`
- `--write`：写入磁盘，不指定则仅预览
- `--overwrite`：覆盖已有页面
- `--backup`：覆盖前备份

**Claude 的职责**：
- 帮助用户确定合适的 `--page-type`
- 建议 `--query` 和 `--title`（用户可能只有模糊想法）
- 生成后检查结果质量

### 流程 3：增量更新

批量更新所有已有 Wiki 页面：

```bash
python3 scripts/run_cli.py --call-source skill wiki-update
```

或更新单个页面：

```bash
python3 scripts/run_cli.py --call-source skill wiki-update --title "<页面标题>" --page-type <类型>
```

### 流程 4：健康检查

```bash
# 仅检查
python3 scripts/run_cli.py --call-source skill wiki-lint

# 自动修复
python3 scripts/run_cli.py --call-source skill wiki-lint --fix
```

`wiki-lint` 检查 6 个维度：frontmatter 完整性、摘要存在性、出链有效性、draft 状态、过期页面、断链。

## 页面类型指南

| 类型 | 目录 | 前缀 | 何时使用 |
|------|------|------|----------|
| `domain` | `01-领域/` | `领域-` | 工作领域知识地图 |
| `concept` | `02-概念/` | `概念-` | 核心概念、术语、方法论 |
| `project` | `03-项目/` | `项目-` | 具体项目知识沉淀 |
| `person` | `04-人物/` | `人物-` | 团队成员与组织知识 |

## 关键路径

- 项目根目录：`scripts/run_cli.py` 所在目录
- Wiki 输出目录：由 `.env` 中 `${IRIS_WIKI_ROOT}` 配置
- 数据源目录：由 `.env` 中 `${IRIS_WORK_DOCS_DIR}` 配置

## 常见错误处理

| 错误 | 处理方式 |
|------|----------|
| 找不到 chunk 索引 | 提示用户先运行 `daily-start` 或 `build-chunks` |
| LLM 调用失败 | 自动重试（最多 3 次），告知用户当前降级链状态 |
| frontmatter 损坏 | `wiki-lint --fix` 可自动修复 |
| 写入权限不足 | 检查 `write_guard` 配置中的 `allowed_paths` |
