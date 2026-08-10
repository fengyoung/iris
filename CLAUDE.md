# Iris 3.23.1 — 项目执行说明

> 工作知识助手，个人知识库（Obsidian Wiki）+ 飞书团队知识库集成。
> 完整版本历史见 [CHANGELOG.md](CHANGELOG.md)。

---

## 项目概览

### 当前规模

~35,000 行 / 166 文件 / 26 模块 · CLI 65 命令 · 单元测试 2,709（138 文件）· 覆盖率 62%+ · 10 个项目级 Skill · Wiki 222 页 · 知识图谱节点 220 / 关系边 1,858（wikilink 1,225 + LLM 633） · 数据源 822 文档 / 5,939 Chunk（text-embedding-v3 / 1,024 维）

**近期新增能力**：实时会议助理（`assistant/`，逐段提炼要点/风险/决策点 + 实时提示关键提问 + 过程文档）· YAML frontmatter 标准化注入（`core/frontmatter.py`）· 批量 frontmatter 补全（`core/frontmatter_batch.py`，正则+LLM+wikilink+备份恢复）· wikilink 自动注入引擎（`wiki/wikilink_injector.py`，零 LLM 成本）· LLM 用量追踪（SQLite WAL + embedding 纳入）· LLM 响应缓存 + embedding 向量缓存（LRU + TTL）· LLM 熔断器（`_CircuitBreaker`，threshold=5 / reset 60s）· 记忆自动更新引擎（`memory_updater.py` + `session_miner.py`，双通道架构）· 多 Agent 并发安全（FileLock + SQLite WAL + Agent 隔离）· ASR 实时校正引擎（Aho-Corasick + LLM 编辑助手 + 反馈反向优化）· CI/CD（Makefile / pre-commit / GitHub Actions）+ pip-audit · constraints.txt 可复现构建

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
| **产品版本** | `pyproject.toml` | 3.23.1 | 软件发布版本 |
| **协议版本** | `src/iris/__init__.py` | 3.16 | CLI 命令集 / agent-spec 格式 |
| **数据版本** | `config/*.json` | 3.3/3.5 | 配置文件 Schema |

> 只有真正发生变化的层才递增版本号。

---

## 技术栈

Python 3.9+ · OpenAI 兼容 LLM API（DeepSeek / 百炼 / Qwen）· Pydantic v2（配置校验）· lark-cli（飞书接口层）· PyMuPDF / python-docx（文档处理）· macOS Keychain（可选密钥存储）。

---

## 项目结构

```
iris3/
├── src/iris/          # 26 模块（见下）
├── scripts/           # CLI 入口 + 委托脚本
├── templates/         # Prompt / Wiki 模板
├── tests/             # 2,709 用例，138 文件
│   ├── unit/          #   纯逻辑单元测试（1,360 用例，<10s）
│   └── integration/   #   集成测试（237 用例）
├── config/            # *.json gitignored，*.example 版本控制
├── data/              # 运行时数据（全 gitignore）
├── .claude/skills/    # 项目级 Skill（10 个）
├── .github/workflows/ # CI 流水线（Python 3.9-3.12）
├── memory/            # Claude 工作记忆
├── Makefile           # 常用开发命令
└── pyproject.toml · README · CLAUDE · CHANGELOG.md
```

**src/iris 模块**：`config`（加载+Pydantic 校验）· `llm`（Provider/路由/LLMService/用量统计）· `core`（类型/锁/写保护/存储/Agent 适配/共享线程池）· `memory`（记忆 6 子模块：含 `session_miner.py` 会话模式挖掘）· `qa`（检索问答+图谱注入+`memory_updater.py` 双通道记忆提取）· `ingest`（扫描/切块）· `retrieval`（BM25+向量+RRF+BM25缓存）· `wiki`（Wiki 体系 + backlink/graph + ASR 校正引擎，最大模块；`wiki/asr/` 含 corrector/coverage/feedback/prompt_optimizer/_progress 等 10 个子模块）· `feed`（信息汇聚管道：飞书聊天记录→话题检测→简报生成，11 文件 / 9 命令）· `analysis`（报告/思维导图）· `evaluation`（Wiki 深度评估 + 引用解析）· `complex_input`（多模态三阶段：图片/PDF/DOCX/VIDEO）· `output`（格式化+DOCX）· `assistant`（实时会议助理：剪贴板采集+校正+检索+逐段分析+面板/文档，9 文件）· `app/cli`（65 命令）· `app/transcribe_meeting`（会议转录）· `feishu`（文档/聊天提炼）· `utils`（含 paths.py / shared.py）· `trello`（看板）。

