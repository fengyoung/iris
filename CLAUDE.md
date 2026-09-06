# Iris 3.32.0 — 项目执行说明

> 工作知识助手，个人知识库（Obsidian Wiki）+ 飞书团队知识库集成。
> 逐版变更记录与版本历史统一归档于 [CHANGELOG.md](CHANGELOG.md)；本文件只承载现行架构 / 配置 / 约定。

---

## 项目概览

**当前规模**：~43,000 行 / 183 个源码文件 / 27 模块 · CLI 68 命令 · 测试 3,296（pytest 全量：unit 2,207 / integration 1,089，含参数化）· 覆盖率 68%（`fail_under` 65）· mypy 基线 193 errors（非阻断）· 10 个项目级 Skill · Wiki 241 页 · 知识图谱节点 220 / 关系边 1,858（wikilink 1,225 + LLM 633）· 数据源 900+ 文档 / 6,771 Chunk（text-embedding-v3 / 1,024 维）

**近期新增能力**：三阶段质量优化（F401/C901 门禁、`IrisError` 统一异常体系、mypy 基线、corrector/live 模块拆分）· 工程可靠性治理（SQLite 生命周期、稳定 inode 文件锁、统一原子写、向量索引 generation 发布、跨进程 LLM 缓存治理）· 任务面板 `taskpanel/`（Web 只读 + TaskReporter 埋点 + 探测兜底 + 常驻守护）· 实时会议助理 `assistant/`（逐段提炼要点/风险/决策点 + 实时提示提问 + 过程文档）· YAML frontmatter 标准化注入（`core/frontmatter.py`）+ 批量补全（`frontmatter_batch.py`，正则+LLM+备份恢复）· wikilink 自动注入引擎（零 LLM 成本）· LLM 用量追踪（SQLite WAL + embedding 纳入）· LLM 响应缓存 + embedding 向量缓存（LRU+TTL）· LLM 熔断器（threshold=5 / reset 60s）· 记忆自动更新引擎（双通道）· 多 Agent 并发安全（FileLock + SQLite WAL + Agent 隔离）· ASR 实时校正引擎（Aho-Corasick + LLM 编辑助手 + 反馈反向优化）· CI/CD（Makefile / pre-commit / GitHub Actions）+ pip-audit · constraints.txt 可复现构建 · sync-memory 双向化（CC↔Iris 记忆互通 + 前向备注噪音治理，daily-start 自动双向，见 `scripts/sync_memory.py`）· `llm-bench`（LLM 通道/模型 连接速度 TTFT + 吞吐基准，字符口径规避中继 usage 虚高，引擎 `llm/benchmark.py`）

**关键路径**：

```
Obsidian 仓库：.../WORKSPACE/WIKI-ROOT/
├── SOURCE/   ← 数据源（9 类：01-目标管理 02-部门管理 03-方案报告 04-讨论思考
│                05-会议纪要 06-我的周报 07-成员周报 08-参考资料 09-工作简报；v2-data/ 软链）
└── LLM-WIKI/ ← Wiki 输出（01-领域 02-概念 03-项目 04-人物；index.md changelog.md）
```

路径通过 `.env` 中的 `${IRIS_WORK_DOCS_DIR}` 和 `${IRIS_WIKI_ROOT}` 配置。

---

## 核心架构

### Wiki 体系

核心理念：**"编译器而非解释器"**——将知识提前编译为结构化交叉链接 Markdown Wiki。

| 类型 | 目录 | 前缀 | 当前数量 |
|------|------|------|:---:|
| 领域 (domain) | `01-领域/` | `领域-` | 14 |
| 概念 (concept) | `02-概念/` | `概念-` | 11 |
| 项目 (project) | `03-项目/` | `项目-` | 21 |
| 人物 (person) | `04-人物/` | `人物-` | 400+ |

命令：`discover-wiki`（发现候选，4 类型分层排序）· `build-wiki`（单页/批量/审核）· `build-wiki-nav`（index.md）· `wiki-pipeline`（发现→审核→生成）· `wiki-lint [--fix]`（6 维检查/修复）· `wiki-update`（增量，daily-start 集成）· `enrich-persons`（通讯录补人物部门/邮箱）· `deep-eval`（引用准确性+全面性）· `build-asr-prompt`（热词→误识别→策略三段）。

### 飞书集成

