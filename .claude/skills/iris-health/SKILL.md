---
name: iris-health
version: 1.0.0
description: Iris 知识库健康检查 — Wiki 深度评估、lint 质量检查、人物信息丰富。当用户需要检查知识库质量、评估 Wiki 准确性、补充人物信息时使用。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 scripts/run_cli.py wiki-lint --help"
---

# Iris 知识库健康检查

定期巡检知识库质量，包含三个维度：结构健康（lint）、内容质量（deep-eval）、人物完整度（enrich-persons）。

## 检查维度

### 1. 结构健康 (`wiki-lint`)

```bash
python3 scripts/run_cli.py wiki-lint
```

检查 6 个维度：

| 维度 | 检查内容 | 自动修复? |
|------|---------|:---:|
| frontmatter | YAML 格式完整性、必填字段 | ✅ `--fix` |
| 摘要 | 是否有 `## 摘要` 段落 | ❌ |
| 出链 | Wiki 链接有效性 | ❌ |
| draft 状态 | 草稿页面是否过期 | ✅ `--fix` |
| 过期 | 页面超过 90 天未更新 | ❌ |
| 断链 | `[[...]]` 链接指向不存在的页面 | ❌ |

自动修复：

```bash
python3 scripts/run_cli.py wiki-lint --fix
```

### 2. 内容质量 (`deep-eval`)

```bash
# 评估所有页面（随机抽样）
python3 scripts/run_cli.py deep-eval --sample-rate 0.3

# 评估特定页面
python3 scripts/run_cli.py deep-eval --page-filter "<页面标题>"
```

两个评估维度：
- **准确性**：每个引用描述是否与源文档一致（consistent / inconsistent / unverifiable / source_missing）
- **全面性**：是否有未引用但相关的源文件（覆盖度缺失）

### 3. 人物完整度 (`enrich-persons`)

```bash
# 预览
python3 scripts/run_cli.py enrich-persons --dry-run

# 执行更新
python3 scripts/run_cli.py enrich-persons
```

通过飞书通讯录自动补充人物 Wiki 页面的：
- 部门信息
- 邮箱地址

## Claude 的工作流程

### 定期巡检（推荐每月一次）

**步骤 1：运行 lint**

```bash
python3 scripts/run_cli.py wiki-lint
```

**步骤 2：解读结果**

以表格形式呈现问题：

| 页面 | 问题 | 严重度 |
|------|------|:---:|
| 领域-产品规划 | frontmatter 缺少 `updated` | ⚠️ 中 |
| 概念-微服务 | 断链：`[[未知页面]]` | 🔴 高 |
| 项目-旧项目 | 超过 90 天未更新 | ⚠️ 中 |

**步骤 3：建议修复**

- 可自动修复的 → 询问是否执行 `--fix`
- 需要人工判断的 → 逐一说明原因和建议

**步骤 4：深度评估（可选，更耗时）**

```bash
python3 scripts/run_cli.py deep-eval --sample-rate 0.2
```

解读评估结果：
- 🔴 Inconsistent：引用描述与源文档不一致 → 需要修正页面内容
- 🟡 Unverifiable：无法验证 → 源文档可能已被修改
- 🟢 Consistent：引用准确
- ⚪ Source Missing：引用来源丢失

**步骤 5：人物丰富（可选）**

```bash
python3 scripts/run_cli.py enrich-persons --dry-run
```

先预览，展示哪些人物页面将更新，确认后执行。

### 针对性检查

用户说「检查一下 XX 页面」时：

1. 运行 `wiki-lint` 查看结构问题
2. 如果是具体页面，运行 `deep-eval --page-filter "XX"`
3. 汇总展示所有发现

## 常见场景

| 用户说 | Claude 操作 |
|--------|-------------|
| 「知识库最近怎么样？」 | `iris wiki-lint` + `iris status` 展示概览 |
| 「帮我修复 Wiki 问题」 | `iris wiki-lint --fix` 自动修复 + 报告手动项 |
| 「XX 页面写得对吗？」 | `iris deep-eval --page-filter "XX"` |
| 「更新人物信息」 | `iris enrich-persons --dry-run` → 确认 → 执行 |
| 「做一次全面体检」 | lint → deep-eval 20% 抽样 → enrich-persons 预览 |

## 常见错误处理

| 错误 | 处理方式 |
|------|----------|
| 飞书 API 不可用 | `enrich-persons` 会静默失败，不影响其他检查 |
| 断链误报 | `wiki-lint` 有 100+ 技术术语白名单，仍有误报可忽略 |
| deep-eval 耗时过长 | 减小 `--sample-rate`，或针对单个页面评估 |
| frontmatter 损坏 | `wiki-lint --fix` 可自动修复大部分问题 |
