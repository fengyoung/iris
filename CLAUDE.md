# Iris 3.21.1 — 项目执行说明

> 工作知识助手，个人知识库（Obsidian Wiki）+ 飞书团队知识库集成。
> 完整版本历史见 [CHANGELOG.md](CHANGELOG.md)。

---

## 项目概览

### 当前规模

~32,000 行 / 153 文件 / 25 模块 · CLI 50 命令 · 单元测试 2,626（134 文件）· 覆盖率 62%+ · 10 个项目级 Skill · Wiki 221 页 · 知识图谱节点 219 / 关系边 2,161（wikilink 1,175 + LLM 986） · 数据源 776 文档 / 9,019 Chunk（text-embedding-v3 / 1,024 维）

**近期新增能力**：YAML frontmatter 标准化注入（`core/frontmatter.py`）· 批量 frontmatter 补全（`core/frontmatter_batch.py`，正则+LLM+wikilink+备份恢复）· wikilink 自动注入引擎（`wiki/wikilink_injector.py`，零 LLM 成本）· LLM 用量追踪（SQLite WAL + embedding 纳入）· LLM 响应缓存 + embedding 向量缓存（LRU + TTL）· LLM 熔断器（`_CircuitBreaker`，threshold=5 / reset 60s）· 记忆自动更新引擎（`memory_updater.py` + `session_miner.py`，双通道架构）· 多 Agent 并发安全（FileLock + SQLite WAL + Agent 隔离）· ASR 实时校正引擎（Aho-Corasick + LLM 编辑助手 + 反馈反向优化）· CI/CD（Makefile / pre-commit / GitHub Actions）+ pip-audit · constraints.txt 可复现构建

### 关键路径