---

## Claude Code Skill（10 个项目级）

`iris-daily-start`（每日启动维护）· `iris-wiki`（发现→审核→生成）· `iris-feishu-import`（飞书文档/聊天导入）· `iris-feed`（信息汇聚：飞书群聊话题检测→简报生成）· `iris-meeting`（转写→纪要→归档）· `iris-ask`（问答）· `iris-process`（富媒体处理：图片/PDF/DOCX/视频）· `iris-report`（分析报告/思维导图/双周报）· `iris-health`（质量巡检）· `iris-okr-check`（OKR 双周逐项检查）。

---

## 近期变更

**当前 v3.23.1 (2026-08-10)** — 遗留修复 + 使用指南（4 文件 / +311）：① asr-corrector Ctrl+C 修复 — Python 3.13 默认 SIGINT 处理无法中断 time.sleep（3.23.0 会议助理开发中发现并已修复，asr-corrector 的 run_forever 存在同款问题）→ run_forever 显式注册 SIGINT handler；真机验证：SIGINT → 「校正引擎已停止」→ pid 清理；② scripts/verify_hotkey_inject.py 纳入版本控制（CGEventPost 注入验证工具，端到端测试/复现 vocotype 按住说话，须 --keycode 61 右 Option，含「纯修饰键热键注入左 Option 无反应」实测提示）；③ 使用指南 docs/meeting-live-assistant-usage.md（按 asr-corrector-usage.md 惯例：快速开始/命令/前置条件/配置/工作链路/面板/文档/FAQ），README 补链接。验证：ASR 相关 194 个 + 全量 2,708 通过（1 个 feed 既有失败与本次无关）。协议版本 3.16（不变）。产品版本 3.23.0→3.23.1。

**v3.23.0 (2026-08-10)** — 实时会议助理 `iris meeting-live-assistant`（23 文件 / +2,080）：① 背景 — 会议中语音转瞬即逝，需要「会议当下」的实时助理：逐段转写→校正→结合知识库分析→提示关键提问，过程实时写入 Markdown 文档（会后直接拿到完整记录）；与 transcribe-meeting（事后批量）互补，与 asr-corrector 运行时互斥（独占剪贴板）；② 改动 — 新模块 `src/iris/assistant/`（9 文件）：剪贴板采集（复用 corrector 特征判定）→ 词典 fast 校正立即显示 + LLM deep 校正与知识库检索并行（ThreadPoolExecutor(2)，10s 等待窗各自降级）→ LLM 结构化分析（要点/风险/问题/决策点/建议提问，15s deadline 降级）→ 终端面板（ANSI 整帧）+ 过程文档（tmp+os.replace 原子重写，`--output` > `assistant.output_dir` > `data/meeting-live/`）；积压丢弃状态机（处理中 submit 覆盖旧段）；`_probe_running` 只读互斥探测（无副作用）；③ 真机修复 2 个 bug — `resolve_data_path('data')` 无子路径被拒改用 `get_project_root()/data`；Python 3.13 SIGINT 无法中断 time.sleep（Ctrl+C 失效）→ 显式 `signal.signal(SIGINT, raise KeyboardInterrupt)`；④ 测试 +63（unit 56：models 11/session 9/clipboard 6/analyzer 11/doc_writer 10/live 9；integration 7 端到端）。验证：新增 63 全过，unit 全量 1,360（1 个 feed 既有失败与本次无关），integration 全量 237 全过；真机冒烟：启动→SIGINT 优雅退出（统计帧+pid 清理+文档保留），asr-corrector 在跑时让位。协议版本 3.15→3.16（新增命令）。产品版本 3.22.5→3.23.0。

