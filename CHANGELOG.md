# Iris 变更历史

> 产品版本（pyproject.toml）完整变更记录。协议版本和数据版本独立演进，仅当真正变化时才在此记录。

---

## v3.11.8 (2026-07-08)

### build-asr-prompt 性能与质量优化（6 项）

**性能（LLM 调用并发化）：**
- `LLMHotwordExtractor.extract`（Phase 1）：分批热词提取由串行改 `ThreadPoolExecutor`（≤6 并发）；跨批去重下沉到末尾统一处理，各批用独立局部去重集，消除共享 `seen` 竞争，`executor.map` 保持批次顺序
- `TermExtractor.generate_misreadings`（Phase 2）：分批误识别生成改并发（≤8）；各批只回填自身 `AsrTerm.mis_asr`，天然无竞争
- 效果：`all` 模式墙钟从「批数×单批耗时」降为「单批耗时×⌈批数/并发度⌉」，与 v3.11.6 wiki 并发化同量级

**质量（Phase 3 校正 Prompt 强化）：**
- `LLMPromptOptimizer.build_optimize_prompt`：强化「校正策略」指令，要求覆盖 6 类 ASR 典型错误模式（词典优先/人名同音消歧/中英混排缩写/专名边界/数字版本号/分词纠错），每维度带「判断依据 + 领域实例」
- 篇幅约束 1200→1500~2200 汉字，`max_tokens` 4096→6144；实测校正 prompt 从 ~776 字增至 ~2960 字，策略段占比 80%

**修复与清理：**
- `--asr-mode prompt` 现也提取热词供优化器使用（原先仅 `all`/`hotwords` 提取，`prompt` 模式热词为空）
- `_exceeds_char_limit` 上提到 `utils/tokenization.py:exceeds_char_limit`，消除 `asr_hotwords`/`asr_formatter` 双份定义
- 移除 handler Phase 3 不可达的 `if not terms:` 重提取分支（死代码）
- 修正 `_clean_text_term` strip 字符集里误入的 `\"` 转义笔误，补齐中英直/弯引号

## v3.11.7 (2026-07-07)

### analysis/service.py 职责拆分重构 + 测试补全

**重构（零行为变更）：**
- 新建 `_biweekly_collector.py`（252行）：文件收集/OP文档加载/历史双周报加载，不依赖 LLM，可独立测试
- 新建 `_biweekly_cache.py`（161行）：所有 Stage 磁盘缓存读写（op_directions/stage1_filter/style_guide/file_briefs）
- 新建 `_biweekly_types.py`（36行）：Stage 间 TypedDict 数据契约（FileEntry/FileBrief）
- `service.py` 精简：Stage 0a~4 改为委托 `self._collector` 和 `self._cache`，保留全部向后兼容方法
- `AnalysisReportService` 所有静态方法保持向后兼容，外部调用方无需修改

**测试补全（+38 用例）：**
- `test_biweekly_cache.py`（21用例）：命中/失效/损坏/清理逻辑全覆盖
- `test_biweekly_collector.py`（17用例）：文件收集/日期过滤/成员去重/OP加载/历史报告/自定义dir_map

**测试：** 315 passed（277 + 38）

---

## v3.11.6 (2026-07-07)

### 全项目深度优化（第二轮，19项）

**P0 — 功能性 Bug：**
- **feishu/doc_convert.py**：图片下载路径安全检查，防止路径逃逸出 img_dir（深度防御）
- **feishu/client.py + _shared.py**：`fromtimestamp` 统一加 `tz=timezone.utc`，消除文档创建日期时区偏差
- **transcribe_meeting/pipeline.py**：`_call_llm` 包 `LLMProviderError`，LLM 不可用时返回含原始转写的 fallback 而非 crash
- **wiki/generator.py**：删除 `_parse_frontmatter_field` 中 `return` 后的死代码（行 310）

**P1 — 静默失效/性能：**
- **feishu/chat_digest.py**：待办表格字段含 `|` 时正确合并为事项列；排重索引实例级缓存，消除批量处理时的重复磁盘读取
- **wiki/generator.py**：`update_all_pages` 改为 `ThreadPoolExecutor(max_workers=6)` 并发执行，91页从~4.5分钟压缩至单页最大耗时
- **feishu/doc_convert.py**：写入异常从 `except Exception` 改为先检查 `content` 字段再 `except OSError`，消除误导性错误信息
- **core/storage.py**：`load_all` 的 `ImportError` 从静默丢弃改为记录 warning，区分"库为空"和"导入失败"
- **config/loader.py**：Pydantic `ValidationError` 单独处理，格式化 `.errors()` 字段级错误替代宽泛异常包装

