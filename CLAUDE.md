# Iris 3.0 — 项目执行说明

> 工作知识助手，从 Iris v2.7.1 重构升级而来。
> 个人知识库（Obsidian Wiki）重新设计 + 新增飞书团队知识库操作能力。

---

## 项目概览

### 三个实施步骤

| 步骤 | 内容 | 状态 |
|------|------|------|
| **步骤 1** | 最小化迁移：非知识库能力迁移（搭骨架） | 🟢 当前进行 |
| **步骤 2** | 本地知识库重构（新 Wiki 体系 + 能力重构） | ⏳ 待开始 |
| **步骤 3** | 飞书团队知识库管理（全新能力） | ⏳ 待开始 |

### 关键路径

```
Obsidian 仓库：.../WORKSPACE/
                ├── WIKI-ROOT/
                │   ├── SOURCE/          ← 数据源（步骤 2）
                │   └── LLM-WIKI/        ← Wiki 输出（步骤 2）
                ├── @转转/
                └── xiaolongxia/         ← v2.7.1 旧数据源
```

- **SOURCE**: `/Users/maintainer/Library/Mobile Documents/iCloud~md~obsidian/Documents/WORKSPACE/WIKI-ROOT/SOURCE/`
- **LLM-WIKI**: `/Users/maintainer/Library/Mobile Documents/iCloud~md~obsidian/Documents/WORKSPACE/WIKI-ROOT/LLM-WIKI/`

---

## 步骤 1：最小化迁移

迁移以下非知识库能力，从 v2.7.1 复制代码并进行裁剪。

### 迁移清单

| 模块 | 源文件 | 说明 |
|------|--------|------|
| 配置加载 | `src/iris/config/loader.py` | .env 加载、${VAR} 解析、.example 回退 |
| 密钥链 | `src/iris/config/secrets.py` | macOS Keychain 存取 API Key |
| LLM Provider | `src/iris/llm/` | 多 Provider 注册与调用 |
| 模型路由 | `src/iris/llm/router.py` | 路由规则引擎与降级链 |
| 日志 | `src/iris/utils/logging.py` | 结构化 JSON 日志 |
| 核心抽象 | `src/iris/core/` | Protocol、文件锁、写入守卫、存储层 |
| CLI 框架 | `src/iris/app/cli/` | 命令处理器分发表、argparse |
| 记忆系统 | `src/iris/memory/` | 画像、校正、工作上下文、会话记忆 |
| Trello | `src/iris/trello/` | 看板 CRUD + LLM 汇总 |
| 图文处理 | `src/iris/complex_input/` | 双阶段图文处理 |
| 周报提取 | `scripts/extract_weekly_reports.py` | 飞书邮箱周报提取 |
| 滴滴行程 | `scripts/extract_didi_travel.py` | 滴滴行程提取 |
| 入口脚本 | `scripts/run_cli.py` | CLI 入口 |

### 裁剪要点

从 v2.7.1 迁移时，注意去掉以下知识库相关依赖：
- `WikiSearcher` 导入和调用
- Wiki 健康度检查（status/diagnose 中）
- `wiki_root` 相关配置校验
- `daily_start` 中的 Wiki 自动发现步骤

---

## 步骤 2：本地知识库重构

### Wiki 体系设计（参考 Karpathy LLM Wiki 模式）

参考：https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

核心理念：**"编译器而非解释器"**——将知识提前编译为结构化的交叉链接 Markdown Wiki。

### 页面类型（4 种）

| 类型 | 目录 | 前缀 | 说明 |
|------|------|------|------|
| 领域 (domain) | `01-领域/` | `领域-` | 工作领域知识地图 |
| 概念 (concept) | `02-概念/` | `概念-` | 核心概念、术语、方法 |
| 项目 (project) | `03-项目/` | `项目-` | 具体项目知识沉淀 |
| 人物 (person) | `04-人物/` | `人物-` | 团队成员与组织知识 |

### 命名规范

```
LLM-WIKI/
├── index.md                    # 总索引（LLM 维护）
├── changelog.md                # 变更日志（追加式）
├── 01-领域/
│   └── 领域-{名称}.md
├── 02-概念/
│   └── 概念-{名称}.md
├── 03-项目/
│   └── 项目-{名称}.md
└── 04-人物/
    └── 人物-{名称}.md
```

### 页面模板

```markdown
---
title: 页面标题
type: domain|concept|project|person
status: draft          # draft | stable | review
sync: false            # 是否允许推送到飞书
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources:
  - SOURCE/path/to/source.md
---

## 摘要
1-2 句概述。

## 正文
...

## 关联页面
[[领域-相关领域]]
[[概念-相关概念]]

## 参考来源
- SOURCE/path/to/source1.md
```

---

## 步骤 3：飞书团队知识库管理

全新能力，步骤 2 完成后启动。通过 lark-cli 操作飞书知识库 API。
同步筛选采用三层机制：`sync: true` 标记 → `status: stable/review` → person 类型默认不推送。
通知通过飞书 Bot 发送群聊卡片。

---

## 配置安全方案

四层配置体系，适合开源：

```
优先级：OS 环境变量 > .env 文件 > macOS Keychain
```

| 层 | 文件 | 版本控制 |
|----|------|---------|
| ① | `.env` | gitignored |
| ② | `config/*.json` | gitignored |
| ③ | `config/*.json.example` | 版本控制 |
| ④ | `data/` | 全 gitignore |

JSON 配置中使用 `${VAR_NAME}` 引用环境变量或 .env 中的值。

---

## 技术栈

- Python 3.9+
- lark-cli（飞书接口层）
- macOS Keychain（可选密钥存储）

## 项目结构

```
src/iris/
├── config/        # 步骤 1 — 配置加载
├── llm/           # 步骤 1 — LLM Provider + 路由
├── memory/        # 步骤 1 — 记忆系统
├── app/cli/       # 步骤 1 — CLI 框架
├── trello/        # 步骤 1 — Trello 集成
├── complex_input/ # 步骤 1 — 图文处理
├── core/          # 步骤 1 — 核心抽象
├── utils/         # 步骤 1 — 工具
├── ingest/        # 步骤 2 — 数据源扫描/切块
├── retrieval/     # 步骤 2 — 检索
├── qa/            # 步骤 2 — 问答
├── wiki/          # 步骤 2 — Wiki 体系
├── analysis/      # 步骤 2 — 报告/思维导图/双周报
└── feishu/        # 步骤 3 — 飞书知识库
```