**v3.22.5 (2026-08-10)** — ASR 校正引擎热键门控修复（2 文件 / +215 -4）：① 背景 — 用户按住热键让 vocotype 输入 1 分多钟语音，转写写入剪贴板后被「不在监听窗口」跳过（held=False, released_at=0.0）；② 根因双叠 — CGEventTap 启动失败（辅助功能权限缺失）时只警告未置空监听器，`_tick` 门控按配置 mask 而非监听器可用性判定 → `in_listen_window` 恒 False 全部跳过；且固定 3s 监听窗口装不下长语音的转写耗时；③ 改动 — `run_forever` start 失败置空 `_hotkey_monitor` 降级为内容特征判定（`_is_asr_text` + 富文本检查兜底）；`_HotkeyMonitor` 记录按下时刻暴露 `hold_duration`，监听窗口 = `max(3s, min(按住时长, 120s))`，1 分钟语音释放后 60s 内剪贴板变化仍处理；④ 测试 +13（`test_asr_corrector.py` 3 个新测试类）。验证：ASR 相关 183 个 + 单元测试 1,304 全通过（unit 1,291→1,304）。协议版本 3.15（不变）。产品版本 3.22.4→3.22.5。

**v3.22.4 (2026-08-10)** — 周报提取主题日期不一致自动标注（2 文件 / +60 行）：① 背景 — 提取 W32 成员周报发现李嘉晨 08-07 发送邮件主题仍写「20260731」（复制上周标题未改日期），归档后易误读为错误周期；② 改动 — `scripts/extract_weekly_reports.py` 新增 `_subject_date_mismatch_note` 静态方法（正则提取主题 `YYYYMMDD`/`YYYY-MM-DD` 日期，兼容时区，与发送日期比较），`generate_content` 邮件信息栏不一致时自动加注「⚠️ 主题日期与发送日期不一致」，一致或无日期不加注（零噪音）；③ 测试 +4（`test_weekly_report_extract.py` 12→16 用例）。验证：全量 2,630→2,633 测试全通过（unit 1,291 / integration 230 / 根目录 1,112）。协议版本 3.15（不变）。产品版本 3.22.3→3.22.4。

**v3.22.3 (2026-08-07)** — 知识库全面体检修复（5 文件 / +152 -6）：① 检索索引死数据修复 — `chunker.py` 全量重建时误执行「保留未变更旧 chunk」分支，把已删除/已归档迁移文档的旧 chunk 全部加回（实测 4,636 死 chunk 占 45.1%），修复后重建：chunk 10,290→5,939、向量 10,321→5,939、覆盖率 201.7%→100%；② 知识图谱 LLM 边清零修复 — `graph.py` 增量刷新只保留本次重提取页面的 LLM 边（8/3 提取 592 条被一次增量刷新清零），修复后从 relations 缓存零成本恢复 591 条，增量刷新验证 591→633 不再丢；③ deep-eval CLI 参数补齐 — `--page-filter`/`--sample-rate` handler 已实现但 argparse 未注册；④ 回归测试 +4（`test_chunker_full_rebuild.py` 2 用例 + `TestGraphLlmEdgePreserve` 2 用例）。验证：单元测试 1,291 全通过。协议版本 3.15（不变）。产品版本 3.22.2→3.22.3。

**v3.22.2 (2026-08-04)** — wikilink 注入残留清理（2 文件 / +7 -34）：① 修正过时注释 — `chat_digest.py` 生成输出注释与 `extract_weekly_reports.py` docstring 移除「wikilink 注入」表述（v3.21.0 收敛后已无实际注入）；② 删除 wiki_root 死参数链 — `chat_digest` `_build_markdown` 参数 + `_resolve_wiki_root_safe` 方法，`extract_weekly_reports` `__init__`/`generate_content` 参数 + main 透传链共 10 处；③ 保留 `_wiki_root` 字段（`_load_wiki_context` 真实用途）。验证：相关测试 52 个通过。协议版本 3.15（不变）。产品版本 3.22.1→3.22.2。

