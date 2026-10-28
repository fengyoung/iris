# Iris 3.44.0 — 项目执行说明

> 工作知识助手，个人知识库（Obsidian Wiki）+ 飞书团队知识库集成。
> 逐版变更记录与版本历史统一归档于 [CHANGELOG.md](CHANGELOG.md)；本文件只承载现行架构 / 配置 / 约定。

---

## 项目概览

**当前规模**：~50,700 行 / 231 个源码文件 / 30 模块 · CLI 77 命令 · 测试 4,179（pytest 全量）· 覆盖率 74.64%（`fail_under` 65，13 个关键模块另有独立门禁）· Ruff、mypy（含显式兼容边界）、AST 安全扫描与 SPDX SBOM 门禁通过 · 11 个项目级 Skill · Wiki 231 页 · 知识图谱节点 231 / 关系边 2,766 · 数据源 1,041 文档 / 7,979 Chunk（text-embedding-v3 / 1,024 维）

**最新版本**（v3.44.0）：飞书日报全面优化（风险句抽取 / 决策待审 / 按天幂等）+ iris-release 发版 Skill。详细变更历史见 [CHANGELOG.md](CHANGELOG.md)。

**关键路径**：

```
Obsidian 仓库：.../WORKSPACE/WIKI-ROOT/
├── SOURCE/   ← 数据源（9 类：01-目标管理 ~ 09-工作简报；_INDEX.md 自动维护）
└── LLM-WIKI/ ← Wiki 输出（01-领域 02-概念 03-项目 04-人物；index.md changelog.md）
```

路径通过 `.env` 中的 `${IRIS_WORK_DOCS_DIR}` 和 `${IRIS_WIKI_ROOT}` 配置。

---

## 核心架构

### Wiki 体系

核心理念：**"编译器而非解释器"**——将知识提前编译为结构化交叉链接 Markdown Wiki。

| 类型 | 目录 | 前缀 | 当前数量 |
|------|------|------|:---:|
| 领域 (domain) | `01-领域/` | `领域-` | 19 |
| 概念 (concept) | `02-概念/` | `概念-` | 22 |
| 项目 (project) | `03-项目/` | `项目-` | 16 |
| 人物 (person) | `04-人物/` | `人物-` | 174 |

命令：`discover-wiki` · `build-wiki` · `wiki-pipeline` · `wiki-lint [--fix]` · `wiki-update` · `enrich-persons` · `deep-eval` · `build-asr-prompt`

### 飞书集成

`feishu-doc-convert`（飞书文档→本地 Markdown）· `chat-digest`（聊天记录 AI 提炼）

### 会议纪要路由

`transcribe-meeting --to-source` 自动判定归档目录：`05-会议纪要/`（多人 ≥3）· `04-讨论思考/`（1对1/双人）· `03-方案报告/`（正式方案）· `08-参考资料/`（外部学习）。路由规则存于 `config/meeting_routes.json`。

### 记忆系统（6 子模块）

`long_term.py`（画像+概念纠正）· `session.py`（会话记忆）· `working.py`（工作上下文）· `lifecycle.py`（老化/冲突检测/合并）· `session_miner.py`（跨会话模式挖掘）· `manager.py`（浏览/删除/导入/导出）

**纠正规则置顶**（v3.43.0）：`pinned: true` 的长期规则免于 90 天自动老化，问答召回优先展示且不占普通名额；`memory-pin --concept <概念> [--unpin]` 人工置顶/取消，详见 `docs/memory-pinning.md`。

**记忆自动更新引擎**：双通道架构（正则快通道 + LLM 深通道）· 会话挖掘懒触发（Q&A 后 ≥24h + daily-start 兜底）· 自治生命周期（老化自动执行，纠正 ≥5 次自动确认）

### 知识图谱

三层架构叠加于 Wiki：① 节点（frontmatter 全量构建，零 LLM）→ ② 反向引用边（`[[wikilink]]` → `linked_to`，零 LLM）→ ③ LLM 关系边（负责/使用/属于，批量增量）

CLI：`build-graph [--full] [--page <title>]` · `graph-query --op <neighbors|related|path|orphans|bridges|density>`

### 复杂输入三阶段流水线

```
Stage 1 (base model) → 动态生成多模态分析指令
Stage 2 (adv model)  → 图片/PDF/DOCX/VIDEO 多模态理解
Stage 3 (base model) → 整合润色输出
```

PDF=PyMuPDF 提取+渲染；DOCX=python-docx 段落+表格；VIDEO=ffmpeg 抽帧 + Whisper 转写（依赖缺失优雅降级）

---

## 配置体系

优先级：**OS 环境变量 > `.env` > macOS Keychain**

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `BAILIAN_API_KEY` | 百炼 API 密钥 |
| `IRIS_WORK_DOCS_DIR` | SOURCE 数据源路径 |
| `IRIS_WIKI_ROOT` | LLM-WIKI 输出路径 |
| `IRIS_MEETING_TRANS_DIR` | 会议转写文件搜索目录 |
| `LARK_APP_ID` / `LARK_APP_SECRET` | 飞书应用凭证 |
| `IRIS_AGENT_ID` | 多 Agent 隔离标识（可选，默认 "default"） |
| `IRIS_PROJECT_ROOT` | 从仓库外启动时显式指定项目根 |