```
Obsidian 仓库：.../WORKSPACE/
                └── WIKI-ROOT/
                    ├── SOURCE/          ← 数据源（9 类分层）
                    │   ├── 01-目标管理/  02-部门管理/
                    │   ├── 03-方案报告/  04-讨论思考/
                    │   ├── 05-会议纪要/  06-我的周报/
                    │   ├── 07-成员周报/  08-参考资料/
                    │   ├── 09-工作简报/
                    │   └── v2-data/ → ../../v2-data (软链)
                    └── LLM-WIKI/        ← Wiki 输出（4 种页面类型）
                        ├── 01-领域/  02-概念/  03-项目/  04-人物/
                        ├── index.md  changelog.md
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
| 人物 (person) | `04-人物/` | `人物-` | 436 |

Wiki 命令：`discover-wiki`（发现候选，4 类型分层排序）· `build-wiki`（生成页面，单页/批量/审核）· `build-wiki-nav`（维护 index.md）· `wiki-pipeline`（发现→审核→生成）· `wiki-lint [--fix]`（6 维健康检查/修复）· `wiki-update`（增量更新，daily-start 集成）· `enrich-persons`（飞书通讯录补充人物部门/邮箱）· `deep-eval`（深度评估：引用准确性+全面性）· `build-asr-prompt`（三段 Pipeline：热词→误识别→策略 Prompt）。

### 飞书集成

| 管道 | 说明 |
|------|------|
| `feishu-doc-convert` | 飞书文档 → 本地 Markdown + 路由归档 + 排重 |
| `chat-digest` | 聊天记录 AI 提炼为结构化文档 |

### 会议纪要路由

`transcribe-meeting --to-source` 自动判定归档目录：

| 路由目标 | 判定条件 |
|---------|---------|
| `05-会议纪要/` | 多人（≥3）正式会议 |
| `04-讨论思考/` | 1对1/双人讨论（首要信号） |
| `03-方案报告/` | 产出正式方案或技术结论 |
| `08-参考资料/` | 外部学习资料 |

路由规则存于 `config/meeting_routes.json`（gitignored），代码零硬编码。

### 记忆系统（6 子模块）

`long_term.py`（用户画像+概念纠正，写入时自动压缩）· `session.py`（会话记忆）· `working.py`（工作上下文 Markdown）· `lifecycle.py`（自治维护：老化/冲突检测/合并，默认自动老化）· `session_miner.py`（会话模式挖掘：LLM 分析跨会话模式，自动晋升为长期记忆）· `manager.py`（统一编排：浏览/删除/导入/导出）。

**记忆自动更新引擎**（v3.19.14 新增）：
- **双通道架构**：`MemoryUpdater`（`qa/memory_updater.py`）→ 正则快速通道（显式命令，免费毫秒级）+ LLM 深度通道（完整对话分析，轻量模型按需触发）
- **会话挖掘**：`SessionPatternMiner`（`memory/session_miner.py`）→ 懒触发（Q&A 后 ≥24h 检查）+ daily-start 兜底，发现高频主题/偏好模式/新事实
- **自治生命周期**：老化默认自动执行（daily-start + memory-maintenance），纠正 ≥5 次自动确认，写入时列表超标当场压缩
- **触发机制**：Q&A 实时 → daily-start 每日 → 写入时检查（融入业务流程，无需独立调度）

### 知识图谱

三层架构叠加在 Wiki 体系之上：
- **第一层（节点）**：从 Wiki frontmatter 全量构建实体节点，零 LLM 成本
- **第二层（反向引用边）**：从 `[[wikilink]]` 构建 `linked_to` 边，零 LLM 成本
- **第三层（LLM 关系边）**：批量 LLM 提取语义关系（负责/使用/属于/…），增量更新

CLI：`build-graph [--full] [--page <title>]`（构建/更新）· `graph-query --op <neighbors|related|path|orphans|bridges|density> [--node/--to/--hops/--min-degree]`（查询）。集成到 `daily-start` 自动维护链。

### 复杂输入三阶段流水线

```
Stage 1 (base model)  → 动态生成多模态分析指令
Stage 2 (adv model)   → 图片/PDF/DOCX/VIDEO 多模态理解
Stage 3 (base model)  → 整合润色输出
```

PDF 通过 PyMuPDF 提取文字 + 逐页渲染；DOCX 通过 python-docx 提取段落+表格文字；VIDEO 通过 ffmpeg 均匀抽帧 + Whisper 音轨转写（依赖缺失时优雅降级）。

---

## 配置体系

优先级：**OS 环境变量 > `.env` > macOS Keychain**。分层：`.env`（gitignored）· `config/*.json`（gitignored）· `config/*.json.example`（版本控制）· `data/`（全 gitignore）。

### 关键环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `BAILIAN_API_KEY` | 百炼 API 密钥 |
| `IRIS_WORK_DOCS_DIR` | SOURCE 数据源路径 |
| `IRIS_WIKI_ROOT` | LLM-WIKI 输出路径 |
| `IRIS_MEETING_TRANS_DIR` | 会议转写文件搜索目录 |
| `LARK_APP_ID` / `LARK_APP_SECRET` | 飞书应用凭证 |
| `IRIS_AGENT_ID` | 多 Agent 隔离标识（可选，默认 "default"） |

---

## 版本体系（三层解耦）

| 层 | 位置 | 当前值 | 含义 |
|------|------|:---:|------|
| **产品版本** | `pyproject.toml` | 3.21.1 | 软件发布版本 |
| **协议版本** | `src/iris/__init__.py` | 3.15 | CLI 命令集 / agent-spec 格式 |
| **数据版本** | `config/*.json` | 3.3/3.4 | 配置文件 Schema |

> 只有真正发生变化的层才递增版本号。

---

## 技术栈

Python 3.9+ · OpenAI 兼容 LLM API（DeepSeek / 百炼 / Qwen）· Pydantic v2（配置校验）· lark-cli（飞书接口层）· PyMuPDF / python-docx（文档处理）· macOS Keychain（可选密钥存储）。

---

## 项目结构

```
iris3/
├── src/iris/          # 25 模块（见下）
├── scripts/           # CLI 入口 + 委托脚本
├── templates/         # Prompt / Wiki 模板
├── tests/             # 2,626 用例，134 文件
│   ├── unit/          #   纯逻辑单元测试（419 用例，0.5s）
│   └── integration/   #   集成测试（1,334 用例）
├── config/            # *.json gitignored，*.example 版本控制
├── data/              # 运行时数据（全 gitignore）
├── .claude/skills/    # 项目级 Skill（9 个）
├── .github/workflows/ # CI 流水线（Python 3.9-3.12）
├── memory/            # Claude 工作记忆
├── Makefile           # 常用开发命令
└── pyproject.toml · README · CLAUDE · CHANGELOG.md
```

**src/iris 模块**：`config`（加载+Pydantic 校验）· `llm`（Provider/路由/LLMService/用量统计）· `core`（类型/锁/写保护/存储/Agent 适配/共享线程池）· `memory`（记忆 6 子模块：含 `session_miner.py` 会话模式挖掘）· `qa`（检索问答+图谱注入+`memory_updater.py` 双通道记忆提取）· `ingest`（扫描/切块）· `retrieval`（BM25+向量+RRF+BM25缓存）· `wiki`（Wiki 体系 + backlink/graph + ASR 校正引擎，最大模块；`wiki/asr/` 含 corrector/coverage/feedback/prompt_optimizer/_progress 等 10 个子模块）· `feed`（信息汇聚管道：飞书聊天记录→话题检测→简报生成，11 文件 / 9 命令）· `analysis`（报告/思维导图）· `evaluation`（Wiki 深度评估 + 引用解析）· `complex_input`（多模态三阶段：图片/PDF/DOCX/VIDEO）· `output`（格式化+DOCX）· `app/cli`（59 命令）· `app/transcribe_meeting`（会议转录）· `feishu`（文档/聊天提炼）· `utils`（含 paths.py / shared.py）· `trello`（看板）。

---

## Claude Code Skill（9 个项目级）

`iris-daily-start`（每日启动维护）· `iris-wiki`（发现→审核→生成）· `iris-feishu-import`（飞书文档/聊天导入）· `iris-meeting`（转写→纪要→归档）· `iris-ask`（问答）· `iris-process`（富媒体处理：图片/PDF/DOCX/视频）· `iris-report`（分析报告/思维导图/双周报）· `iris-health`（质量巡检）· `iris-okr-check`（OKR 双周逐项检查）。

---

## 近期变更

**当前 v3.21.1 (2026-08-02)** — SOURCE 归档适配全面修复：① 双周报文件名改为日期前缀（`YYYYMMDD-` 前缀），`resolve_source_archive_path` 正则正确匹配→归档到 `06-我的周报/YYYY/`；② `refresh_meeting_minutes` 非递归查找修复（`find_source_matches`→`rglob`），已归档会议纪要可正确备份/去重；③ `_stage0b_load_style` 风格源文件递归查找修复（`rglob`→扁平→找到 `YYYY/` 下文件）；④ feed 简报生成使用 `resolve_source_archive_path` 替代硬编码 YYYYMM；⑤ 移除 `transcribe_meeting/pipeline.py` 两个扁平路径死函数；⑥ 5 个 Skill 文档 SOURCE 路径更新（iris-okr-check / iris-feed / iris-report / iris-feishu-import / iris-meeting 共 18 处）。附加：`extract_weekly_reports.py` `_resolve_output_dir` 文档一致性注释。协议版本 3.15（不变）。产品版本 3.21.0→3.21.1。

**v3.21.0 (2026-08-02)** — SOURCE 元数据工程：① 新增 `frontmatter-batch` 批量补全命令（新模块 `core/frontmatter_batch.py` ~610 行 — 正则快速通道零 LLM 成本 + LLM 深度通道按 9 类目录字段映射 + wikilink 可选注入 + 自动备份/一键恢复 + 幂等跳过）；② wikilink 注入收敛 — 从 4 个管道（doc-convert/chat-digest/transcribe-meeting/extract-weekly-reports）移除，统一由 frontmatter-batch 按需注入；③ 周报按月归档 — `extract-weekly-reports` 输出自动归入 YYYYMM 月份子目录；④ 双周报 frontmatter 注入（title/date/type/period/author）+ analysis `period` 字段。测试 +65（2,626 用例 / 134 文件）。协议版本 3.14→3.15（新增 frontmatter-batch 命令）。产品版本 3.20.2→3.21.0。

**v3.20.2 (2026-07-30)** — 双线合并：① SOURCE 文档质量系统性提升 — YAML frontmatter 标准化（新增 `core/frontmatter.py` 统一工具模块，4 个 CLI 管道输出全部注入 frontmatter 元数据）、wikilink 自动注入引擎（新增 `wiki/wikilink_injector.py`，零 LLM 成本，4 管道集成）、成员周报 Prompt 增强（4 段落结构 + 量化指标要求 + 项目上下文注入）、周报质量门禁（`check_quality`，三级判定，不阻塞写入），测试 +54；② 测试覆盖率系统提升 — 新增 253 个单元测试（9 个新文件），覆盖率 59.87% → 62.82%，覆盖 chunker/formatter/hotwords/lifecycle/embedder/memory_updater/session_miner/feishu_bridge/agent_adapter 等模块。协议版本 3.14（不变）。产品版本 3.20.1→3.20.2。

**v3.20.1 (2026-07-30)** — deep_eval 配置路径改进：chunk 摘要文件路径由硬编码 `main_source_chunk_summary.json` 改为根据 `config.data_source.default_source` 动态加载，便于多数据源切换。协议版本 3.14（不变）。产品版本 3.20.0→3.20.1。

**v3.20.0 (2026-07-30)** — iris-feed 文档提取（Step 5）：新模块 `_doc_extractor.py`（199 行），从话题消息中自动收集飞书文档链接（docx/wiki/sheet/base），调用 `FeishuDocConverter` 转换为本地 Markdown 并关联到简报，支持跨次排重。配置项 `extract_docs` / `doc_extract_max`，新增 CLI 参数 `--no-extract-docs`。测试 +37（2,254 用例）。协议版本 3.13→3.14（新增 `--no-extract-docs` 参数）。产品版本 3.19.26→3.20.0。

**v3.19.26 (2026-07-29)** — 检索与知识库时效性四项优化：① chunk 切块重叠（`chunk_overlap_chars` 默认 150，跨段落承接信息不再丢失）；② Wiki `source_fingerprint` 源文档指纹追踪（frontmatter 记录引用源 hash，过时判定从「按天数猜」变「源文档变化精准触发」，天数阈值降为无指纹旧页面的兜底）；③ 向量索引 embedder 模型不匹配硬失败 + `--force-rebuild` 全量重建参数（此前仅 warning 且提示的参数不存在，旧向量带病混用）；④ 主动提醒引擎 `reminders` 命令 + daily-start 集成（栏目断供/成员周报缺失/项目停滞三类信号，零 LLM 成本，阈值 `app.json.reminders` 可配）。顺带修复：`_chunk_document` 生成器 return 值被丢弃导致 PDF 文档 0 chunk、`write_hash_index` 已有条目 hash 永不更新、`AppConfig` 未声明 `retrieval`/`organization` 字段导致 app.json 中 RRF 权重配置从未生效。协议版本 3.12→3.13（新增 reminders 命令）。测试 +54。

**v3.19.25 (2026-07-28)** — iris-feed 简报质量跃升：两阶段 LLM 架构（Phase 1 检测+合并+OKR / Phase 2 逐话题并发深度摘要），去掉消息截断利用 1M 上下文，Prompt 重写提升讨论要点/引述/决策结构化输出，`_extract_json` 嵌套数组误提取 bug 修复，简报模板编号修正+引述合并兜底。合并 0728-beta：输入截断保护 + `_fill_fallback_summary` 兜底 + 移除 `_llm_detect`/`_parse_llm_response` 死代码。测试 2,154→2,162。协议版本 3.12。7 文件 +725/-268 行。

**v3.19.24 (2026-07-28)** — 全量代码质量加固（第二轮）：P0 项 Dockerfile 修复/CI 安全门禁/硬编码路径消除 3 项 + P1 项 feed 测试补齐 +177 用例/SecretStr API 密钥保护/pre-commit 工具链升级（ruff v0.11 + 8 基础钩子）/静默异常修复 4 项 + P2 项 logger.exception 替换/导入风格统一/CI 覆盖率合并 3 项。测试 1,977→2,154。协议版本 3.12。16 文件。

> 历史版本摘要（v3.12.x ~ v3.19.10）见 [CHANGELOG.md](CHANGELOG.md)。