**P2 — 设计改进：**
- **core/agent_adapter.py**：`_invoke_wiki_lint` 在 wiki 配置缺失时提前返回错误，不再创建指向当前目录的空 Path
- **memory/lifecycle.py**：`_merge_corrections` 时间相等时新值优先（`>=` 替代 `>`），允许重新导入纠正错误记录

**P2 — 测试覆盖（+58 用例）：**
新增 8 个测试文件，覆盖原先零覆盖的核心模块：
  - `test_transcribe_meeting.py`：duration/date/footer 计算 + LLM 失败 fallback（14 用例）
  - `test_feishu_chat_digest.py`：classify/表格转义/排重缓存（9 用例）
  - `test_feishu_doc_convert.py`：路由/扩展名/标题插入（8 用例）
  - `test_storage.py`：ChunkStore CRUD/load_all（8 用例）
  - `test_output_formatter.py`：各命令格式化/边界（6 用例）
  - `test_cli_helpers.py`：emit/路径/status（5 用例）
  - `test_wiki_generator.py`：update_all_pages/write_page（4 用例）
  - `test_qa_service.py`：local/llm 模式/降级（4 用例）

**P3 — 代码清理：**
- **llm/provider.py**：提取 `_dispatch_provider_call` 方法，消除 `force_model` 分支与 `_try_call` 闭包的 ~60 行重复 provider 分发逻辑

**测试：** 277 passed（219 原有 + 58 新增）

---

## v3.11.5 (2026-07-07)

### 深度代码审查优化（12 项）

**P0 — 功能性 Bug 修复：**
- **Stage 1 preview 注入**：`_stage1_filter_files` 中 `preview` 变量计算后未注入 `file_inventory`，LLM 方向分类时看不到文件内容，导致分类准确性下降；现已注入为每行末尾摘要字段
- **ModelManagerError 捕获**：`_build_fallback_chain` else 分支的 `ModelManagerError` 不是 `LLMProviderError` 子类，逃逸所有捕获导致裸 traceback；现已包装为 `LLMProviderError`
- **UTC 时间窗口**：`build_biweekly_report` 用 UTC 时间计算 lookback 窗口再剥 tzinfo，UTC+8 下边界日文件系统性丢失；改为全程使用本地时间

**P1 — 静默失效修复：**
- **reasoning_content fallback**：content 为非空 list 但无 text 条目时 `not content` 为 False，跳过 reasoning fallback 直接抛错；移除冗余条件
- **quality_score 缺失**：审查结果缺少 `quality_score` 字段时被误判为"通过"（默认值 5 ≥ 4）；改为单独处理并记录 warning
- **OP 缓存哈希窗口**：`_stage0a_parse_op` 用前 500 字节哈希，文档中后段新增方向不感知；扩展为全文哈希
- **OP 文档静默失败**：找不到 OP 文档时静默返回 `""`；补充 warning 日志

**P2 — 配置脆弱性：**
- **`_DIR_MAP` 配置化**：硬编码物理路径（如 `07-成员周报`）与 `data_source.json` 脱节；保留默认值同时支持 `app.json biweekly_report.dir_map` 覆盖，目录不存在时输出 warning

**P3 — 代码质量：**
- **提取 `resolve_source_root`**：新建 `src/iris/utils/paths.py`，`service.py` 和 `handlers.py` 中的重复实现统一导入
- **合并 JSONL 加载函数**：`_load_batch_items` / `_load_review_items` 80% 代码重复，提取为 `_load_wiki_items_from_jsonl(path, only_selected=False)` 后保留向后兼容包装
- **提取时间戳解析**：`lifecycle.py` `age()` / `list_stale()` 的重复时间戳判断逻辑提取为 `_is_item_stale(item, cutoff)` 静态方法

**测试：** 219 passed（全量回归，无新增/删除）

---

## v3.11.4 (2026-07-07)

### build-biweekly-report 流水线修复

