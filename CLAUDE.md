# Iris 3.20.2 — 项目执行说明

> 工作知识助手，个人知识库（Obsidian Wiki）+ 飞书团队知识库集成。
> 完整版本历史见 [CHANGELOG.md](CHANGELOG.md)。

---

## 项目概览

### 当前规模

~32,000 行 / 151 文件 / 25 模块 · CLI 49 命令 · 单元测试 2,307（121 文件）· 覆盖率 60%+ · 10 个项目级 Skill · Wiki 221 页 · 知识图谱节点 219 / 关系边 2,161（wikilink 1,175 + LLM 986，NetworkX 引擎） · 数据源 776 文档 / 9,019 Chunk · 向量索引 9,019 条（text-embedding-v3 / 1,024 维） · YAML frontmatter 标准化注入（4 管道 + `core/frontmatter.py` 统一工具）· wikilink 自动注入引擎（`wiki/wikilink_injector.py`，零 LLM 成本，基于 Wiki 标题索引）· LLM 用量追踪（SQLite WAL + embedding 纳入 + CLI/Skill 来源标记） · LLM 响应缓存（内存 LRU 驱逐）· embedding 向量缓存（LRU + TTL 600s）· LLM 熔断器（`_CircuitBreaker`，threshold=5 / reset 60s）· 记忆自动更新引擎（LLM 深度提取 + 会话模式挖掘 + 全自治生命周期，`memory_updater.py` + `session_miner.py`，双通道架构）· 多 Agent 并发安全（FileLock 推广 + SQLite WAL + Agent 记忆隔离 `IRIS_AGENT_ID` + 进程注册表 `ProcessRegistry`）· ASR 实时校正引擎（剪贴板监听 + Aho-Corasick + LLM 编辑助手，`_clipboard_io.py` + `_text_detector.py` 拆分，替换词典热加载 + 手动热词合并）· ASR 反馈反向优化引擎（feedback.jsonl 驱动词典自动进化，僵尸规则淘汰 + LLM 发现提升 + 热词补充）· ASR 独立熔断器 + 超时配置 · LLM deadline 实时超时控制 · Wiki 引用校验 · 结构化日志 · 共享线程池 · 多工作空间 · 文件监听 · CI/CD（Makefile / pre-commit / GitHub Actions）+ pip-audit 安全审计 · constraints.txt 可复现构建 · ASR Pipeline 交互式进度输出。

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
| **产品版本** | `pyproject.toml` | 3.20.1 | 软件发布版本 |
| **协议版本** | `src/iris/__init__.py` | 3.14 | CLI 命令集 / agent-spec 格式 |
| **数据版本** | `config/*.json` | 3.3/3.4 | 配置文件 Schema |

> 只有真正发生变化的层才递增版本号。

---

## 技术栈

Python 3.9+ · OpenAI 兼容 LLM API（DeepSeek / 百炼 / Qwen）· Pydantic v2（配置校验）· lark-cli（飞书接口层）· PyMuPDF / python-docx（文档处理）· macOS Keychain（可选密钥存储）。

---

## 项目结构

