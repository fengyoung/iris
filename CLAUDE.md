# Iris 3.10.0 — 项目执行说明

> 工作知识助手，从 Iris v2.7.1 重构升级而来。
> 个人知识库（Obsidian Wiki）重新设计 + 新增飞书团队知识库操作能力。

---

## 项目概览

### 三个实施步骤

| 步骤 | 内容 | 状态 |
|------|------|------|
| **步骤 1** | 最小化迁移：非知识库能力迁移（搭骨架） | ✅ 完成 |
| **步骤 2** | 本地知识库重构（新 Wiki 体系 + 能力重构） | ✅ 完成 |
| **步骤 3** | 飞书 → 本地知识库提炼 | ✅ 完成 |

### 当前规模

| 维度 | 数据 |
|------|------|
| 源代码 | 17,298 行，94 个文件，19 个模块 |
| CLI 命令 | 45 个 |
| 单元测试 | 169 个（12 个测试文件） |
| 数据源 | 594 个文档，3,402 个 Chunk |
| 向量索引 | 5,810 条，25 MB |
| Wiki 页面 | 91 页（领域 6 / 概念 7 / 项目 8 / 人物 70） |
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
                │   │   └── v2-data/ → ../../v2-data (软链)
                │   └── LLM-WIKI/        ← Wiki 输出（4 种页面类型）
                │       ├── 01-领域/
                │       ├── 02-概念/
                │       ├── 03-项目/
                │       ├── 04-人物/
                │       ├── index.md
                │       └── changelog.md
                ├── @项目/
                ├── LLM-Wiki/             ← v2.7.1 旧 Wiki（保留不迁）
                └── v2-data/          ← v2.7.1 旧数据源
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
| CLI 框架 | `src/iris/app/cli/` | argparse 分发表、43 个命令 |
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
| `build-asr-prompt` | 🆕 v3.5 三段 LLM Pipeline：热词提取 → 误识别生成 → 策略 Prompt（支持 `--asr-mode` / `--bump`） |

### 人物提取（🆕 v3.1）

- **数据源**：周报文件、会议纪要参会人、文档中的负责人标记
- **协作网络**：按同场会议频次统计
- **更新机制**：person 类型专用增量更新 prompt，追踪方向变化

### 索引质量

- 断链智能过滤：技术术语白名单（100+）+ 源文档引用 + 字符级模糊匹配，排除率 83%
- `wiki-lint` 6 维指标：frontmatter / 摘要 / 出链 / draft 状态 / 过期 / 断链

### 会议纪要路由（v3.2.1）

`transcribe-meeting --to-source` 现在支持 LLM 动态路由，自动判定纪要归档到 SOURCE 对应子目录：

| 路由目标 | 判定条件 | 命名格式 |
|---------|---------|---------|
| `05-会议纪要/` | 多人（≥3）正式会议，有决策/待办 | `YYYYMMDD-{type}-{topic}.md` |
| `04-讨论思考/` | 1对1/双人讨论（首要信号），即使有决策待办 | `YYYYMMDD-{type}-{topic}.md` |
| `03-方案报告/` | 产出正式方案或技术结论 | `YYYYMMDD-{topic}.md` |
| `08-参考资料/` | 外部学习资料、参会笔记 | `YYYYMMDD-{source}-{topic}.md` |

**配置驱动（开源安全）：** 路由规则存于 `config/meeting_routes.json`（gitignored），
代码零硬编码。`.example` 为脱敏占位。

**来源标识：** 纪要头部写入 `来源：原始文件名`，供后续排重和溯源。

---

## 步骤 3：飞书 → 本地知识库提炼 ✅

飞书文档/聊天记录 → 本地 Markdown → SOURCE 归档 → Wiki 自然吸收。

| 管道 | 状态 | 说明 |
|------|------|------|
| `feishu-doc-convert` | ✅ 完成 | 飞书文档转本地 Markdown（图片→Pic，补充元信息+作者，排重） |
| `chat-digest` | ✅ 完成 | 聊天记录 AI 提炼为结构化文档（主题/决策/待办/关联项目） |

**不包含**：本地 → 飞书发布（需求已取消）、团队知识查询（直接用飞书搜）。

### 使用方式

```bash
# 飞书文档转换
iris feishu-doc-convert --url <文档URL>              # 单篇文档
iris feishu-doc-convert --url <URL1> --url <URL2>     # 批量
iris feishu-doc-convert --from-config                 # 从配置文件读取
iris feishu-doc-convert --url <URL> --dry-run         # 预览路由结果

# 聊天记录提炼
iris chat-digest --group <群聊名> --range 5           # 指定群聊+天数
iris chat-digest --user <用户名> --range 3            # 指定用户单聊
iris chat-digest --interactive                        # 交互选择模式
iris chat-digest --from-config                        # 从配置文件读取
```

### 关键设计