---

## 版本体系（三层解耦）

| 层 | 位置 | 当前值 | 含义 |
|------|------|:---:|------|
| **产品版本** | `pyproject.toml` | 3.44.0 | 软件发布版本 |
| **协议版本** | `src/iris/__init__.py` | 3.27 | CLI 命令集 / agent-spec 格式 |
| **数据版本** | `config/*.json` | app 3.8（其余独立演进） | 配置文件 Schema |

> 只有真正发生变化的层才递增版本号。

---

## 技术栈

Python 3.11+ · LLM API 双协议（OpenAI 兼容 / Anthropic Messages，经 zz_tokenhub 中转与百炼、DeepSeek 官方直连）· Pydantic v2 · lark-cli · PyMuPDF / python-docx · macOS Keychain

---

## 开发约定

- **长任务埋点规则**：新增长任务/常驻命令（分钟级以上）必须评估接入 `taskpanel.TaskReporter` 埋点
- **持久化规则**：共享状态读-改-写必须在 `FileLock` 临界区内完成；单文件 `atomic_write_*`，多文件制品 generation 目录原子切换
- **资源生命周期规则**：SQLite 等持久资源必须显式 `close()` 或使用上下文管理器
- **异常规则**：新增自定义异常必须继承 `iris.core.exceptions` 的 `IrisRuntimeError`/`IrisValueError`；需额外标准库父类用多继承。详见 `docs/EXCEPTION_HANDLING.md`
- **复杂度规则**：ruff C901 门禁 `max-complexity = 20`，超限 CI 直接失败；拆分手法优先「分阶段私有方法」「表驱动分派」「状态对象」
- **导入规则**：F401 已启用；仅包 `__init__.py` 与 `app/cli/handlers.py` 允许 re-export 未使用导入；`iris.core.exceptions` 零依赖
- **敏感文档规则**：调薪方案、人员盘点/评估过程、绩效评价、Leader 盘点类文档**只保留 SOURCE 原件**，不得进入任何下游衍生制品。判定唯一真相源为 `src/iris/wiki/_sensitive.py`（零依赖，仅匹配文件名/标题，**绝不匹配正文**）。存量清理用 `scripts/purge_sensitive_wiki.py`
- **发版版本号同步规则**：产品版本递增时同步 **5 个文件**——`pyproject.toml` · `CHANGELOG.md` · `CLAUDE.md`（5 个位置：标题/规模/最新版本行/版本体系表/项目结构用例数）· `README.md` · `AGENTS.md`。**「本版（vX.Y.Z）」叙述段跟随最近一次特性版、补丁版不追**

### 项目级 Skill 共享（Claude Code + DSH）

`.claude/skills/` 为唯一真身，DSH 通过 `.agents/skills -> ../.claude/skills` 软链读取。约定：

- **只允许目录包**：`<name>/SKILL.md`，禁止扁平 `<name>.md`（DSH 支持但 CC 拒绝，会造成静默不一致）
- **frontmatter 只写交集**：`name`（kebab-case）与 `description`（单行）必填；可选 `disable-model-invocation` / `user-invocable` / `metadata` / `version`
- **共享目录只放工具中立的 Skill**：正文命令走 `python3 scripts/run_cli.py ...`，不依赖单侧工具
- **正文路径以项目根为基准**：所有命令默认在 iris3 仓库根目录执行
- **软链是结构的一部分**：缺失时用 `mkdir -p .agents && ln -s ../.claude/skills .agents/skills` 重建

---

## 项目结构

```
iris3/
├── src/iris/          # 30 模块（config/llm/core/memory/qa/ingest/retrieval/wiki/...）
├── scripts/           # CLI 入口 + 委托脚本
├── templates/         # Prompt / Wiki 模板
├── tests/             # 4,179 用例（pytest 全量）
├── config/            # *.json gitignored，*.example 版本控制
├── data/              # 运行时数据（全 gitignore）
├── .claude/skills/    # 项目级 Skill 真身（11 个，CC 原生读取）
├── .agents/skills     # → ../.claude/skills（软链，DSH 据此发现）
├── .github/workflows/ # CI 流水线（Python 3.11-3.13）
└── memory/            # Claude 工作记忆
```

**src/iris 30 模块**：`config` · `llm` · `core` · `memory`（6 子模块）· `qa` · `ingest` · `retrieval` · `wiki`（最大模块，含 ASR 17 子模块）· `feed`（飞书聊天简报，11 文件）· `analysis` · `evaluation` · `complex_input` · `output` · `assistant`（实时会议参谋，14 文件）· `games`（谁是卧底）· `decisions`（决策库）· `briefing`（会前情报包）· `signals`（主动信号）· `okr_evidence`（OKR 证据）· `intelligence`（共享底座）· `app/cli`（77 命令）· `app/transcribe_meeting` · `feishu` · `utils` · `trello` · `taskpanel`（7 文件）

---

## 项目级 Skill（11 个，CC + DSH 共用）

`iris-daily-start` · `iris-wiki` · `iris-feishu-import` · `iris-feed` · `iris-meeting` · `iris-ask` · `iris-process` · `iris-report` · `iris-health` · `iris-okr-check` · `iris-release`