```
iris3/
├── src/iris/          # 21 模块（见下）
├── scripts/           # CLI 入口 + 委托脚本
├── templates/         # Prompt / Wiki 模板
├── tests/             # 2,154 用例，119 文件
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

**src/iris 模块**：`config`（加载+Pydantic 校验）· `llm`（Provider/路由/LLMService/用量统计）· `core`（类型/锁/写保护/存储/Agent 适配/共享线程池）· `memory`（记忆 6 子模块：含 `session_miner.py` 会话模式挖掘）· `qa`（检索问答+图谱注入+`memory_updater.py` 双通道记忆提取）· `ingest`（扫描/切块）· `retrieval`（BM25+向量+RRF+BM25缓存）· `wiki`（Wiki 体系 + backlink/graph + ASR 校正引擎，最大模块；`wiki/asr/` 含 corrector/coverage/feedback/prompt_optimizer/_progress 等 10 个子模块）· `feed`（信息汇聚管道：飞书聊天记录→话题检测→简报生成，11 文件 / 9 命令）· `analysis`（报告/思维导图）· `evaluation`（Wiki 深度评估 + 引用解析）· `complex_input`（多模态三阶段：图片/PDF/DOCX/VIDEO）· `output`（格式化+DOCX）· `app/cli`（58 命令）· `app/transcribe_meeting`（会议转录）· `feishu`（文档/聊天提炼）· `utils`（含 paths.py / shared.py）· `trello`（看板）。

---

## Claude Code Skill（9 个项目级）

`iris-daily-start`（每日启动维护）· `iris-wiki`（发现→审核→生成）· `iris-feishu-import`（飞书文档/聊天导入）· `iris-meeting`（转写→纪要→归档）· `iris-ask`（问答）· `iris-process`（富媒体处理：图片/PDF/DOCX/视频）· `iris-report`（分析报告/思维导图/双周报）· `iris-health`（质量巡检）· `iris-okr-check`（OKR 双周逐项检查）。

---

## 近期变更

**当前 v3.20.2 (2026-07-30)** — SOURCE 文档质量系统性提升：① YAML frontmatter 标准化（新增 `core/frontmatter.py` 统一工具模块，`build_frontmatter` / `inject_frontmatter` / `parse_frontmatter`，4 个 CLI 管道输出全部注入 frontmatter 元数据）；② wikilink 自动注入引擎（新增 `wiki/wikilink_injector.py`，基于 Wiki 标题索引 + 保护区屏蔽机制，零 LLM 成本，4 管道集成）；③ 成员周报 Prompt 增强（4 段落结构 + 量化指标要求 + 项目上下文注入）；④ 周报质量门禁（`check_quality`，不合格标记 `ai_quality: low`，不阻塞写入）。测试 +54（2,307 用例）。协议版本 3.14（不变）。产品版本 3.20.1→3.20.2。

**v3.20.1 (2026-07-30)** — deep_eval 配置路径改进：chunk 摘要文件路径由硬编码 `main_source_chunk_summary.json` 改为根据 `config.data_source.default_source` 动态加载，便于多数据源切换。协议版本 3.14（不变）。产品版本 3.20.0→3.20.1。

**v3.20.0 (2026-07-30)** — iris-feed 文档提取（Step 5）：新模块 `_doc_extractor.py`（199 行），从话题消息中自动收集飞书文档链接（docx/wiki/sheet/base），调用 `FeishuDocConverter` 转换为本地 Markdown 并关联到简报，支持跨次排重。配置项 `extract_docs` / `doc_extract_max`，新增 CLI 参数 `--no-extract-docs`。测试 +37（2,254 用例）。协议版本 3.13→3.14（新增 `--no-extract-docs` 参数）。产品版本 3.19.26→3.20.0。

**v3.19.26 (2026-07-29)** — 检索与知识库时效性四项优化：① chunk 切块重叠（`chunk_overlap_chars` 默认 150，跨段落承接信息不再丢失）；② Wiki `source_fingerprint` 源文档指纹追踪（frontmatter 记录引用源 hash，过时判定从「按天数猜」变「源文档变化精准触发」，天数阈值降为无指纹旧页面的兜底）；③ 向量索引 embedder 模型不匹配硬失败 + `--force-rebuild` 全量重建参数（此前仅 warning 且提示的参数不存在，旧向量带病混用）；④ 主动提醒引擎 `reminders` 命令 + daily-start 集成（栏目断供/成员周报缺失/项目停滞三类信号，零 LLM 成本，阈值 `app.json.reminders` 可配）。顺带修复：`_chunk_document` 生成器 return 值被丢弃导致 PDF 文档 0 chunk、`write_hash_index` 已有条目 hash 永不更新、`AppConfig` 未声明 `retrieval`/`organization` 字段导致 app.json 中 RRF 权重配置从未生效。协议版本 3.12→3.13（新增 reminders 命令）。测试 +54。

**v3.19.25 (2026-07-28)** — iris-feed 简报质量跃升：两阶段 LLM 架构（Phase 1 检测+合并+OKR / Phase 2 逐话题并发深度摘要），去掉消息截断利用 1M 上下文，Prompt 重写提升讨论要点/引述/决策结构化输出，`_extract_json` 嵌套数组误提取 bug 修复，简报模板编号修正+引述合并兜底。合并 0728-beta：输入截断保护 + `_fill_fallback_summary` 兜底 + 移除 `_llm_detect`/`_parse_llm_response` 死代码。测试 2,154→2,162。协议版本 3.12。7 文件 +725/-268 行。

**v3.19.24 (2026-07-28)** — 全量代码质量加固（第二轮）：P0 项 Dockerfile 修复/CI 安全门禁/硬编码路径消除 3 项 + P1 项 feed 测试补齐 +177 用例/SecretStr API 密钥保护/pre-commit 工具链升级（ruff v0.11 + 8 基础钩子）/静默异常修复 4 项 + P2 项 logger.exception 替换/导入风格统一/CI 覆盖率合并 3 项。测试 1,977→2,154。协议版本 3.12。16 文件。

> v3.19.10 (2026-07-21)：ASR 引擎全面质量加固（P0~P3 十四项）：P0 Prompt `protected_terms` 字符串截断修复 / P1 热键校验 + 超时 + 参数化 / P2 预检查 + warning + 校验 / P3 Aho-Corasick 优化 + worker 动态 + 单字符代码分档 + 重试（8 文件，+313 / -114 行）

> v3.19.9 (2026-07-20)：双周报流水线全面质量加固（P0~P1 九项）：Stage 1 全空兜底 / owner-map 注入 / 缓存方向数校验 + Stage 3 子方向覆盖重构（max_items → 全覆盖 + ≤3条/子方向 + ~50字精简）+ Stage 2/4b 上下文增强 + brief 优先级排序 + 超时补跑 + key_indicators 端到端贯通（9 文件，+295 行）

> v3.19.8 (2026-07-20)：检测路径全面改进（P0~P2 十四项）：4 处正确性 Bug（死代码 / RANGE_PATTERN 贪婪正则 / 字符串表达式未赋值 / 缺失异常处理）+ 6 处设计缺陷（代码正则补全 / 路径归一化双端一致 / 泛型类型修正 / 槽位效率去重 / 参数签名化）+ +79 测试新建（`test_text_detector.py` 40 用例 + detector/deep_eval/source_locator 补全，总量 1,753）

> v3.19.7 (2026-07-19)：全面质量加固（P0~P2 七项）：`_wiki.py` 5 处静默异常补日志、embedding 向量 LRU 缓存（128/600s）、`corrector.py` 拆分（834→712 行，`_clipboard_io.py` + `_text_detector.py`）、LLM `_CircuitBreaker` 熔断器、新增 109 个单元测试（biweekly helpers 48 + graph engine 26 + config models 35，总量 1,674）

> v3.19.6 (2026-07-19)：ASR 校正引擎加固：`max_mappings` 上限扩展 990→2000 并配置化至 `asr_profiles.json`、替换词典热加载（`_check_dict_reload`，无需重启进程）、手动热词合并机制（`data/asr_manual_hotwords.txt`）

> v3.19.5 (2026-07-19)：全面质量加固：双周报 Stage 4 拆分（4a 纯组装 + 4b LLM 审查）、`_TEAM_OKR_PATTERN` 配置化（`dept_op_keyword` + `team_okr_patterns`）、Stage 3 子方向顺序后置校验、ASR 音近推断示例动态化、`generate_misreadings` 超时修复、新增 30 个测试用例（407 通过）。

> v3.19.4 (2026-07-19)：双周报生成逻辑优化：修复 `load_op_document()` 误取个人 OKR、Stage 0a 支持 `### KR1：` 格式、Stage 3 Prompt 重写（方向标题精简化 / ≤4条 / 来源按时间最新 / 严格 KR 顺序）、`_warn_unresolved_placeholders` 误报修复。

