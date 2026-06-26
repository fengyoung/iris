# Iris 3.1 — 项目执行说明

> 工作知识助手，从 Iris v2.7.1 重构升级而来。
> 个人知识库（Obsidian Wiki）重新设计 + 新增飞书团队知识库操作能力。

---

## 项目概览

### 三个实施步骤

| 步骤 | 内容 | 状态 |
|------|------|------|
| **步骤 1** | 最小化迁移：非知识库能力迁移（搭骨架） | ✅ 完成 |
| **步骤 2** | 本地知识库重构（新 Wiki 体系 + 能力重构） | ✅ 完成 |
| **步骤 3** | 飞书团队知识库管理（全新能力） | ⏳ 待开始 |

### 当前规模

| 维度 | 数据 |
|------|------|
| 源代码 | 10,432 行，75 个文件，16 个模块 |
| CLI 命令 | 41 个 |
| 单元测试 | 59 个 |
| 数据源 | 594 个文档，3,402 个 Chunk |
| 向量索引 | 5,810 条，25 MB |
| Wiki 页面 | 30 页（领域 6 / 概念 7 / 项目 8 / 人物 9） |
| 成员周报 | 453 份（2025-2026） |

### 关键路径

```
Obsidian 仓库：.../WORKSPACE/
                ├── WIKI-ROOT/
                │   ├── SOURCE/          ← 数据源（8 类分层）
                │   │   ├── 01-目标管理/
                │   │   ├── 02-部门管理/
                │   │   ├── 03-方案报告/
                │   │   ├── 04-讨论思考/
                │   │   ├── 05-会议纪要/
                │   │   ├── 06-我的周报/
                │   │   ├── 07-成员周报/
                │   │   ├── 08-参考资料/
                │   │   └── xiaolongxia/ → ../../xiaolongxia (软链)
                │   └── LLM-WIKI/        ← Wiki 输出（4 种页面类型）
                │       ├── 01-领域/
                │       ├── 02-概念/
                │       ├── 03-项目/
                │       ├── 04-人物/
                │       ├── index.md
                │       └── changelog.md
                ├── @转转/
                ├── LLM-Wiki/             ← v2.7.1 旧 Wiki（保留不迁）
                └── xiaolongxia/          ← v2.7.1 旧数据源
```

路径通过 `.env` 中的 `${IRIS_WORK_DOCS_DIR}` 和 `${IRIS_WIKI_ROOT}` 配置。

---

## 步骤 1：最小化迁移 ✅

### 已迁移模块

| 模块 | 文件 | 说明 |
|------|------|------|
| 配置加载 | `src/iris/config/loader.py` | .env 加载、${VAR} 解析、.example 回退 |
| 密钥链 | `src/iris/config/secrets.py` | macOS Keychain 存取 API Key |
| LLM Provider | `src/iris/llm/` | 多 Provider 注册、降级链 |
| 模型路由 | `src/iris/llm/router.py` | 路由规则引擎 |
| 日志 | `src/iris/utils/logging.py` | 结构化 JSON 日志 |
| 核心抽象 | `src/iris/core/` | Protocol、文件锁、写入守卫、存储层 |
| CLI 框架 | `src/iris/app/cli/` | argparse 分发表、41 个命令 |
| 记忆系统 | `src/iris/memory/` | 画像、校正、工作上下文、会话 |
| Trello | `src/iris/trello/` | 看板 CRUD + LLM 汇总 |
| 图文处理 | `src/iris/complex_input/` | 双阶段图文处理 |
| 周报提取 | `scripts/extract_weekly_reports.py` | 飞书邮箱周报提取 |
| 滴滴行程 | `scripts/extract_didi_travel.py` | 滴滴行程提取 |
| 入口脚本 | `scripts/run_cli.py` | CLI 入口 |

### 裁剪要点

- WikiSearcher 导入和调用已去除
- Wiki 健康度检查已从 status/diagnose 中去除
- wiki_root 相关配置校验已去除

---

## 步骤 2：本地知识库重构 ✅

### Wiki 体系设计（基于 Karpathy LLM Wiki 模式）

核心理念：**"编译器而非解释器"**——将知识提前编译为结构化的交叉链接 Markdown Wiki。

参考：https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

### 页面类型（4 种）

| 类型 | 目录 | 前缀 | 说明 |
|------|------|------|------|
| 领域 (domain) | `01-领域/` | `领域-` | 工作领域知识地图 |
| 概念 (concept) | `02-概念/` | `概念-` | 核心概念、术语、方法 |
| 项目 (project) | `03-项目/` | `项目-` | 具体项目知识沉淀 |
| 人物 (person) | `04-人物/` | `人物-` | 团队成员与组织知识（🆕 v3.1） |