**问题修复（7 项）：**
- **跨方向 brief 路由**：修复 `primary_direction` 独占路由导致跨方向内容遗漏（如团队成员A周报的 某检测项目/某品牌项目 进展仅路由到方向二）
- **Stage 1 LLM 非确定性**：新增 `stage1_filter.json` 缓存（按文件清单 hash + 方向 hash），消除 LLM 随机性导致的文件过滤不一致
- **low 文件静默丢弃**：Stage 2 摘要范围从 high+medium 扩展到 high+medium+low，防止 LLM 保守判定导致内容缺失
- **历史去重文本泄露**：修复 Stage 3 LLM 将历史去重参考文本当作本期素材的问题，增加隔离警告
- **方向二/三"无显著进展"**：放宽全子领域覆盖规则，允许按内容主题自行聚类
- **base_model 统一**：Stage 3/4 的 `complexity` 从 `"complex"` 改为 `"standard"`，全链路使用 base_model
- **双重文件读取**：`_collect_recent_files` frontmatter fallback 时合并为单次读取

**Prompt 优化：**
- Stage 3 新增规则 12：禁止「某某决策/明确/认为」等过程性表述，转为客观事实
- Stage 3 新增规则 13：同项目进展聚合为加粗标题 + 子 bullet 展开
- Stage 1 增加包容性规则：疑惑时归 medium 而非 low/none
- Stage 3 增加战略分析段撰写指南（四层次：态势→突破→风险→指向）

**测试覆盖：**
- 新增 52 个单元测试（`test_biweekly_files.py` 18 + `test_biweekly_dedup.py` 13 + `test_biweekly_pipeline.py` 21）
- 全量测试 219 passed

**配置增强：**
- `biweekly_report.data_sources`：可配置扫描目录
- `biweekly_report.lookback_days`：可配置时间窗口（默认 14）
- `biweekly_report.dedup_window_days`：可配置去重窗口（默认 35）
- `--dry-run`：预览模式（文件清单 + OP 方向，不调用 LLM）

## v3.11.3 (2026-07-05)

### build-biweekly-report 全面重构

- **管道重构**：从「OP 关键词 → chunk 检索 → 时效性加权」改为「时间窗口文件扫描 → LLM 直接理解」，不再遗漏关键词不匹配的文档
- **证据源**：直接扫描 SOURCE 中 03-方案报告/04-讨论思考/05-会议纪要/07-成员周报 近两周文件，43 个完整文件直接喂给 LLM
- **混合检索**：纯 BM25 → EnhancedRetriever（BM25 + 向量 RRF 融合）+ 时效性加权
- **格式参考**：新增上期双周报加载，作为 LLM 输出格式与战略分析深度的标杆
- **引用格式**：简化可读，支持类型前缀 + 完整描述 + MMDD（如 `项目讨论-某检测项目拆修检测...-0702`）
- **反幻觉**：引用标签严格来自文件清单，禁止编造
- **架构清理**：删除废弃方法 `_retrieve_recent_evidence`, `_extract_op_keywords`, `_load_wiki_for_report`；OP 文档缓存；handler 解耦
- **默认模式**：`build-biweekly-report` 默认走 `llm` 模式，不再 fallthrough 到 `local`

---

## v3.11.2 (2026-07-03)

### force_model 参数 + 历史纪要翻新

- LLM Provider 新增 `force_model` 参数，支持跳过路由规则直接指定模型
- `TranscribeMeetingPipeline` 新增 `model` 参数，可指定纪要生成模型
- `scripts/refresh_meeting_minutes.py` — 独立翻新脚本，支持 dry-run/resume/verify
- 使用 deepseek-v4-pro 重新提取全部 111 份历史转录文件纪要
- 人物歧义处理：6 人手动排歧 + 杜鹏→杜朋飞书信息统一

---

## v3.11.1 (2026-07-03)

### transcribe-meeting 三项修复

| # | 问题 | 修复 | 方法 |
|:---:|------|------|------|
| 1 | 日期错误 — header 和尾注都使用生成日期而非会议日期 | 从文件名提取会议日期 | `_format_meeting_date()` |
| 2 | 缺少时长 — 纪要无时长字段 | 从转写时间戳计算首尾时差 | `_calc_duration()` |
| 3 | 尾注缺失 — 12/40 文件无生成说明 | LLM 输出后自动追加 | `_ensure_footer()` |

---

## v3.11.0 (2026-07-03)

### Claude Code 项目级 Skill 体系

新增 6 个 Skill（`.claude/skills/`）：`iris-wiki`、`iris-feishu-import`、`iris-meeting`、`iris-ask`、`iris-report`、`iris-health`。

设计原则：决策密度高、多轮交互、错误易发的命令优先 Skill 化。公式：Skill = CLI + Claude 对话引导。

---

## v3.10.2 (2026-07-02)

### feishu-doc-convert 改进