`feishu-doc-convert`（飞书文档→本地 Markdown + 路由归档 + 排重）· `chat-digest`（聊天记录 AI 提炼为结构化文档）。

### 会议纪要路由

`transcribe-meeting --to-source` 自动判定归档目录：`05-会议纪要/`（多人 ≥3 正式会议）· `04-讨论思考/`（1对1/双人讨论，首要信号）· `03-方案报告/`（正式方案或技术结论）· `08-参考资料/`（外部学习资料）。路由规则存于 `config/meeting_routes.json`（gitignored），代码零硬编码。

### 记忆系统（6 子模块）

`long_term.py`（画像+概念纠正，写时自动压缩）· `session.py`（会话记忆）· `working.py`（工作上下文 Markdown）· `lifecycle.py`（老化/冲突检测/合并，默认自动老化）· `session_miner.py`（跨会话模式挖掘，自动晋升长期记忆）· `manager.py`（浏览/删除/导入/导出编排）。

**记忆自动更新引擎**（v3.19.14）：双通道架构（`MemoryUpdater` 正则快通道 + LLM 深通道）· 会话挖掘懒触发（Q&A 后 ≥24h + daily-start 兜底）· 自治生命周期（老化自动执行，纠正 ≥5 次自动确认，写入超标当场压缩）· 触发机制（Q&A 实时 → daily-start 每日 → 写时检查）。

### 知识图谱

三层架构叠加于 Wiki 体系：① 节点（frontmatter 全量构建，零 LLM）→ ② 反向引用边（`[[wikilink]]` → `linked_to`，零 LLM）→ ③ LLM 关系边（负责/使用/属于/…，批量增量）。

CLI：`build-graph [--full] [--page <title>]` · `graph-query --op <neighbors|related|path|orphans|bridges|density>`；集成 `daily-start` 自动维护链。

### 复杂输入三阶段流水线

```
Stage 1 (base model) → 动态生成多模态分析指令
Stage 2 (adv model)  → 图片/PDF/DOCX/VIDEO 多模态理解
Stage 3 (base model) → 整合润色输出
```

PDF=PyMuPDF 提取文字 + 逐页渲染；DOCX=python-docx 段落+表格文字；VIDEO=ffmpeg 均匀抽帧 + Whisper 音轨转写（依赖缺失优雅降级）。

---

## 配置体系

优先级：**OS 环境变量 > `.env` > macOS Keychain**。分层：`.env`（gitignored）· `config/*.json`（gitignored）· `config/*.json.example`（版本控制）· `data/`（全 gitignore）。

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `BAILIAN_API_KEY` | 百炼 API 密钥 |
| `IRIS_WORK_DOCS_DIR` | SOURCE 数据源路径 |
| `IRIS_WIKI_ROOT` | LLM-WIKI 输出路径 |
| `IRIS_MEETING_TRANS_DIR` | 会议转写文件搜索目录 |
| `LARK_APP_ID` / `LARK_APP_SECRET` | 飞书应用凭证 |
| `IRIS_AGENT_ID` | 多 Agent 隔离标识（可选，默认 "default"） |
| `IRIS_PROJECT_ROOT` | 从仓库外启动时显式指定 Iris 项目根目录 |

---

## 版本体系（三层解耦）

| 层 | 位置 | 当前值 | 含义 |
|------|------|:---:|------|
| **产品版本** | `pyproject.toml` | 3.32.0 | 软件发布版本 |
| **协议版本** | `src/iris/__init__.py` | 3.22 | CLI 命令集 / agent-spec 格式 |
| **数据版本** | `config/*.json` | app 3.7（其余独立演进） | 配置文件 Schema |

> 只有真正发生变化的层才递增版本号。

---

## 技术栈

Python 3.11+ · OpenAI 兼容 LLM API（DeepSeek / 百炼 / Qwen）· Pydantic v2（配置校验）· lark-cli（飞书接口层）· PyMuPDF / python-docx（文档处理）· macOS Keychain（可选密钥存储）。

---

## 开发约定