### 页面模板

```markdown
---
title: 页面标题
type: domain|concept|project|person
status: review          # draft | stable | review
sync: false             # 是否允许推送到飞书
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

### Wiki 操作命令

| 命令 | 说明 |
|------|------|
| `discover-wiki` | 从 Chunk 中发现 Wiki 候选（4 种类型，分层排序） |
| `build-wiki` | 生成 Wiki 页面（单页/批量/审核模式） |
| `build-wiki-nav` | 维护 index.md 总索引 |
| `wiki-pipeline` | 发现→审核→生成全流程 |
| `wiki-lint` | 6 维健康检查（断链/孤立/草稿/过期/摘要/出链） |
| `wiki-lint --fix` | 自动修复 frontmatter、噪音链接、draft 状态 |
| `wiki-update` | 🆕 增量更新（daily-start 自动集成） |
| `build-asr-prompt` | 🆕 从 Wiki 构建 ASR 校正词汇表 |

### 人物提取（🆕 v3.1）

- **数据源**：周报文件、会议纪要参会人、文档中的负责人标记
- **协作网络**：按同场会议频次统计
- **更新机制**：person 类型专用增量更新 prompt，追踪方向变化

### 索引质量

- 断链智能过滤：技术术语白名单（100+）+ 源文档引用 + 字符级模糊匹配，排除率 83%
- `wiki-lint` 6 维指标：frontmatter / 摘要 / 出链 / draft 状态 / 过期 / 断链

---

## 步骤 3：飞书团队知识库管理 ⏳

全新能力，步骤 2 完成后启动。通过 lark-cli 操作飞书知识库 API。

- lark-cli 已就绪（v1.0.51，用户 + Bot 双身份认证）
- 同步筛选：`sync: true` → `status: stable/review` → person 默认不推送
- 通知：飞书 Bot 发送群聊卡片

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

### 环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `BAILIAN_API_KEY` | 百炼 API 密钥 |
| `IRIS_WORK_DOCS_DIR` | SOURCE 数据源路径 |
| `IRIS_WIKI_ROOT` | LLM-WIKI 输出路径 |
| `IRIS_MEETING_TRANS_DIR` | 会议转写文件默认搜索目录 |
| `LARK_APP_ID` / `LARK_APP_SECRET` | 飞书应用凭证（步骤 3） |

---

## 技术栈

- Python 3.9+
- OpenAI 兼容 LLM API（DeepSeek / 百炼 / Qwen 等）
- lark-cli（飞书接口层）
- macOS Keychain（可选密钥存储）

## 项目结构

```
iris3/
├── pyproject.toml          # 3.1.0，依赖：PyMuPDF / python-docx / numpy
├── README.md
├── CLAUDE.md               # 本文件
├── .env.example            # 环境变量模板
├── config/                 # 配置（*.json gitignored, *.example 版本控制）
├── data/                   # 运行时数据（全 gitignore）
├── src/iris/
│   ├── config/             # 步骤 1 — 配置加载
│   ├── llm/                # 步骤 1 — LLM Provider + 路由
│   ├── memory/             # 步骤 1 — 记忆系统
│   ├── core/               # 步骤 1 — 核心抽象（Protocol、锁、写保护、存储）
│   ├── app/cli/            # 步骤 1 — CLI 框架（41 命令）
│   ├── app/transcribe_meeting/ # 步骤 2 — 会议转录
│   ├── trello/             # 步骤 1 — Trello 集成
│   ├── complex_input/      # 步骤 1 — 图文处理
│   ├── utils/              # 步骤 1 — 工具
│   ├── ingest/             # 步骤 2 — 数据源扫描/切块
│   ├── retrieval/          # 步骤 2 — 混合检索
│   ├── qa/                 # 步骤 2 — 问答
│   ├── wiki/               # 步骤 2 — Wiki 体系
│   ├── analysis/           # 步骤 2 — 报告/思维导图/双周报
│   ├── output/             # 步骤 1 — 输出格式化
│   └── feishu/             # 步骤 3 — 飞书知识库（待开发）
├── scripts/                # CLI 入口 + 委托脚本
├── templates/              # Prompt / Wiki 模板
├── tests/                  # 单元测试（59 用例）
└── memory/                 # Claude 工作记忆
```