- **路由驱动**：文档用 `meeting_routes.json` 配置规则，聊天用 AI 萃取后的决策/待办判定
- **排重索引**：`data/dedup/feishu_doc_index.json` 和 `chat_digest_index.json`
- **图片存储**：`Pic/{stem}/feishu_xxx.png`（与 Obsidian 兼容）
- **开源安全**：全部关键词和描述均从 gitignored 配置文件动态读取

详细设计见 [[feishu-to-local-pipelines]]。

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

## 版本体系（三层解耦）

| 层 | 位置 | 格式 | 含义 | 当前值 |
|----|------|------|------|--------|
| **产品版本** | `pyproject.toml` | SemVer X.Y.Z | 软件发布版本 | 3.10.0 |
| **协议版本** | `src/iris/__init__.py` | MAJOR.MINOR | CLI 命令集 / agent-spec 格式 | 3.8 |
| **数据版本** | `config/*.json` | 各自独立 | 配置文件 Schema | 3.4 |

> 三层解耦：只有真正发生变化的层才递增版本号。

---

## 技术栈

- Python 3.9+
- OpenAI 兼容 LLM API（DeepSeek / 百炼 / Qwen 等）
- lark-cli（飞书接口层）
- macOS Keychain（可选密钥存储）

## 项目结构

```
iris3/
├── pyproject.toml          # 3.10.0，依赖：PyMuPDF / python-docx / numpy / pydantic>=2.0
├── README.md
├── CLAUDE.md               # 本文件
├── .env.example            # 环境变量模板
├── config/                 # 配置（*.json gitignored, *.example 版本控制）
├── data/                   # 运行时数据（全 gitignore）
├── src/iris/
│   ├── config/             # 步骤 1 — 配置加载（🆕 v3.7: models.py Pydantic v2 类型安全）
│   ├── complex_input/       # 步骤 1 — 复杂输入（🆕 v3.8: 三阶段流水线 + 多文件类型检测）
│   ├── llm/                # 步骤 1 — LLM Provider + 路由（🆕 v3.8: LLMService 统一入口）
│   ├── memory/             # 步骤 1 — 记忆系统（🆕 v3.10: lifecycle, long_term, manager, session, working 独立子模块）
│   ├── core/               # 步骤 1 — 核心抽象（Protocol、锁、写保护、存储）
│   ├── app/cli/            # 步骤 1 — CLI 框架（45 命令）
│   ├── app/transcribe_meeting/ # 步骤 2 — 会议转录
│   ├── trello/             # 步骤 1 — Trello 集成
│   ├── utils/              # 步骤 1 — 工具（🆕 v3.6: constants, llm_parsing）
│   ├── ingest/             # 步骤 2 — 数据源扫描/切块
│   ├── retrieval/          # 步骤 2 — 混合检索
│   ├── qa/                 # 步骤 2 — 问答
│   ├── wiki/               # 步骤 2 — Wiki 体系（🆕 v3.6: asr_hotwords, asr_prompt_optimizer, asr_formatter, asr_version 拆分自 term_extractor; 🆕 v3.9: person_enricher 飞书通讯录丰富）
│   ├── analysis/           # 步骤 2 — 报告/思维导图/双周报
│   ├── evaluation/         # 🆕 v3.7 — Wiki 深度评估（准确性 + 全面性，从 iris2 迁移）
│   ├── output/             # 🆕 v3.10 — 输出格式化（formatter, converters）
│   └── feishu/             # 步骤 3 — 飞书文档/聊天提炼
│       ├── client.py       #   lark-cli 封装（文档/IM/图片/通讯录）
│       ├── _shared.py      #   共享工具（路径/排重/时间/标题清理）
│       ├── doc_convert.py  #   飞书文档→本地 Markdown + 路由归档
│       └── chat_digest.py  #   聊天记录 AI 提炼 + 结构化输出
├── scripts/                # CLI 入口 + 委托脚本
├── templates/              # Prompt / Wiki 模板（🆕 v3.6: wiki/generate_*.txt Prompt 外部化）
├── tests/                  # 单元测试（169 用例，12 个测试文件）
└── memory/                 # Claude 工作记忆
```

---

## 🆕 v3.9.0 变更（2026-06-30）

### 人物 Wiki 页面飞书通讯录自动丰富 + 人物发现规则增强

**动机**：人物 Wiki 页面创建后，需要手动填写部门、邮箱等信息，且人物发现规则覆盖不全（缺少正文中的动作提及模式）。

### 新增模块

| 模块 | 文件 | 行数 | 说明 |
|------|------|:------:|------|
| 人物丰富器 | `wiki/person_enricher.py` | 335 | 飞书通讯录批量搜索，自动补充部门/邮箱到人物 Wiki frontmatter |

### 新增 CLI 命令

| 命令 | 说明 |
|------|------|
| `enrich-persons` | 扫描所有人物 Wiki 页面，通过飞书通讯录补充部门/邮箱信息 |
| `enrich-persons --dry-run` | 预览模式，仅显示将要更新的内容，不写入 |

### 人物发现规则增强

#### 新增 8 条正则模式（`discovery_rules.py`）