**v3.22.1 (2026-08-03)** — Wiki 发现噪音过滤 + 知识图谱全量重建修复（3 文件 / +76 行）：① Wiki 候选发现增加周报模板噪音过滤（`is_noise_candidate`），过滤「本内容由AI」「💼 本周工作」等固定章节标题噪音，提升候选主题质量；② 知识图谱 `full=True` 全量重建边去重修复 — 旧 LLM 边既参与去重又被下方过滤丢弃，导致每次重建边数退化，修复后去重基准只保留 wikilink 边；③ 测试 +50 行（`TestNoiseCandidateFilter` 参数化测试）。协议版本 3.15（不变）。产品版本 3.22.0→3.22.1。

**v3.22.0 (2026-08-02)** — 合并 0802-alpha → main，开源信息泄露治理全库脱敏（40+ 文件）：① 生产代码 — `IRIS_BOT_USER_ID` 真实 open_id 改环境变量（未配置时跳过飞书推送）；团队名单与 `dept_op_keyword` 默认值清空，改由 app.json 配置驱动（附 null 防御）；② 全库泛化 — 真实人名（11 人）→ 通用占位、`zhuanzhuan.com` 企业邮箱 → `example.com`、真实 OKR/项目名/业务指标（图验技术/拍照3.0/XRay/直检率等）→ 通用词；③ 模板与 Skill — biweekly prompt 真实 OKR 示例、iris-okr-check KR 检索词表、iris-feed dry-run 示例重写；④ DESIGN/CHANGELOG 反向泄露条目二次脱敏；⑤ 测试断言同步更新（61 文件）。合并冲突 1 处（iris-okr-check SKILL.md：归档路径修复 + 人名泛化双保留）。验证：2,612 测试全通过。协议版本 3.15（不变）。产品版本 3.21.1→3.22.0。

**v3.21.1 (2026-08-02)** — SOURCE 归档适配全面修复：① 双周报文件名改为日期前缀（`YYYYMMDD-` 前缀），`resolve_source_archive_path` 正则正确匹配→归档到 `06-我的周报/YYYY/`；② `refresh_meeting_minutes` 非递归查找修复（`find_source_matches`→`rglob`），已归档会议纪要可正确备份/去重；③ `_stage0b_load_style` 风格源文件递归查找修复（`rglob`→扁平→找到 `YYYY/` 下文件）；④ feed 简报生成使用 `resolve_source_archive_path` 替代硬编码 YYYYMM；⑤ 移除 `transcribe_meeting/pipeline.py` 两个扁平路径死函数；⑥ 5 个 Skill 文档 SOURCE 路径更新（iris-okr-check / iris-feed / iris-report / iris-feishu-import / iris-meeting 共 18 处）。附加：`extract_weekly_reports.py` `_resolve_output_dir` 文档一致性注释。协议版本 3.15（不变）。产品版本 3.21.0→3.21.1。

**v3.21.0 (2026-08-02)** — SOURCE 元数据工程：① 新增 `frontmatter-batch` 批量补全命令（新模块 `core/frontmatter_batch.py` ~610 行 — 正则快速通道零 LLM 成本 + LLM 深度通道按 9 类目录字段映射 + wikilink 可选注入 + 自动备份/一键恢复 + 幂等跳过）；② wikilink 注入收敛 — 从 4 个管道（doc-convert/chat-digest/transcribe-meeting/extract-weekly-reports）移除，统一由 frontmatter-batch 按需注入；③ 周报按月归档 — `extract-weekly-reports` 输出自动归入 YYYYMM 月份子目录；④ 双周报 frontmatter 注入（title/date/type/period/author）+ analysis `period` 字段。测试 +65（2,626 用例 / 134 文件）。协议版本 3.14→3.15（新增 frontmatter-batch 命令）。产品版本 3.20.2→3.21.0。

