# Iris 3.30.0 — 项目执行说明

> 工作知识助手，个人知识库（Obsidian Wiki）+ 飞书团队知识库集成。
> 完整版本历史见 [CHANGELOG.md](CHANGELOG.md)。

---

## 项目概览

**当前规模**：~43,000 行 / 183 个源码文件 / 27 模块 · CLI 67 命令 · 测试 3,261（pytest 全量，含参数化）· 覆盖率 68%（`fail_under` 65）· mypy 基线 193 errors（非阻断）· 10 个项目级 Skill · Wiki 241 页 · 知识图谱节点 220 / 关系边 1,858（wikilink 1,225 + LLM 633）· 数据源 900+ 文档 / 6,771 Chunk（text-embedding-v3 / 1,024 维）

**近期新增能力**：三阶段质量优化（F401/C901 门禁、`IrisError` 统一异常体系、mypy 基线、corrector/live 模块拆分）· 工程可靠性治理（SQLite 生命周期、稳定 inode 文件锁、统一原子写、向量索引 generation 发布、跨进程 LLM 缓存治理）· 任务面板 `taskpanel/`（Web 只读 + TaskReporter 埋点 + 探测兜底 + 常驻守护）· 实时会议助理 `assistant/`（逐段提炼要点/风险/决策点 + 实时提示提问 + 过程文档）· YAML frontmatter 标准化注入（`core/frontmatter.py`）+ 批量补全（`frontmatter_batch.py`，正则+LLM+备份恢复）· wikilink 自动注入引擎（零 LLM 成本）· LLM 用量追踪（SQLite WAL + embedding 纳入）· LLM 响应缓存 + embedding 向量缓存（LRU+TTL）· LLM 熔断器（threshold=5 / reset 60s）· 记忆自动更新引擎（双通道）· 多 Agent 并发安全（FileLock + SQLite WAL + Agent 隔离）· ASR 实时校正引擎（Aho-Corasick + LLM 编辑助手 + 反馈反向优化）· CI/CD（Makefile / pre-commit / GitHub Actions）+ pip-audit · constraints.txt 可复现构建

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
| **产品版本** | `pyproject.toml` | 3.29.1 | 软件发布版本 |
| **协议版本** | `src/iris/__init__.py` | 3.21 | CLI 命令集 / agent-spec 格式 |
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
├── tests/             # 3,261 用例（pytest 全量；conftest 按目录/白名单自动打 unit/integration 标记）
├── config/            # *.json gitignored，*.example 版本控制
├── data/              # 运行时数据（全 gitignore）
├── .claude/skills/    # 项目级 Skill（10 个）
├── .github/workflows/ # CI 流水线（Python 3.11-3.13）
├── memory/            # Claude 工作记忆
└── Makefile · pyproject.toml · README · CLAUDE · CHANGELOG.md
```

**src/iris 模块**：`config`（加载+Pydantic 校验）· `llm`（Provider/路由/LLMService/用量统计）· `core`（类型/锁/写保护/存储/Agent 适配/共享线程池/`exceptions.py` 统一异常基类）· `memory`（记忆 6 子模块：含 `session_miner.py`）· `qa`（检索问答+图谱注入+`memory_updater.py` 双通道记忆提取）· `ingest`（扫描/切块）· `retrieval`（BM25+向量+RRF+BM25缓存）· `wiki`（Wiki 体系 + backlink/graph + ASR 校正引擎，最大模块；`wiki/asr/` 含 corrector/_hotkey/_trie/_diff/_clipboard_io/_text_detector/coverage/feedback/prompt_optimizer 等 14 子模块）· `feed`（飞书聊天记录→话题检测→简报生成，11 文件 / 9 命令）· `analysis`（报告/思维导图）· `evaluation`（Wiki 深度评估 + 引用解析）· `complex_input`（多模态三阶段）· `output`（格式化+DOCX）· `assistant`（实时 AI 会议参谋：本地 ASR+校正+检索+批量分析+话题/说话人+洞察推送+面板/文档，14 文件；`live.py` 编排 + `_audio_capture.py` 合并缓冲 + `_batch_processor.py` 批处理纯逻辑）· `app/cli`（65 命令）· `app/transcribe_meeting`（会议转录）· `feishu`（文档/聊天提炼）· `utils`（paths.py / shared.py）· `trello`（看板）· `taskpanel`（任务埋点 + Web 只读展示 + 常驻守护，7 文件）。

---

## Claude Code Skill（10 个项目级）

`iris-daily-start`（每日启动维护）· `iris-wiki`（发现→审核→生成）· `iris-feishu-import`（飞书文档/聊天导入）· `iris-feed`（群聊话题检测→简报）· `iris-meeting`（转写→纪要→归档）· `iris-ask`（问答）· `iris-process`（图片/PDF/DOCX/视频）· `iris-report`（分析报告/思维导图/双周报）· `iris-health`（质量巡检）· `iris-okr-check`（OKR 双周逐项检查）。

---

## 近期变更

**当前 v3.30.0 (2026-09-03)** — 三阶段质量优化(开源冲刺 → 代码质量 → 长期改进,3 次独立提交 + 1 次 release)。**阶段 1**:测试数据个人用户名泛化(全库残留 0);启用 F401 门禁并清理 395 处未使用导入(130 文件 / -380 行),`__init__.py` 与 `app/cli/handlers.py` facade 例外;修复 ruff 误删的两处 re-export 链(`discovery_utils` 改从 `_constants` 取 `PAGE_TYPE_PRIORITY`);SECURITY.md 补「仓库元数据」节固化 git 历史不重写结论。**阶段 2**:`wiki/asr/corrector.py` 1,542→873 行(拆 `_hotkey.py` CGEventTap 监听/热键解析、`_trie.py` Aho-Corasick、`_diff.py` 词级差异,来源判定并入 `_clipboard_io.py`)、`assistant/live.py` 1,044→871 行(拆 `_audio_capture.py` MergeBuffer 合并缓冲+噪音门控、`_batch_processor.py` 批文本组装/检索去重/分析结果应用/建议判定),旧导入路径全部 re-export 兼容;ruff C901 `max-complexity=20` 门禁,11 个 >20 函数全部重构达标(`_panel._build` 53、`handle_build_asr_prompt` 48、`run_sync` 34、`_tick` 28、`lint_wiki` 27、`_stage1_filter_files` 24、`_apply_extracted` 23、`_diff_changes` 23、`_normalize` 23、`_is_wiki_broken_link` 21、`cmd_run` 21);库层 print→logger(`core/storage.py` FTS5 降级、`feishu/chat_digest.py` 时间范围解析,守护进程/评估进度等用户可见输出保留)。**阶段 3**:新建 `core/exceptions.py`——`IrisError` → `IrisRuntimeError(+RuntimeError)` / `IrisValueError(+ValueError)`,19 个模块异常挂接且原标准库父类保留在 MRO(既有 `except RuntimeError` 不受影响),`StorageError` 迁入并消除 `core/__init__.py` 的 ImportError 回退重复定义;顺带修复异常迁移暴露的循环导入 `config.loader → core/__init__ → write_guard → config.loader`(`write_guard.py` / `utils/logging.py` 的 `ConfigBundle` 改 TYPE_CHECKING);mypy 非阻断基线(`make typecheck`,pre-commit manual 阶段,不接 CI):**193 errors / 53 files**(assistant 58 · wiki 53 · app 22;union-attr 51 · assignment 33 · arg-type 32);新拆模块专项单测 +208(`_audio_capture` / `_batch_processor` / `asr/_diff` / `asr/_hotkey` / `scripts/sync_memory` 后者此前零测试)+ 异常体系 +49。附带发现并修复 `MergeBuffer` 首句 push 产出空 Flush 的语义错误(live 侧曾靠噪音门控吞掉)。验证:ruff 0 告警;pytest 全量 3,261 通过;覆盖率 65.82%→68%(fail_under 55→65);6 个底层模块冷启动 import 无环;`asr-corrector` / `meeting-live-assistant` / `build-asr-prompt --help` 可运行。协议 3.21(不变);app 配置 3.7(不变);产品 3.29.1→**3.30.0**。

**v3.29.1 (2026-09-02)** — Wiki 增量更新指纹预检补全(`src/iris/wiki/generator.py`):修复 daily-start 卡死——`update_all_pages()` 对全部 241 页每页无差别调 LLM(并发 6 路)判断是否需更新,241 次长 prompt 调用压垮 zz_tokenhub 中转(超时 + finish_reason=stop/length 无 content),进程阻塞在网络上 15 分钟无页面落盘。根因:v3.28.4 已实现 `is_wiki_stale()` 指纹预检(source_fingerprint 源文档 hash 全未变→页面新鲜→零 LLM 跳过,discovery/metrics 均已用),但 `update_all_pages` 漏用此路径。修复:加载 chunk_hash_index,每页先 `is_wiki_stale(path, hash_index)` 判定,源文档未变直接返回 no_changes(跳过 LLM),仅过时页面走增量更新;判定异常兜底照常走 LLM(不因预检漏更)。效果:daily-start Wiki 更新从 241 次 LLM 调用降至 ~0 次(源文档无变化场景),全跳过 ~5s。验证:wiki_generator 相关 unit 85 + integration 5 通过、ruff 零告警、真实 daily-start 跑通(exit 0:chunks 6820/vector 6820/graph 241 节点 2115 边/人物 176 无歧义)。协议 3.21(不变);产品 3.29.0→**3.29.1**。

**v3.29.0 (2026-09-01)** — 飞书消息图片理解沉淀(9 文件 / +测试 17):`MessageImageAnalyzer`(`feishu/image_analyzer.py`)下载→多模态 LLM→描述,按 image_key 跨管道共享缓存(data/image_analysis/)+ enabled/max_per_run 控制;`FeishuClient.download_message_image()`(`im +messages-resources-download`,区别于文档图 `docs +media-download`);feed 管道 Step2b 插桩 + `RawMessage.content_for_prompt()` 三级回退(描述>[图片]>原文);chat-digest 注入 `image_descriptions`;配置 `image_understanding:{enabled,max_per_run}`(feed 落 feeds.json#topic_config / chat-digest 落 feishu_ingest.json#chat_digest,默认 true/10)。已知边界:只处理 msg_type==image 独立图,post 内嵌图不单独分析;单张失败降级[图片]占位。端到端:图验先遣队 feed-collect(5 张播报图识别为直检率看板)+ chat-digest 跑通。验证:全量 3,004 通过、ruff 零告警。协议 3.21(不变);产品 3.28.5→**3.29.0**。

**v3.28.5 (2026-09-01)** — LLM 调用统一到 LLMService 单一口径（7 文件 / +56 -69）：消除「已建 LLMService 却又 `get_provider()` 取回底层 provider 绕过响应缓存」的残留路径。① scripts — `extract_travel_invoice.py` / `extract_weekly_reports.py` 改 `LLMService`；② ASR — `hotwords.extract` / `extractor.generate_misreadings` 接口改 `llm: LLMService`；③ 检索 — `LLMQueryPlanner` 参数名 `llm_provider` 实为 provider（命名误导）+ `enhanced.py` 直接传 `LLMService`；④ 命令 — `build-asr-prompt` 改传 `llm_service`。统一适配 `provider.generate(LLMRequest(...))` → `llm.generate(prompt, route_context=...)`。保留（非绕过）：`corrector` 的 `_provider` fallback（测试注入/降级）、`route-model` 的 `ModelRouter`（查询路由）、`get_provider()` 诊断用途、`force_model`。验证：全量 unit 1,910 通过、ruff 零告警。协议 3.21（不变）；app 配置 3.7（不变）；产品 3.28.4→**3.28.5**。

**v3.28.4 (2026-09-01)** — 提醒引擎项目停滞判定兜底（8 文件 / +242 -9，回归测试 +5）：① 三重兜底消除误报 — `project_stalled` 原先只信项目页 source_fingerprint（生成时证据快照可能陈旧），软硬一体/XRay/视频稽查/数据标注平台/售后归因/AI巡检等活跃项目被误报；修复 — 指纹 → SOURCE 同名文档（剥离链/英文 token/连词变体）→ **内容级匹配**（周报正文含项目活动但文件名仅含人名；前缀变体「数据标注平台」→「标注平台」；名单类文档仅提及不算活跃）；② `project_stall_ignore` 配置 — 已完结/已移交/维护期项目不再告警；③ 模板/生成器根因修复 — `[[Wiki-链接]]` 示例文字被 LLM 当真实链接复制进页面（存量 24 文件已清零），改为禁止输出示例占位符。验证：提醒 28 全过、全量 unit 1,606、ruff 零告警。协议 3.21（不变）；app 配置 3.6→**3.7**；产品 3.28.3→**3.28.4**。

**v3.28.3 (2026-09-01)** — 开源前信息安全复审：全库二次脱敏（文档/测试/模板/Skill 中的真实姓名、业务指标与内部项目名泛化），git 历史作者邮箱迁移至 noreply，CI 权限最小化（`contents: read`），`.gitignore` 补 `.env.*`。协议 3.21（不变）；产品 3.28.2→**3.28.3**。

**v3.28.2 (2026-09-01)** — batch-transcribe 批量会议纪要修复（3 文件 / +111，回归测试 +2）：`TranscribeMeetingPipeline.run_batch` 从未实现，批量命令一执行即抛 `AttributeError`；补全实现（按扩展名分流音视频 Whisper 转写/已有转写文本、单文件失败不中断批量、批量层 TaskReporter 埋点）+ handler 补传 `--to-source`（此前批量模式丢失归档能力）。协议 3.21（不变）；产品 3.28.1→**3.28.2**。

**v3.28.1 (2026-08-30)** — 两条修复线合并（14 代码+测试文件 / 回归测试 +26）：① **LLM 思考文本污染修复**（2 文件 / +7）— `_extract_chat_completions_text` 在 `content` 为空时静默回退返回 `reasoning_content`，思考模型（deepseek-v4-flash）max_tokens 耗尽时把思考当最终输出（实测某期双周报 Stage 4b 审查 13k 思考字符写入归档文件）；修复 — content 为空直接抛 `LLMProviderError`（走重试/降级链，绝不产出「伪成功」垃圾文本），Stage 4b 失败回退 Stage 4a 组装稿；② **深度审查批次 1 数据止损**（12 文件 / +19）— P0×6：记忆裁剪方向反转（`lifecycle.summarize()` `[-10:]` 保留最新）、chunker 增量丢 chunk（增量无条件保留）、向量索引增量只增不改不删（按 `document_hash` 重嵌 + 差集清理，ids.json 新增 doc_hashes）、wikilink 注入吞正文（URL 排除全角标点 + 合并区原文切片重建）、feed pending 队列覆盖（新增 `append_pending` 锁内合并 + topic_id 去重）、person_enricher 清空手工 email（空值保留原行 + 备份只写一次）；P1×4：文件日志从未生效（`isinstance(app, dict)` → `hasattr(x,"get")`）、会话挖掘懒触发从未执行（`shared_pool.submit` 不存在 → `get_executor().submit` + 时间戳后置）、建议提问 TypeError 被吞（CONF_ICON+text 渲染入 try）、会议助理埋点目录错位（`data_root` 改传 `_pid_dir`）；附带 `_load_archive_config` 缺配置回退 `.example`。**升级需执行一次 `build-chunks --write-summary` + `build-vector-index --force-rebuild` 清理存量死数据。**验证：全量 2,970 通过，ruff 零告警。协议 3.21（不变）；产品 3.28.0→**3.28.1**。

**v3.28.0 → v3.19.24 主题索引**（完整条目见 [CHANGELOG.md](CHANGELOG.md)）：

v3.28.0 工程可靠性治理（SQLite 生命周期/稳定 inode 锁/统一原子写/向量索引 generation 发布/LLM 缓存治理/IRIS_PROJECT_ROOT）· v3.27.2 LLM 配置修复+新视觉模型默认 · v3.27.1 双周报写作风格固化 · v3.27.0 任务面板 task-panel · v3.26.3 面板稳定化+并发加固 · v3.26.2 面板双主题 · v3.26.1 会议助理全量优化 · v3.26.0 AI 会议参谋 · v3.24.x 会议助理×asr-corrector 优化系列 · v3.23.x 实时会议助理落地系列 · v3.22.5 热键门控 · v3.22.4 周报日期标注 · v3.22.3 知识库体检修复 · v3.22.2 wikilink 残留清理 · v3.22.1 噪音过滤+图谱重建 · v3.22.0 开源信息脱敏 · v3.21.1 SOURCE 归档适配 · v3.21.0 SOURCE 元数据工程 · v3.20.x feed 文档提取/覆盖率提升 · v3.19.24-26 质量加固/feed 质量/检索时效性

> 更早版本摘要（v3.12.x ~ v3.19.10）见 [CHANGELOG.md](CHANGELOG.md)。