| 类别 | 模式 | 示例匹配 |
|------|------|----------|
| 结构化标记 | 供稿/整理/撰写/编辑：XXX | `供稿：团队成员J` |
| 正文动作 | 由/让/委派 XXX 负责/主持/牵头/主讲/跟进 | `由团队成员J负责` |
| 正文动作 | XXX 提出/汇报了/分享了/总结了/主持了（排除集合主语） | `团队成员J分享了` |
| 正文转述 | 据 XXX 介绍/反馈/透露/汇报 | `据团队成员J介绍` |
| 参与人列表 | 参与人/参与人员/与会人/与会人员 列表 | `参与人：团队成员J、张三` |

#### 质量过滤增强

| 机制 | 说明 |
|------|------|
| `PERSON_EXCLUSIONS` | 60+ 非人名排除名单（Iris、发言人、甲方、用户、客户…） |
| Markdown 语法过滤 | 排除 `**` 等纯符号残留 |
| 字母+数字排除 | 如「发言人3」|
| 组织后缀排除 | 以团队/小组/部门/系统/平台/项目结尾的非人名 |
| 证据阈值 | person 从 2 降到 1（出现 1 次即可候选） |

### ChunkSlim 扩展（`ingest/chunker.py`）

| 字段 | 改进前 | 改进后 |
|------|:---:|------|
| `content` | ❌ 无（仅有 180 字符 `content_preview`） | ✅ 新增 `content` 字段（全文） |

### 概念/人物提取改进（`wiki/discovery.py`）

- 概念和人物提取现在使用 `chunk.content`（全文），fallback 到 `content_preview`
- 消除因 180 字符截断导致的信息丢失

### Bug 修复

| 问题 | 文件 | 修复 |
|------|------|------|
| `lint_wiki` 使用原始 `ptype` 变量而非 `page_info.page_type` | `navigation.py:314` | 改为 `page_info.page_type` |

### daily-start 集成

`daily-start` 新增第 5 步：飞书通讯录人物信息丰富

```
1. 内存同步 → 2. 内存维护 → 3. 文档扫描 → 4. Wiki 发现+更新 → 5. 人物丰富 🆕
```

- 静默失败设计：飞书 API 不可用时不影响主流程
- 有更新时自动重建导航索引

### CLI 命令数

| 维度 | 改进前 (v3.8.1) | 改进后 (v3.9.0) |
|------|:---:|:---:|
| CLI 命令 | 44 | **45**（+`enrich-persons`） |

### 关键指标

| 维度 | 改进前 (v3.8.1) | 改进后 (v3.9.0) |
|------|:---:|:---:|
| 源代码行数 | 16,694 | **17,117** |
| 源文件数 | 93 | **94** |
| 模块数 | 19 | 19 |
| CLI 命令 | 44 | **45** |
| 单元测试 | 169 | 169 |
| Wiki 人物发现模式 | 6 条 | **14 条** |
| 人物证据阈值 | 2 | **1** |
| ChunkSlim 字段 | 3 | **4**（+content） |
| Person exclusion 名单 | ❌ 无 | ✅ 60+ 词条 |
| 飞书通讯录集成 | ❌ 无 | ✅ `PersonEnricher` |

---

## 🆕 v3.10.0 变更（2026-07-01）

### 全面代码优化 + 新模块引入

基于多轮并行审查（v3.9.0 后 7 个提交，46 个文件变更，+2171/-527），完成致命修复、架构重构和性能优化。

### 致命 Bug 修复（4 项）

| # | 问题 | 修复 |
|---|------|------|
| C1 | API Key 在全系统中明文传播 | `get_active_model_config(sensitive=False)` 默认脱敏 |
| C2 | Protocol 与实际 LLM Provider 签名不一致 | 统一为 `(LLMRequest, *, kwargs) -> LLMResponse` |
| C3 | Trello DNS monkey-patch 全局状态污染 | 移除 monkey-patch，改为 URL 重写 + Host header |
| C4 | Keychain set_secret 先删后加数据丢失 | 移除前置 delete 调用 |

### High 问题修复（10 项）

| # | 问题 | 修复 |
|---|------|------|
| H1 | LLM 调用无重试机制 | `generate()` / `generate_multimodal()` 加 `max_retries` |
| H2 | generate/generate_multimodal 85% 代码重复 | `_fallback_loop()` 共享降级循环 |
| H3 | has_credentials_for_role 只查活跃模型 | 扫描角色下全部模型 |
| H4 | handlers.py 15+ 处 `__import__('sys')` | 统一为顶部 `import sys` |
| H5 | _read_wiki_page 无文件读取容错 | try/except + 日志警告 |
| H6 | 日志归档竞态条件 | `os.rename` 原子归档 + `fcntl` 锁 |
| H7 | QA token 预算用 len() 近似 | 改用 `estimate_tokens()` |
| H8 | fix_wiki 缺少 ConfigBundle 参数 | `_atomic_write` 支持可选 bundle 降级 |
| H9 | BM25 基于 180 字符 content_preview | 改用 `chunk.content` 全文计算 |
| H10 | 图片编码无大小限制 | 20MB 上限 + 跳过大图警告 |