- **长任务埋点规则（v3.27.0 起）**：新增长任务/常驻命令（分钟级以上）必须评估接入 `taskpanel.TaskReporter` 埋点——启动注册、关键阶段 `report_phase()`、结束写终态；不接需说明理由（如探测兜底即可）。
- **持久化规则（v3.28.0 起）**：共享状态读-改-写必须在 `FileLock` 临界区内完成，`.lock` 释放后必须保留；单文件 `atomic_write_text/bytes/json`，多文件制品 generation 目录写全后原子切换指针。
- **资源生命周期规则（v3.28.0 起）**：SQLite 等持久资源必须显式 `close()` 或使用上下文管理器；不得依赖垃圾回收释放文件描述符。
- **异常规则（v3.30.0 起）**：新增自定义异常必须继承 `iris.core.exceptions` 的 `IrisRuntimeError`（外部依赖/运行期失败）或 `IrisValueError`（输入/配置不合法）；需要额外标准库父类时用多继承 `class X(IrisError, PermissionError)`。调用方捕获优先 `except IrisError`。
- **复杂度规则（v3.30.0 起）**：ruff C901 门禁 `max-complexity = 20`，新增/修改函数超限 CI 直接失败；拆分手法优先「分阶段私有方法」「表驱动分派」「状态对象」，不要靠 `# noqa: C901` 绕过。
- **导入规则（v3.30.0 起）**：F401 已启用；仅包 `__init__.py` 与 `app/cli/handlers.py`（facade）允许 re-export 未使用导入；其余模块需要保留给外部导入的符号必须显式 `__all__` 或在真正的定义处导入。`iris.core.exceptions` 零依赖，底层模块（config/utils）只从它导入，不得 `from iris.core import ...`（会经 `core/__init__` 触发循环导入）。

---

## 项目结构

```
iris3/
├── src/iris/          # 27 模块（见下）
├── scripts/           # CLI 入口 + 委托脚本
├── templates/         # Prompt / Wiki 模板
├── tests/             # 3,296 用例（pytest 全量：unit 2,207 / integration 1,089，conftest 自动打标记）
├── config/            # *.json gitignored，*.example 版本控制
├── data/              # 运行时数据（全 gitignore）
├── .claude/skills/    # 项目级 Skill（10 个）
├── .github/workflows/ # CI 流水线（Python 3.11-3.13）
├── memory/            # Claude 工作记忆
└── Makefile · pyproject.toml · README · CLAUDE · CHANGELOG.md
```

**src/iris 模块**：`config`（加载+Pydantic 校验）· `llm`（Provider/路由/LLMService/用量统计/`benchmark.py` 连接吞吐基准）· `core`（类型/锁/写保护/存储/Agent 适配/共享线程池/`exceptions.py` 统一异常基类）· `memory`（记忆 6 子模块：含 `session_miner.py`）· `qa`（检索问答+图谱注入+`memory_updater.py` 双通道记忆提取）· `ingest`（扫描/切块）· `retrieval`（BM25+向量+RRF+BM25缓存）· `wiki`（Wiki 体系 + backlink/graph + ASR 校正引擎，最大模块；`wiki/asr/` 含 corrector/_hotkey/_trie/_diff/_clipboard_io/_text_detector/coverage/feedback/prompt_optimizer 等 14 子模块）· `feed`（飞书聊天记录→话题检测→简报生成，11 文件 / 9 命令）· `analysis`（报告/思维导图）· `evaluation`（Wiki 深度评估 + 引用解析）· `complex_input`（多模态三阶段）· `output`（格式化+DOCX）· `assistant`（实时 AI 会议参谋：本地 ASR+校正+检索+批量分析+话题/说话人+洞察推送+面板/文档，14 文件；`live.py` 编排 + `_audio_capture.py` 合并缓冲 + `_batch_processor.py` 批处理纯逻辑）· `app/cli`（68 命令）· `app/transcribe_meeting`（会议转录）· `feishu`（文档/聊天提炼）· `utils`（paths.py / shared.py）· `trello`（看板）· `taskpanel`（任务埋点 + Web 只读展示 + 常驻守护，7 文件）。

---

## Claude Code Skill（10 个项目级）

`iris-daily-start`（每日启动维护）· `iris-wiki`（发现→审核→生成）· `iris-feishu-import`（飞书文档/聊天导入）· `iris-feed`（群聊话题检测→简报）· `iris-meeting`（转写→纪要→归档）· `iris-ask`（问答）· `iris-process`（图片/PDF/DOCX/视频）· `iris-report`（分析报告/思维导图/双周报）· `iris-health`（质量巡检）· `iris-okr-check`（OKR 双周逐项检查）。