> v3.19.3 (2026-07-19)：交互体验：`build-asr-prompt` 三阶段实时进度输出。新增 `_progress.py` 线程安全进度追踪器，Phase 2（误识别生成）补齐逐批进度（此前完全静默），Phase 级耗时和总耗时汇总，Phase 3 标签修正（去"LLM"误导）。产品版本 3.19.2 → 3.19.3。

> v3.19.2：ASR Phase 1 基础设施：`_AhoCorasick.list_patterns()` 模式枚举 API、`extract_llm_discoveries()` LLM 发现提取、daily-start 集成 ASR 覆盖审计（`_daily_asr_audit` 零 LLM 成本）、`extract_mappings_from_corrections` 修复 `[LLM]` 前缀解析 bug、运行日志中模式计数修复。

> v3.19.1：ASR 代码质量加固：JSONL 反馈格式统一（`llm_time_ms` 写入/加载路径一致）、热词去重修正（移除去重前截断）、死代码清理（移除 `build_optimize_prompt` 等 V2 残留）、剪贴板等待策略改进（固定 delay → 基线+轮询）、coverage.py 常量复用。

> v3.19.0：ASR 实时校正引擎：`iris-asr-corrector` 常驻守护进程，剪贴板监听 vocotype ASR 输出，Aho-Corasick 替换词典 + LLM 编辑助手双重校正，自动反馈数据采集。新增 `asr-audit`（覆盖分析）和 `asr-report`（手动纠错）命令。Prompt 生成改为 Python 模板直渲染（V3 编辑助手）。deepseek-v4-flash 推理关闭（`thinking: disabled`）。高危映射自动过滤。Prompt 热加载支持。

> v3.18.9：代码质量加固：内存系统 FileLock（并发安全）+ 向量索引模型追踪 + `.env` 行尾注释剥离 + Stage2 `max_tokens` 控制 + lark-cli fallback + Wiki 证据阈值配置化。

> v3.18.8：PersonEnricher 飞书 API 频率限制修复：预先过滤已丰富页面 + 自适应批间延迟 + 批次大小调低。
> v3.18.7：CI/CD 基础设施（Makefile/CI/pre-commit/Dockerfile）+ 测试分层重组（unit/integration，1,467→1,513，覆盖率 60.42%）+ Wiki 模块重构（graph.py 751→215 行、_graph_engine.py 独立、ASR 子包 `wiki/asr/` 物理隔离 + `_types.py` 消除循环导入）。
> v3.18.6：开源脱敏补充清理。
> v3.18.5：新增 `iris-daily-start` Skill + 更新 adv_model 降级链。

> 覆盖范围：仅统计 Iris 自身经 provider 发出的 LLM 调用（CLI + 调用 CLI 的 Skill），不含 Claude Code 本体 / Whisper 转写 / 飞书接口。

> 完整版本历史（v3.12.x 及更早）见 [CHANGELOG.md](CHANGELOG.md)。