### 架构重构（4 项）

| 项 | 内容 | 效果 |
|----|------|------|
| **1** | LLM Provider 双接口统一 | Protocol + BaseLLMProvider 签名一致，`_fallback_loop` 消除重复 |
| **2** | term_extractor.py 拆分 | 1,298 行 → 556 行，拆出 4 个 ASR 子模块（hotwords/prompt_optimizer/formatter/version） |
| **3** | WikiContextLoader 统一加载 | 5 处独立文件扫描收敛到 1 个入口 |
| **4** | Prompt 模板外部化 | 2 个 Wiki 生成模板 → `templates/wiki/generate_*.txt` |

### 新模块

| 模块 | 文件 | 行数 | 说明 |
|------|------|:----:|------|
| 记忆子模块 | `memory/lifecycle.py` | 327 | 记忆生命周期管理 |
| 记忆子模块 | `memory/long_term.py` | 252 | 长期记忆持久化 |
| 记忆子模块 | `memory/manager.py` | 104 | 记忆管理器编排 |
| 记忆子模块 | `memory/session.py` | 101 | 会话记忆 |
| 记忆子模块 | `memory/working.py` | 173 | 工作记忆上下文 |
| 输出格式化 | `output/formatter.py` | 441 | 统一输出格式化 |
| 输出格式化 | `output/converters.py` | 58 | 输出格式转换 |
| 全局常量 | `utils/constants.py` | 35 | IMAGE_EXTENSIONS, FILE_TYPE_* 等 |

### 搜索性能优化

| 优化项 | 说明 |
|--------|------|
| 分词缓存 | 热路径分词结果缓存，减少重复计算 |
| RRF 排序改进 | 混合检索排序融合优化 |
| BM25 全文基准 | 从 content_preview（180 字符）改为 `chunk.content` 全文 |
| 向量索引矩阵缓存 | 避免每次 `search()` 重建 numpy 矩阵 |
| `query_plan` 权重调整 | 高优先级 focus_areas 提升标题/段落权重 |

### Bug 修复

| 文件 | 修复 |
|------|------|
| `config/loader.py` | `resolve_env_vars` 死循环检测，文档说明单层替换 |
| `config/secrets.py` | 移除 `set_secret` 前置 delete 调用，消除数据丢失窗口 |
| `trello/client.py` | DNS monkey-patch → URL 重写 + Host header，消除全局污染 |
| `feishu/doc_convert.py` | 孤儿子进程 `Popen.kill()` + `communicate()` + `finally` |
| `retrieval/enhanced.py` | RRF 分数归一化修复 |
| `output/formatter.py` | 统一 `build-report` / `build-mindmap` 输出格式 |
| `ingest/chunker.py` | `ChunkSlim` 新增 `content` 字段（全文），消除 180 字符截断 |

### 关键指标

| 维度 | 改进前 (v3.9.0) | 改进后 (v3.10.0) |
|------|:---:|:---:|
| 源代码行数 | 17,117 | **17,298** |
| 源文件数 | 94 | **94** |
| 模块数 | 19 | **19** |
| CLI 命令 | 45 | **45** |
| 单元测试 | 169 | **169** |
| Wiki 页面数 | 30 | **91** |
| term_extractor.py | 1,298 行 | **556 行**（-57%） |
| provider.py 重复率 | ~85% | **0%**（_fallback_loop 统一） |
| API Key 暴露面 | 全模块 | **仅 provider 内部** |
| Trello 线程安全 | ❌ | ✅ |
| 记忆系统 | __init__ 单文件 | **5 子模块：lifecycle/long_term/manager/session/working** |

---

## 🆕 v3.8.1 变更（2026-06-30）

### 多模型路由自动触发改进

**问题**：用户通过 query 文本或 Claude Code 传入图片路径时，系统不会自动触发多模型路由。

**根因 3 个断点**：
1. Agent 层：`ask` 的 agent-spec 未声明 `image` 参数
2. CLI 层：`handle_ask` 只检查 `--image` 标志，不解析 query 文本
3. 检测层：`InputDetector.detect()` 的 `query` 参数未使用

### 改动清单

| 文件 | 改动 |
|------|------|
| `complex_input/detector.py` | 新增 `extract_file_paths_from_text()` 公开函数；`detect()` 在 `file_paths` 为空时自动从 query 提取路径；提取 `_resolve_files()` 静态方法消除代码重复；新增 `_merge_detected_types()` 辅助函数 |
| `app/cli/handlers.py` | `handle_ask()` 在 `--image` 为空时自动调用 `extract_file_paths_from_text(args.query)` 提取路径并路由到 ComplexInputPipeline |
| `core/agent_adapter.py` | `ask` 的 input_schema 新增 `"image": {"type": "string"}`，Claude Code 作为 Agent 时知道可以传 `--image` |
| `complex_input/__init__.py` | 导出 `extract_file_paths_from_text` |

