---
name: iris-feed
version: 1.0.0
description: Iris 信息汇聚 — 从飞书群聊/单聊自动挖掘有价值话题，提取关联的飞书文档，配合 OKR 语义匹配产出结构化简报并归档。当用户需要获取群聊讨论简报、关注话题进展、自动汇聚飞书信息时使用。
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "python3 scripts/run_cli.py --call-source skill feed-list"
---

# Iris 信息汇聚

从飞书群聊/单聊中自动挖掘有价值的讨论话题，通过 LLM 做话题聚合和 OKR 语义匹配，自动提取消息中引用的飞书文档（docx/wiki/sheet/base），生成结构化简报归档到 `SOURCE/09-工作简报/YYYYMM/`（按月归档）。

涉及 9 个 CLI 命令，核心工作流分为**配置→汇聚→管理**三个阶段。

---

## 阶段一：首次配置

首次使用前需要配置关注的群聊。两种方式：

### 方式 A：交互式向导（推荐首次使用）

```bash
python3 scripts/run_cli.py --call-source skill feed-setup
```

三步走：
1. 拉取用户可用群聊列表，让用户选择
2. 配置每个群的导入模式（`auto_import` 自动入库 / `confirm` 手动确认）
3. 关联 OKR 标签（可选，如 `O1-KR1`）

### 方式 B：命令行逐个添加（知道自己要加什么群）

```bash
# 添加群聊（搜索匹配第一个结果）
python3 scripts/run_cli.py --call-source skill feed-add --chat "<群聊名>"

# 添加并指定 OKR 标签
python3 scripts/run_cli.py --call-source skill feed-add --chat "<群聊名>" --tags O1-KR1

# 添加单聊
python3 scripts/run_cli.py --call-source skill feed-add --chat "<联系人名>" --chat-type single
```

### 验证配置

```bash
python3 scripts/run_cli.py --call-source skill feed-list
```

会展示每个群聊的导入模式、OKR 标签及从 `SOURCE/01-目标管理/YYYY/`（按年归档）解析出的实际 KR 描述。

---

## 阶段二：信息汇聚（日常工作）

### 预览（推荐先 dry-run）

```bash
# 默认时间范围（最近3天）
python3 scripts/run_cli.py --call-source skill feed-collect --dry-run

# 指定时间范围
python3 scripts/run_cli.py --call-source skill feed-collect --since 2026-07-20 --dry-run

# 限定某个群聊
python3 scripts/run_cli.py --call-source skill feed-collect --chat "<群名>" --dry-run
```

预览输出包含：
- 📊 获取/过滤/检测的统计数据
- 📄 发现的可提取飞书文档链接
- 📋 每个话题的标题、摘要、来源、**OKR 语义匹配结果**
- 不会实际写入磁盘，也不会实际转换文档

### 确认后执行

去掉 `--dry-run` 即可实际生成简报：

```bash
python3 scripts/run_cli.py --call-source skill feed-collect --since 2026-07-20
```

如需跳过文档提取（仅生成简报）：

```bash
python3 scripts/run_cli.py --call-source skill feed-collect --no-extract-docs
```

### 结果

- `auto_import` 的群聊话题 → 自动入库到 `SOURCE/09-工作简报/YYYYMM/`
- `confirm` 的群聊话题 → 暂存待确认队列
- 消息中的飞书文档链接 → 自动转换为本地 Markdown 并智能路由到 SOURCE 对应子目录，简报中关联引用

---

## 阶段三：待确认管理与配置维护

### 查看待确认

```bash
python3 scripts/run_cli.py --call-source skill feed-pending
```

### 确认/忽略

```bash
# 确认单个
python3 scripts/run_cli.py --call-source skill feed-confirm feed-20260727-001

# 确认全部
python3 scripts/run_cli.py --call-source skill feed-confirm --all

# 忽略
python3 scripts/run_cli.py --call-source skill feed-ignore feed-20260727-001
```

### 修改配置

```bash
# 查看完整配置
python3 scripts/run_cli.py --call-source skill feed-config --show

# 修改某个群的导入模式
python3 scripts/run_cli.py --call-source skill feed-config --chat "<群名>" --import-mode confirm

# 修改 OKR 标签
python3 scripts/run_cli.py --call-source skill feed-config --chat "<群名>" --tags O2-KR3

# 移除关注
python3 scripts/run_cli.py --call-source skill feed-remove --chat "<群名>"
```

---

## Claude 的工作流程

### 场景 A：用户说「帮我关注 XX 群」