**v3.20.2 (2026-07-30)** — 双线合并：① SOURCE 文档质量系统性提升 — YAML frontmatter 标准化（新增 `core/frontmatter.py` 统一工具模块，4 个 CLI 管道输出全部注入 frontmatter 元数据）、wikilink 自动注入引擎（新增 `wiki/wikilink_injector.py`，零 LLM 成本，4 管道集成）、成员周报 Prompt 增强（4 段落结构 + 量化指标要求 + 项目上下文注入）、周报质量门禁（`check_quality`，三级判定，不阻塞写入），测试 +54；② 测试覆盖率系统提升 — 新增 253 个单元测试（9 个新文件），覆盖率 59.87% → 62.82%，覆盖 chunker/formatter/hotwords/lifecycle/embedder/memory_updater/session_miner/feishu_bridge/agent_adapter 等模块。协议版本 3.14（不变）。产品版本 3.20.1→3.20.2。

**v3.20.1 (2026-07-30)** — deep_eval 配置路径改进：chunk 摘要文件路径由硬编码 `main_source_chunk_summary.json` 改为根据 `config.data_source.default_source` 动态加载，便于多数据源切换。协议版本 3.14（不变）。产品版本 3.20.0→3.20.1。

**v3.20.0 (2026-07-30)** — iris-feed 文档提取（Step 5）：新模块 `_doc_extractor.py`（199 行），从话题消息中自动收集飞书文档链接（docx/wiki/sheet/base），调用 `FeishuDocConverter` 转换为本地 Markdown 并关联到简报，支持跨次排重。配置项 `extract_docs` / `doc_extract_max`，新增 CLI 参数 `--no-extract-docs`。测试 +37（2,254 用例）。协议版本 3.13→3.14（新增 `--no-extract-docs` 参数）。产品版本 3.19.26→3.20.0。

**v3.19.26 (2026-07-29)** — 检索与知识库时效性四项优化：① chunk 切块重叠（`chunk_overlap_chars` 默认 150，跨段落承接信息不再丢失）；② Wiki `source_fingerprint` 源文档指纹追踪（frontmatter 记录引用源 hash，过时判定从「按天数猜」变「源文档变化精准触发」，天数阈值降为无指纹旧页面的兜底）；③ 向量索引 embedder 模型不匹配硬失败 + `--force-rebuild` 全量重建参数（此前仅 warning 且提示的参数不存在，旧向量带病混用）；④ 主动提醒引擎 `reminders` 命令 + daily-start 集成（栏目断供/成员周报缺失/项目停滞三类信号，零 LLM 成本，阈值 `app.json.reminders` 可配）。顺带修复：`_chunk_document` 生成器 return 值被丢弃导致 PDF 文档 0 chunk、`write_hash_index` 已有条目 hash 永不更新、`AppConfig` 未声明 `retrieval`/`organization` 字段导致 app.json 中 RRF 权重配置从未生效。协议版本 3.12→3.13（新增 reminders 命令）。测试 +54。

**v3.19.25 (2026-07-28)** — iris-feed 简报质量跃升：两阶段 LLM 架构（Phase 1 检测+合并+OKR / Phase 2 逐话题并发深度摘要），去掉消息截断利用 1M 上下文，Prompt 重写提升讨论要点/引述/决策结构化输出，`_extract_json` 嵌套数组误提取 bug 修复，简报模板编号修正+引述合并兜底。合并 0728-beta：输入截断保护 + `_fill_fallback_summary` 兜底 + 移除 `_llm_detect`/`_parse_llm_response` 死代码。测试 2,154→2,162。协议版本 3.12。7 文件 +725/-268 行。

**v3.19.24 (2026-07-28)** — 全量代码质量加固（第二轮）：P0 项 Dockerfile 修复/CI 安全门禁/硬编码路径消除 3 项 + P1 项 feed 测试补齐 +177 用例/SecretStr API 密钥保护/pre-commit 工具链升级（ruff v0.11 + 8 基础钩子）/静默异常修复 4 项 + P2 项 logger.exception 替换/导入风格统一/CI 覆盖率合并 3 项。测试 1,977→2,154。协议版本 3.12。16 文件。

> 历史版本摘要（v3.12.x ~ v3.19.10）见 [CHANGELOG.md](CHANGELOG.md)。