### 三层自动触发

| 层 | 触发条件 | 效果 |
|:--:|----------|------|
| Agent | Claude Code 通过 agent-spec 知道 `ask` 支持 `--image` | 自动传 `--image` 标志 |
| CLI | `ask` 命令自动从 query 文本提取文件路径 | 路由到三阶段流水线 |
| Detector | 任何调用 `detect(query)` 时自动检查 query 中的路径 | 兜底触发多模态 |

### 测试

```
161 → 169 测试 (+8)
新增: test_utils.py::TestExtractFilePaths (8 个)
```

### 关键指标

| 维度 | 改进前 (v3.8.0) | 改进后 (v3.8.1) |
|------|:---:|:---:|
| 源代码行数 | 16,620 | **16,694** |
| 源文件数 | 93 | 93 |
| 单元测试 | 161 | **169** |
| query 自动路径提取 | ❌ 无 | ✅ `extract_file_paths_from_text()` |
| ask 命令图片支持 | ❌ agent-spec 缺 `image` | ✅ agent-spec 完整声明 |
| detector query 使用 | ❌ 标注"未使用" | ✅ 自动提取路径 |

---

## 🆕 v3.4 变更（2026-06-27）

### 新增模块
| 模块 | 文件 | 说明 |
|------|------|------|
| Wiki 常量 | `src/iris/wiki/_constants.py` | 页面类型单一数据源，消除 4 文件映射重复 |
| Wiki 上下文加载 | `src/iris/wiki/context_loader.py` | 统一 Wiki 页面加载，替换 4 处独立实现 |
| 输入校验 | `src/iris/utils/validation.py` | 安全类型转换、JSON 解析、必填字段校验 |

### Critical Bug 修复（6 项）
- XMind 导出：`zipfile.ZipIO()` → `io.BytesIO()`（原代码完全无法运行）
- BM25 检索算法重写：正确的 IDF 公式 + 语料库级统计量
- 向量索引矩阵缓存：避免每次 `search()` 重建 numpy 矩阵
- `query_plan` 参数接入：之前接收后从未使用
- Wiki 覆盖度统计字段修正：`document_path` → `relative_path`
- `_build_person_prompt` return 后死代码删除

### 第二轮审查修复（2 Critical + 3 High）
- `ModelManagerError` 未导入 → 异常路径 `NameError` 崩溃
- Trello DNS 全局 monkey-patch 线程安全：Lock + TTL
- 飞书客户端孤儿子进程清理：`subprocess.Popen` + `finally kill`
- frontmatter 损坏永久卡死修复 + `\r\n` 换行符兼容

### 检索质量改进
- BM25 评分：IDF 从 `log(doc_len/tf)` 修正为 `log((N-df)/df)`
- 向量索引：矩阵缓存避免每次搜索 O(n) 重建
- `query_plan` 权重调整：高优先级 focus_areas 提升标题/段落权重

### 健壮性增强
- `write_guard` 统一：用户自定义路径与内部关键目录合并处理
- `model_manager` 浅拷贝：防止就地修改共享配置字典
- 排重过滤器安全修复：`source_url` 为空时不再误删条目
- 飞书客户端进程生命周期管理：`Popen.kill()` + `communicate()`
- FTS5 初始化失败记录 warning 日志
- PDF 标题提取 `fitz` 缺失回退保护
- JSONL 输入解析错误提示（含行号）
- `\r\n` 换行符统一处理（frontmatter / YAML 解析兼容 Windows）
- 日志 10MB 自动归档 + set/frozenset 序列化

### 性能优化
- `update_all_pages` O(n²) → O(n)：预建 title→path 索引
- PDF 提取 `import re` 模块级迁移（热路径优化）
- Whisper MPS 加速检测（Apple Silicon）
- 提示词模板内存缓存
- DNS 缓存 TTL 机制（Trello）

### Wiki 常量统一
- 页面类型配置（目录/前缀/显示名）从 4 处硬编码收敛到 `_constants.py` 单一数据源
- `discovery.py` / `generator.py` / `navigation.py` / `searcher.py` 统一引用

---

## 🆕 v3.5.0 变更（2026-06-29）

### build-asr-prompt 三段 LLM Pipeline 重写

`build-asr-prompt` 从单阶段术语提取升级为**三段式 LLM Pipeline**，产出三种语音转写优化资源：

| 阶段 | 功能 | LLM 调用 | 产出 |
|:----:|------|:--------:|------|
| Phase 1 | **LLM 热词提取**（深度理解 30 个 Wiki 页面） | 5 批 | `asr-hotwords-{date}-{time}.txt` |
| Phase 2 | **LLM 误识别生成**（拼音混淆 + 中英混排模式） | 8 批 | `asr-replace-dict-{date}-{time}.json` |
| Phase 3 | **LLM Prompt 优化**（策略指引，非术语列表） | 1 次 | `asr-prompt-v{ver}-{date}-{time}.md` |

#### 产品定位