1. **搜索群聊**：`feed-add --chat "<群名>"`
2. **展示结果**：告知已添加，展示群名和默认模式（auto_import）
3. **询问是否需要**：
   - 改为 `confirm` 模式？
   - 关联 OKR 标签？（从 `SOURCE/01-目标管理/` 中解析，展示可选 O/KR 列表）
4. **验证**：`feed-list`

### 场景 B：用户说「看看最近有什么讨论」

1. **先预览**：`feed-collect --dry-run --since <自动推算的时间范围>`
2. **展示话题概览**：按 OKR 匹配维度排序展示，**标注每个话题关联的飞书文档链接**
3. **询问是否入库或调整**：
   - 用户觉得有价值 → 执行实际 collect
   - 用户想跳过文档提取 → 加 `--no-extract-docs`
   - 用户想扩大时间范围 → 重新 dry-run
   - 用户想改某个群的模式 → feed-config

**展示格式**（dry-run 输出示例）：

```
📊 最近 3 天发现 4 个话题：

1. [O2-KR1] 智能巡检模型准召优化方案讨论
   来源: 部门交流群 · N条消息
   📄 关联文档: 智能巡检模型优化方案 (feishu docx)
   OKR: ✅ strong

2. [O1-KR1] 影像3.0主观项检测效果复盘
   来源: 算法讨论群 · N条消息
   （无关联文档）

📄 发现 1 个飞书文档链接，执行时将自动转换。
```

### 场景 C：用户说「提取近 N 天简报」

1. 解析时间范围：
   - 「最近一周」→ `--since 7天前`
   - 「上周」→ 自动算上周一到周日
   - 「最近3天」→ `--since 3天前`
2. **先 dry-run 预览**
3. 用户确认后执行
4. **展示生成结果**：每个话题的标题 + OKR 匹配 + 来源群聊

### 场景 D：用户说「有哪些待我确认的」

1. `feed-pending` 查看待确认列表
2. 逐一展示每个待确认话题的标题、摘要、来源、OKR 匹配
3. 询问每个是否确认或忽略
4. 执行 `feed-confirm` 或 `feed-ignore`

### 时间范围自动解析

| 用户说 | 计算方式 |
|--------|---------|
| 「最近3天」 | `today - 3d` |
| 「最近一周」 | `today - 7d` |
| 「上周」 | 上周一 ~ 上周日 |
| 「6月20日到现在」 | `2026-06-20 ~ today` |
| 「6月1日到6月30日」 | `2026-06-01 ~ 2026-06-30` |
| 未指定 | 默认 3 天（由 `topic_config.default_range_days` 控制） |

### OKR 标签推荐

用户给群聊配 OKR 标签时，Claude 可以先加载 OKR 文档，展示可选目标：

```
当前 OKR 目标：
  O1: 智能质检技术升级
    → O1-KR1: 【质量】影像3.0主观项检测
    → O1-KR2: 【功能】X光拆修检测
    → O1-KR3: 【验真】二手包袋图片真伪检测
  O2: 质检流程智能化
    → O2-KR1: 【分析】视频行为分析能力建设
    → O2-KR2: 【校验】在线校验与信息检测
    → O2-KR3: 【流程】作业流程能力建设
  请输入该群对应的标签（如 O1-KR1，多标签逗号分隔）:
```

---

## OKR 语义匹配说明

IRIS Feed 会从 `SOURCE/01-目标管理/YYYY/`（按年归档）中加载最新 OKR 文档，在话题检测时将实际 KR 描述注入 LLM Prompt。

- LLM 基于**语义理解**判断话题与哪个 KR 相关（非关键词硬匹配）
- 输出匹配强度：`strong` / `weak` / `none`
- 简报中包含匹配的 KR 原文和匹配强度
- `feed-list` 展示标签时自动解析为实际描述

---

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| 没配任何群 | 引导用户先跑 `feed-setup` |
| 没找到群聊 | 提示用户提供更精确的群名，或走 feed-setup 交互模式 |
| 没有获取到消息 | 扩大时间范围再试 |
| 没有检测到话题 | 消息量太少不够成话题，或都是闲聊噪音 |
| `--since` 日期格式错误 | 提示用 `YYYY-MM-DD` 格式 |
| 话题不合适（误报） | 用 `feed-ignore` 忽略，重新 collect |
| 飞书 API 限流 | 自动重试，日志告警 |
| OKR 文档缺失 | 降级处理，话题检测和简报不依赖 OKR |
| 文档转换失败（权限不足） | 跳过该文档，warn 日志，其他文档和简报不受影响；提示用户手动 `feishu-doc-convert` |
| 文档转换失败（内容为空） | 同上，跳过 |
| 文档已被转换过 | 自动排重跳过，复用已有本地文件，简报关联已有路径 |