- 文件名使用飞书创建时间：`{date}-{title}-from{author}`
- 新增 `search_doc_meta` 获取文档创建时间和作者
- 元信息 fallback：`wiki +node-get` → `docs +search`
- `extract_date` 新增 Unix 时间戳支持
- 路由关键词调整：03-方案报告增加「规划」，08-参考资料移除「外部」

---

## v3.10.1 (2026-07-02)

产品版本 `3.10.0` → `3.10.1`，仅版本号更新。协议版本（3.8）和数据版本不变。

---

## v3.10.0 (2026-07-01)

### 全面代码优化 + 新模块

基于多轮并行审查（7 提交，46 文件，+2171/-527）。

**致命修复（4 项）：**
- API Key 全系统明文传播 → `sensitive=False` 默认脱敏
- Protocol 与 LLM Provider 签名不一致 → 统一签名
- Trello DNS monkey-patch 全局状态污染 → URL 重写 + Host header
- Keychain set_secret 先删后加数据丢失 → 移除前置 delete

**架构重构（4 项）：**
- LLM Provider 双接口统一 + `_fallback_loop` 消除 85% 重复代码
- `term_extractor.py` 1,298→556 行，拆出 4 个 ASR 子模块
- `WikiContextLoader` 统一加载，收敛 5 处独立扫描
- Prompt 模板外部化到 `templates/wiki/`

**新模块：**
- 记忆 5 子模块：`lifecycle.py` / `long_term.py` / `manager.py` / `session.py` / `working.py`
- 输出格式化：`output/formatter.py` + `converters.py`
- 全局常量：`utils/constants.py`

---

## v3.9.0 (2026-06-30)

### 人物 Wiki 飞书通讯录丰富 + 发现规则增强

- 新增 `PersonEnricher` + `enrich-persons` 命令
- 人物发现新增 8 条正则模式（正文动作/转述/结构标记）
- 60+ 非人名排除名单
- `ChunkSlim` 新增 `content` 字段（全文），消除 180 字符截断

---

## v3.8.1 (2026-06-30)

### 多模型路由自动触发

修复 3 个断点：Agent 层 agent-spec 补全 `image` 参数、CLI 层自动从 query 提取路径、Detector 层兜底触发。

---

## v3.8.0 (2026-06-29)

### 复杂输入三阶段重构 + LLMService 统一入口

- 双阶段 → 三阶段流水线（base→指令, adv→理解, base→整合）
- `LLMService` 统一入口消除各模块重复创建 Provider
- 文件类型检测从仅图片扩展为 image/pdf/document/video 多类型

---

## v3.7.0 (2026-06-29)

### iris2 → iris3 能力迁移

- Pydantic v2 配置校验（20+ BaseModel，字段约束 + 自定义校验）
- Wiki 深度评估模块（准确性 + 全面性校验）

---

## v3.6.0 (2026-06-29)

### 全模块深度审查

5 并行 agent 审查（83 文件，14,000+ 行），29 项修复 + 5 项架构重构。核心：
- 6 项 Critical Bug 修复
- `term_extractor.py` 拆分（→ asr_hotwords/prompt_optimizer/formatter/version）
- `utils/constants.py` 和 `utils/llm_parsing.py` 新增

---

## v3.5.0 (2026-06-29)

### build-asr-prompt 三段 LLM Pipeline

- Phase 1：LLM 热词提取（5 批，≤490 条）
- Phase 2：LLM 误识别生成（8 批，12 种拼音混淆模式，≤990 条）
- Phase 3：LLM Prompt 优化（策略指引型，≤1200 汉字）
- 设计原则：替换词典负责确定性映射，Prompt 负责策略指引

---

## v3.4.0 (2026-06-27)

### 代码审查 + 检索质量修复

- 6 Critical Bug（XMind 导出、BM25 重写、向量矩阵缓存等）
- Wiki 常量统一（_constants.py 单一数据源）
- 性能优化（O(n²)→O(n)、热路径 import、Whisper MPS 加速）

---

## v3.3.0 (2026-06)

飞书 → 本地知识库提炼，步骤 3 完成。

---

## v3.2.1 (2026-06)

会议纪要 LLM 动态路由 + 来源标识。

---

## v3.2.0 (2026-06)

步骤 2 完成，Wiki 体系上线。

---

## v3.1.0 (2026-05)

人物页面类型 + 协作网络。

---

## v3.0.0 (2026-05)

项目初始化（从 Iris v2.7.1 重构）。