| 产出 | 职责 | 限制 | 格式 |
|------|------|:----:|------|
| **热词表** | ASR boosting 热词增强 | ≤490 条，≤20 字符或 ≤10 中文字 | txt，每行一个 |
| **替换词典** | 确定性词→词映射 | ≤990 条，两端均 ≤20 字符 | JSON `{"replace_map": {}}` |
| **校正 Prompt** | LLM 语境消歧 + 流畅润色 + 输出规范 | ≤1200 汉字 | Markdown |

> **设计原则**：替换词典负责确定性映射，Prompt 负责策略指引——**不互相重复**。

#### CLI 参数

```bash
# 全流程（默认）
iris build-asr-prompt --asr-mode all --bump minor

# 单独生成
iris build-asr-prompt --asr-mode hotwords   # 仅热词
iris build-asr-prompt --asr-mode replace-dict  # 仅替换词典
iris build-asr-prompt --asr-mode prompt     # 仅校正 Prompt

# 控制参数
iris build-asr-prompt --asr-mode all \
  --max-hotwords 490 --max-mappings 990 --max-chars 20
```

#### Phase 1：LLM 热词提取

- **领域上下文注入**：告诉 LLM 这是ExampleOrg技术研发部场景（二手商品质检 AI、搜索推荐、大模型）
- **全量正文分析**：不再截断 600 字符，传完整章节/粗体/Wiki 链接结构
- **强质量过滤**：自动过滤括号不完整、超长、句子片段等噪音
- **LLMHotwordExtractor** 类负责分批次调用和去重合并

#### Phase 2：LLM 误识别生成

- **拼音混淆模式完整版**：zh↔z, ch↔c, sh↔s, n↔l, r↔l, h↔f, an↔ang, en↔eng, in↔ing, **ian↔ie**, eng↔ong
- **中英混排专项指导**：数字读法混淆（3.0→三点零/三零/30）、字母音译、大小写变体
- **Phase 1 热词反馈**：`hotwords_to_terms()` 将热词补充进术语列表，统一生成误识别

#### Phase 3：LLM Prompt 优化

- **策略型 Prompt**（非术语列表）：包含校正策略、润色规则、输出格式三个章节
- **与替换词典互补**：不逐条复述术语映射，聚焦语境消歧和流畅度

#### 改进统计

| 维度 | 改进前 (v3.4.1) | 改进后 (v3.5.0) |
|------|:---:|:---:|
| term_extractor.py | 756 行 | **1,300 行** |
| 热词提取 | ❌ 无（外挂脚本） | ✅ Phase 1 原生集成 |
| 替换词典 | ❌ 无（外挂脚本） | ✅ Phase 2 原生集成 |
| Prompt 风格 | 术语全量抄表 | ✅ 策略指引型 |
| 领域上下文 | ❌ 无 | ✅ 转转/二手商品/质检 AI |
| 拼音模式 | 4 种 | 12 种（含 ian→ie 等） |
| Phase 1→2 数据融合 | ❌ 无 | ✅ hotwords_to_terms 桥接 |
| 质量过滤 | ❌ 无 | ✅ 括号完整性 + 长度 + 中文字数 |
| 单元测试 | 38 个 | 38 个（全部通过） |


## 🆕 v3.6.0 变更（2026-06-29）

### 全面代码审查 + 架构升级

基于 5 个并行 agent 的全模块深度审查（83 个文件，14,000+ 行），完成 29 项修复 + 5 项架构重构。

### Critical Bug 修复（6 项）

| # | 问题 | 文件 | 修复 |
|---|------|------|------|
| C1 | API Key 在全系统中明文传播 | `model_manager.py` | `get_active_model_config(sensitive=False)` 默认脱敏 |
| C2 | Protocol 与实际 LLM Provider 签名不一致 | `protocols.py`, `provider.py` | 统一为 `(LLMRequest, *, kwargs) -> LLMResponse` |
| C3 | Trello DNS monkey-patch 全局状态污染 | `trello/client.py` | 移除 monkey-patch，改为 URL 重写 + Host header |
| C4 | term_extractor.py:787 重复添加热词 | `term_extractor.py` | 删除 1 行 |
| C5 | BM25 基于 180 字符 content_preview | `retrieval/searcher.py` | 改用 `chunk.content` 全文计算 |
| C6 | Keychain set_secret 先删后加数据丢失 | `config/secrets.py` | 移除前置 delete 调用 |

### High 问题修复（14 项）

| # | 问题 | 修复 |
|---|------|------|
| H1 | 所有 LLM 调用无重试机制 | `generate()` / `generate_multimodal()` 加 `max_retries` 参数 |
| H3 | generate/generate_multimodal 85% 重复 | 提取 `_fallback_loop()` 共享降级循环 |
| H3 | has_credentials_for_role 只查活跃模型 | 扫描角色下全部模型 |
| H5 | handlers.py 15+ 处 `__import__('sys')` | 统一为顶部 `import sys` |
| H10 | _read_wiki_page 无文件读取容错 | try/except + 日志警告 |
| M1 | default_strategy 死配置字段 | 实现 `allow_auto_upgrade/downgrade` 策略开关 |
| M2 | resolve_env_vars 死循环检测 | 清理 seen 逻辑 + 文档说明单层替换 |
| M5 | 日志归档竞态条件 | `os.rename` 原子归档 + `fcntl` 锁 |
| M6 | QA token 预算用 len() 近似 | 改用 `estimate_tokens()` |
| M12 | Stage1 失败消息注入 Stage2 输出 | 提前拦截 `[Stage1 失败]` 前缀 |
| M14 | fix_wiki 非原子写入 | `_atomic_write()` + `os.replace()` |
| M15 | 图片编码无大小限制 | 20MB 上限 + 跳过大图警告 |
| — | 方法体内 `import re` × 4 处 | lifecycle/secrets/service 迁移到顶部 |

### 架构重构（5 项）

| 项 | 内容 | 效果 |
|----|------|------|
| **C2** | LLM Provider 双接口统一 | Protocol + BaseLLMProvider + Null/Fake 全部签名一致 |
| **H2** | term_extractor.py 拆分 | 1,298 行 → 556 行，拆出 4 个模块 |
| **H4** | WikiContextLoader 统一加载 | 5 处独立文件扫描收敛到 1 个入口 |
| **H7** | Prompt 模板外部化 | 2 个 Wiki 模板 → `templates/wiki/*.txt` |
| **C3** | Trello DNS → URL 重写 | 消除全局 socket.getaddrinfo monkey-patch |

### 新文件

```
src/iris/utils/constants.py          ← 全局常量（IMAGE_EXTENSIONS, MIME_MAP）
src/iris/utils/llm_parsing.py        ← LLM 响应解析（strip_code_fence, try_parse_json）
src/iris/wiki/asr_hotwords.py        ← LLM 热词提取（Phase 2）
src/iris/wiki/asr_prompt_optimizer.py ← LLM Prompt 优化器（Phase 3）
src/iris/wiki/asr_formatter.py       ← ASR 输出格式化
src/iris/wiki/asr_version.py         ← 版本管理
templates/wiki/generate_generic.txt  ← 通用 Wiki Prompt 模板
templates/wiki/generate_person.txt   ← 人物 Wiki Prompt 模板
```

### 测试

```
97 → 138 测试 (+41)
新增: test_llm_security.py, test_provider_fallback.py,
      test_retrieval_scoring.py, test_utils.py
```

### 关键指标

| 维度 | 改进前 (v3.5.0) | 改进后 (v3.6.0) |
|------|:---:|:---:|
| 源代码行数 | 14,358 | 14,688 |
| 源文件数 | 83 | 89 |
| term_extractor.py | 1,298 行 | 556 行（-57%） |
| Wiki 总行数 | 3,461 | ~3,480 |
| provider.py 重复率 | ~85% | 0%（_fallback_loop 统一） |
| 单元测试 | 97 | **138** |
| API Key 暴露面 | 全模块 | 仅 provider 内部 |
| BM25 统计基准 | ~180 字符 | 全文 |
| Trello 线程安全 | ❌ | ✅ |


## 🆕 v3.7.0 变更（2026-06-29）

### iris2 → iris3 能力迁移

基于 iris2 对比分析，将 iris2 的两项独有能力迁移到 iris3。

### 1. Pydantic v2 配置校验（`config/models.py`, 270 行）

| 特性 | 说明 |
|------|------|
| 模型类数 | 20+ 个 BaseModel |
| 字段约束 | `gt`/`ge`/`le` 范围校验、`Literal` 类型限定 |
| 自定义校验 | `api_base_url` 非空、`data_source` 至少一个启用 |
| 渐进迁移 | `ConfigBundleV2.from_config_bundle()` 从现有 dict 无缝转换 |
| 新增依赖 | `pydantic>=2.0` |

**关键改进**：配置拼写错误/数值越界在启动时即可发现，IDE 自动补全配置字段。

### 2. 深度评估模块（`evaluation/deep_eval.py`, 1,093 行）

| 组件 | 功能 |
|------|------|
| `SourceLocator` | chunk JSONL 索引加载、按路径/行号定位、同目录发现 |
| `AccuracyVerifier` | LLM 驱动逐条引用准确性校验（consistent/inconsistent/unverifiable/source_missing） |
| `ComprehensivenessVerifier` | 路径相似度发现未引用但相关的源文件 |
| `DeepEvaluator` | 主编排器：逐页评估 → 汇总 + 分级修复建议（P0/P1/P2） |
| CLI 命令 | `deep-eval`（`--page-filter` / `--sample-rate` / `--pretty`） |

**与 `wiki-lint` 互补**：`wiki-lint` 检查结构（断链/过期/draft），`deep-eval` 校验内容（引用准确性/知识覆盖）。

### 3. 适配项

| iris2 原始 | iris3 适配 |
|------|------|
| 3 种 Wiki 类型 | 4 种（+person） |
| 2 个 chunk 源 | 1 个（work_docs_main） |
| 无 index/changelog 配置 | 新增 `IndexConfig` / `ChangelogConfig` |
| 无飞书配置 | 新增 `FeishuIngestConfig` |

### 测试

```
138 → 161 测试 (+23)
新增: test_pydantic_config.py (10), test_deep_eval.py (13)
```

### 关键指标

| 维度 | 改进前 (v3.6.0) | 改进后 (v3.7.0) |
|------|:---:|:---:|
| 源代码行数 | 14,688 | **16,183** |
| 源文件数 | 89 | **92** |
| 模块数 | 18 | **19** |
| CLI 命令 | 38 | **39** |
| 单元测试 | 138 | **161** |
| 配置类型安全 | ❌ 纯 dict | ✅ Pydantic v2 |
| Wiki 内容校验 | ❌ 无 | ✅ 准确性 + 全面性 |
| 依赖数 | 3 | **4**（+pydantic） |


## 🆕 v3.8.0 变更（2026-06-29）

### 复杂输入三阶段重构 + LLMService 统一入口

基于设计讨论（团队成员J + Claude Code），对复杂输入模块和 LLM 调用架构进行全面升级。

### 1. 复杂输入：双阶段 → 三阶段流水线（`complex_input/pipeline.py`）

| 组件 | 改进前 (v3.7.0) | 改进后 (v3.8.0) |
|------|:---:|------|
| 阶段数 | 2（adv→理解, base→整合） | **3**（base→指令, adv→理解, base→整合） |
| Stage 1 prompt | 硬编码 `STAGE1_PROMPT` | **base_model 根据 query + file_type 动态生成** |
| 文件类型检测 | 仅图片 | **image / pdf / document / video 多类型** |
| 非图片处理 | ❌ 无 | ✅ 明确提示 "暂不支持"（为后续扩展留接口） |
| 错误隔离 | 单层 try/except | **三层独立异常处理 + 降级传递** |

**三阶段流程**：
```
Stage 1  base_model  → 生成 adv_model 分析指令（新增 prompt_gen 路由规则）
Stage 2  adv_model   → 多模态理解非文本内容
Stage 3  base_model  → 整合润色输出
```

### 2. LLMService 统一入口（`llm/service.py`, 121 行）

| 特性 | 说明 |
|------|------|
| 统一创建 | 消除各模块各自 `EnvironmentConfiguredLLMProvider(config)` 的重复模式 |
| 便捷方法 | `generate()` / `generate_multimodal()` 封装路由上下文和异常处理 |
| 兼容过渡 | `get_provider()` 返回完整实例供高级用法 |

### 3. 检测器扩展（`complex_input/detector.py`）

- **文件类型分类**：`_classify_file()` 按扩展名映射到 image/pdf/document/video/unknown
- **全局常量**：`utils/constants.py` 新增 `FILE_TYPE_*`、`PDF_EXTENSIONS`、`DOCUMENT_EXTENSIONS`、`VIDEO_EXTENSIONS`、`COMPLEX_EXTENSIONS`
- **安全修复**：超大图片先做大小检查再加入 `resolved`，消除 `file_count` 与 `encoded_images` 不一致
- **安全修复**：`str.format()` → `_safe_format()`，消除用户输入含花括号时的 KeyError 崩溃风险

### 4. 路由规则扩展（`config/llm.json`）

新增 1 条路由规则（7→8）：

| 规则 | 优先级 | 匹配条件 | 路由目标 |
|------|:------:|------|:------:|
| `prompt_gen_go_base` | 7 | `task_type=prompt_gen` | base_model → adv_model |

### 5. 代码质量修复（v3.8 审查 7 项）

| # | 严重度 | 问题 | 修复 |
|---|:------:|------|------|
| C1 | Critical | 超大图片路径泄露进 resolved | 重构循环顺序 |
| C2 | Critical | `str.format()` 花括号崩溃 | `_safe_format()` 逐字段 replace |
| C3 | Critical | 方法体内 `from pathlib import Path` | 移到模块顶部 |
| H1 | High | 非图片文件处理返回空壳 | 返回 `[Stage2 跳过]` 明确提示 |
| H2 | High | 内部方法返回裸 `tuple` | `Tuple[str, Optional[str]]` |
| M1 | Medium | `known_type` 参数语义不清 | 重命名为 `force_type` |
| M2 | Medium | 纯文本误入 pipeline 静默失败 | 添加 `logger.warning` |

### 关键指标

| 维度 | 改进前 (v3.7.0) | 改进后 (v3.8.0) |
|------|:---:|:---:|
| 源代码行数 | 16,183 | **16,620** |
| 源文件数 | 92 | **93** |
| 模块数 | 19 | 19 |
| CLI 命令 | 39 | 40 |
| 单元测试 | 161 | 161 |
| 路由规则 | 7 | **8** |
| Pipeline 阶段 | 2 | **3** |
| LLM 统一入口 | ❌ 各自创建 | ✅ `LLMService` |
| 文件类型支持 | 仅图片 | image / pdf / document / video |
