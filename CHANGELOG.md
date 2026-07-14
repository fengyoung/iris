# Iris 变更历史

> 产品版本（pyproject.toml）完整变更记录。协议版本和数据版本独立演进，仅当真正变化时才在此记录。

---

## v3.12.0 (2026-07-14)

### 新功能

**知识图谱（wiki/graph.py）：**
- 三层架构：节点层（frontmatter 全量构建）→ wikilink 边（零 LLM 成本）→ LLM 关系边（增量提取）
- 图查询：`neighbors()`（BFS 多跳）、`related_entities()`（按类型分组）、`find_path()`（最短路径）
- 图分析：`find_orphans()`（零入链节点）、`find_bridges()`（跨类型桥接节点）、`density_report()`（密度/度分布）
- LLM 关系提取：批量提取（负责/使用/属于/…）三元组，分页缓存增量更新
- CLI：`iris build-graph [--full] [--page <title>]`
- 集成：`daily-start` 自动增量刷新 · `wiki-lint` 孤页检测使用 BacklinkBuilder · `ask` LLM 模式注入图谱上下文

**PDF 多模态支持（complex_input/pdf_adapter.py）：**
- PyMuPDF 提取全部页面文字 + 渲染前 5 页为 base64 图片
- Pipeline Stage 2 新增 `_stage2_pdf` 路径：文字+页面图片 → adv_model 多模态理解
- 支持多文件、部分失败继续处理
- 参数可配置：`max_render_pages`、`max_text_chars`、`render_scale`

**DOCX 文字提取（complex_input/docx_adapter.py）：**
- python-docx 提取段落文字 + 表格内容 + 嵌入图片检测
- Pipeline Stage 2 新增 `_stage2_docx` 路径：纯文本提取（不做 LLM 调用）
- 支持标题层级保留（Heading → # 前缀）

**反向引用索引（wiki/backlink.py）：**
- 扫描全部 `[[wikilink]]` 构建 `inbound` / `outbound` / `orphans` 双向映射
- 带缓存机制（`invalidate_cache()`），避免重复全量扫描
- `wiki-lint` 重构为使用 BacklinkBuilder，孤页检测结果一致

### 工程质量

**代码审查修复（14 项）：**
- **Critical**: BacklinkBuilder 添加缓存避免 O(n²) 扫描；`_atomic_write_json` + `now_iso` 提取到 `utils/shared.py` 统一引用
- **High**: `find_path` BFS 改为邻接表索引 O(V+E)；`extract_relations` 边去重；`backlink.save()` 改用原子写入；`_stage2_docx` 修正模型名
- **Medium**: `batch_size` 重命名为 `chunk_size`；删除死代码 `_TYPE_TO_PREFIX`；`total_links` 去重后重新计数；`neighbors()` 改用标准 BFS
- **Low**: 移除重复 `_now_iso()` / `_atomic_write_json` 定义；XML namespace 提取常量

**Pipeline 架构重构：**
- `_stage2_multimodal` 拆分为 4 条独立路径：`_stage2_images` / `_stage2_pdf` / `_stage2_docx` / 跳过
- 新增 `utils/shared.py`（项目级共享工具：原子写入 + 时间戳）

### 测试

- 532 测试（+71）：`test_pdf_adapter.py`（13 用例）、`test_docx_adapter.py`（9 用例）、`test_backlink.py`（12 用例）、`test_graph.py`（18 用例）、`test_complex_input_pipeline.py`（+19 用例 PDF/DOCX/路由）
- 测试覆盖所有新模块的：正常路径、异常路径、边界条件、持久化巡回

### 重构优化

**max_tokens 去硬编码（配置兜底）：**
- `config/llm.json` 每个模型新增 `max_tokens` 字段（deepseek 8192~16384 / qwen 8192），作为输出上限兜底值（数据版本 3.4→**3.5**）
- `provider.py` 中调用方不传 max_tokens 时，自动从模型配置读取
- 移除 5 处分散硬编码（planner 256 / deep_eval 100/120×2 / transcribe_route 300），消除 DeepSeek reasoning 吃掉 token 预算导致的输出截断问题

### 项目指标

- 源文件：99 → **104**（+5：pdf_adapter / docx_adapter / backlink / graph / shared）
- 测试文件：34 → **39**（+5）
- 单元测试：461 → **532**（+71）
- CLI 命令：45 → **46**（+build-graph）
- 模块：20（不变，子文件扩充）

---

## v3.11.17 (2026-07-13)

### 安全加固

**.env→Keychain 密钥迁移：**
- 4 个 API Key（DEEPSEEK_API_KEY / BAILIAN_API_KEY / TRELLO_API_KEY / TRELLO_TOKEN）从 `.env` 明文迁移到 macOS Keychain
- `.env` 中对应行已注释保留为标记，不再影响安全扫描

**去重安全提醒：**
- `_check_plaintext_keys()` 新增进程级去重标志 `_plaintext_keys_warned`，同一进程只输出一次提醒
- 空值占位（如 `LARK_APP_SECRET=`）不再计入明文计数

### Bug 修复

**secrets-list 输出不全：**
- macOS `security` 命令输出包含 `<blob>` 标签（如 `"acct"<blob>="KEY_NAME"`），原有 regex 未匹配导致多 key 漏显示
- `list_secrets()` 重构为遍历已知键名表 + `get_secret()`，确保所有已存储密钥正确显示

### 测试

- 新增 `test_config_security` 中 `_reset_plaintext_warning` fixture，每个测试前重置去重标志，保证独立运行
- 460 → 461 测试

### 项目指标

- 单元测试：**461**（34 文件）
- 数据源文档：691
- Keychain 密钥：4 个已保护

---

## v3.11.16 (2026-07-13)

### 安全加固（P0）

**Memory 原子写入：**
- 新增 `_atomic_write_json()` 函数（`tempfile.mkstemp` + `os.replace`），崩溃不损坏 JSON
- 覆盖 `long_term.py`、`session.py`、`working.py`、`lifecycle.py` 全部 `_save` 路径

**API Key 明文提醒：**
- 新增 `_check_plaintext_keys()` — 启动时检测 `.env` 中的 API Key/Token，输出 stderr 安全提醒，建议迁移到 macOS Keychain

**LLMQueryPlanner 实现：**
- `LLMQueryPlanner.enhance()` 从空桩升级为完整实现：低置信度（generic/topic 意图或实体 <2）触发 LLM 语义增强，异常优雅回退规则规划

### 工程质量（P1）

**HTTP 客户端去重（-85 行重复代码）：**
- 新增 `core/http_client.py`：`http_post_json()` 统一 POST + 指数退避重试逻辑
- `provider.py` 和 `embedder.py` 的 `_post_json` 改为委托共享工具
- 清理 `provider.py` 不再需要的 `random`/`socket`/`time`/`urllib` 导入

**Chunk JSON 加载去重：**
- 新增 `ingest.chunker.iter_chunk_items()` 生成器，统一 3 处重复的 chunk_summary 加载模式
- `searcher.py`、`discovery.py`、`handlers.py` 全部切换

### 测试（+63 用例）

- 新增 `test_http_client.py`（14 用例）：POST 成功/重试/耗尽/不可重试/自定义异常/边界
- 新增 `test_planner.py`（23 用例）：意图识别/实体提取/LLM 增强触发/降级回退
- 新增 `test_chunker.py`（8 用例）：空源/禁用源/缺失/多源/损坏 JSON 容错
- 新增 `test_config_security.py`（11 用例）：明文 Key 检测/原子写入/内容完整性

### 项目指标

- 测试：397 → **460**（34 文件）
- 源文件：98 → **99**（+`http_client.py`）
- 模块：19 → **20**
- 知识库：689 文档 / 3,956 Chunk / 6,789 向量

---

## v3.11.15 (2026-07-10)

### Bug 修复

**Trello 客户端 Python 3.13 SSL 兼容：**
- 新增 `_TrelloHTTPSConnection` 类，IP 直连时使用正确 SNI hostname
- `_request` 按 URL 类型分流：IP → `_request_via_ip` / 域名 → `urlopen`
- 修复 `_make_trello_url` 域名回退双 `/1` 路径 bug
- `_load_trello_config` 增加 `resolve_env_vars` 解析 `${VAR}` 占位符

---

## v3.11.14 (2026-07-10)

### 新功能

- **新增 `iris-process` Skill**：富媒体输入自动检测 → `route-model` 路由 → 三阶段 process 流水线
- **职责边界清晰化**：`iris-ask` 收窄为纯文本问答，富媒体改走 `iris-process`

### Bug 修复

- `complex_input/pipeline.py` Stage 3 失败路径 f-string 模板变量未替换（`{{query}}` → `{query}`）

### 开源脱敏（未发布随 3.11.14 批量提交）

**源码脱敏：**
- `src/iris/analysis/service.py`：`_SUB_AREA_KEYWORDS` 字典清空，移除 11 个硬编码内部业务子领域词条（某检测项目 / 图像采集3.0 / 图像验证 / 视频审核 / 在线评估 / 工作流 / 消费品类 / 二手商品 / 兴趣品类 / 推荐 / 搜索），改为用户自定义注释说明；移除注释中员工姓名
- `src/iris/wiki/navigation.py`：`EXTERNAL_CONCEPT_PATTERNS` 从 15 个业务特定正则缩减为 2 个通用示例，添加用户自定义说明
- `src/iris/wiki/term_extractor.py`、`src/iris/analysis/_biweekly_types.py`：移除注释/文档字符串中的员工姓名与内部项目代号
- `templates/prompt/biweekly_stage3_direction.md`、`biweekly_stage1_filter.md`：示例中的内部项目代号替换为通用占位符
- `scripts/extract_weekly_reports.py`：移除注释中的员工姓名及真实企业邮箱域名
- `iris-report SKILL.md` 移除个人姓名示例

**测试数据脱敏（9 个测试文件）：**
- 员工真实姓名 → 张三 / 李四 / 王小明 / 王五 / 赵六；内部项目代号 → 项目Alpha / Beta / Gamma / Delta / Epsilon
- 企业邮箱（`*@example.com`）→ `*@example.com`

**Git 追踪清理：**
- `.gitignore` 新增：`.claude/scheduled_tasks.json`、`.claude/scheduled_tasks.lock`、`方案清单.md`、`需求清单.md`
- `git rm --cached` 取消追踪上述 4 个文件（文件保留在磁盘）

**同期 Bug 修复（测试数据替换后发现）：**
- `test_deep_eval.py`、`test_biweekly_dedup.py`、`test_weekly_report_extract.py` 断言调整

---

## v3.11.13 (2026-07-10)

### 开源脱敏全面清理

**源码脱敏：**
- `src/iris/analysis/service.py`：`_SUB_AREA_KEYWORDS` 字典清空，移除 11 个硬编码内部业务子领域词条（某检测项目 / 图像采集3.0 / 图像验证 / 视频审核 / 在线评估 / 工作流 / 消费品类 / 二手商品 / 兴趣品类 / 推荐 / 搜索），改为用户自定义注释说明；移除注释中员工姓名
- `src/iris/wiki/navigation.py`：`EXTERNAL_CONCEPT_PATTERNS` 从 15 个业务特定正则缩减为 2 个通用示例，添加用户自定义说明
- `src/iris/wiki/term_extractor.py`、`src/iris/analysis/_biweekly_types.py`：移除注释/文档字符串中的员工姓名与内部项目代号
- `templates/prompt/biweekly_stage3_direction.md`、`biweekly_stage1_filter.md`：示例中的内部项目代号替换为通用占位符
- `scripts/extract_weekly_reports.py`：移除注释中的员工姓名及真实企业邮箱域名

**测试数据脱敏（9 个测试文件）：**
- 员工真实姓名（团队成员A / 团队成员B / 团队成员C / 团队成员D / 团队成员E / 团队成员F 等）→ 张三 / 李四 / 王小明 / 王五 / 赵六
- 内部项目代号（某检测项目 / 图像采集3.0 / 图像验证 / 视频审核 / 在线评估）→ 项目Alpha / Beta / Gamma / Delta / Epsilon
- 企业邮箱（`*@example.com`）→ `*@example.com`

**Git 追踪清理：**
- `.gitignore` 新增：`.claude/scheduled_tasks.json`、`.claude/scheduled_tasks.lock`、`方案清单.md`、`需求清单.md`
- `git rm --cached` 取消追踪上述 4 个文件（文件保留在磁盘）

**同期 Bug 修复（测试数据替换后发现）：**
- `test_deep_eval.py`：`search_sources_by_keywords(["拍照"])` 关键词与新内容不匹配 → 改为 `["项目Beta"]`
- `test_biweekly_dedup.py`：`"低好能"` 断言在 SAMPLE_REPORT 更新后变为空洞真值 → 改为 `"长尾品类"`
- `test_weekly_report_extract.py`：`sorted(["李四","张三"])` Unicode 字典序错误 → 修正为 `["张三","李四"]`

---

## v3.11.12 (2026-07-09)

### extract-didi-travel PDF 文字直接提取 + 表格输出格式

**核心改进：**
- PDF 解析新增文字提取路径：`pdf_extract_text()` 优先从文字型 PDF 直接提取文字，扫描件才降级为多模态图像理解
- `resolve_input_pages()` 拆分为 `resolve_inputs()`，返回 `(text_inputs, page_images)` 两路输入
- 新增 `PdfTextInput` dataclass 承载文字型 PDF 内容
- 新增 `_process_text_input()`：将 PDF 文字送 base_model 解析，替代 adv_model 图像理解
- `stage1_extract_entries()` 同时处理两路输入：文字路径串行，图像路径并发
- 新增 `STAGE1_TEXT_PROMPT`：专为文字型 PDF 设计，处理跨行断行问题

**输出格式改进：**
- Stage2 输出由逐条纵向格式改为转置 Markdown 表格（行为字段，列为差旅+总计），与报销单图表格式一致

**效果：**
- 文字型 PDF 无图像识别误差，金额准确率 100%（原图像路径漏识别 ¥20）
- 速度更快，无需多模态 API 调用

---

## v3.11.11 (2026-07-09)

### extract-didi-travel 代码审查修复（8 项）

**死代码清理：**
- 删除 `_process_single_page` 中未使用的 `b64_kb` 变量（与 `size_kb` 属性重复）
- 删除 `TripEntry.to_dict()` 方法（流水线中无任何调用方）
- 删除 `TripEntry.route_type` 字段及 Stage1 Prompt 中对应的 JSON 字段说明（Stage2 从不消费，浪费 LLM token）

**Bug 修复：**
- 自动命名逻辑：`min()`/`max()` 在全空日期时抛 `ValueError`，改为先收集非空日期列表，空时回退文件名为 `unknown`
- Stage2 失败处理：原先将错误字符串静默写入输出文件，改为 `raise RuntimeError`，`main()` catch 后打印错误并 `sys.exit(1)`

**代码质量：**
- 删除重复的 `# ── Stage 1: adv_model 多模态理解` section 注释头
- 修正 `stage1_extract_entries` docstring："最多 4 页" → "最多 2 页"（与 `min(2, ...)` 对齐）
- 删除 `stage2_consolidate` 内对 `entries` 的冗余排序（`stage1_extract_entries` 末尾已排序）

---

## v3.11.10 (2026-07-09)

### extract-weekly-reports 扫描漏人修复 + 测试补全（384 → 397 测试）

**问题**：白名单 12 人本周实际 10 人提交周报，`extract-weekly-reports` CLI 仅扫到 3 人，静默漏掉 7 人（团队成员B、刘备、团队成员A、团队成员G、团队成员D、团队成员H、团队成员I）。根因 `LarkMailScanner.scan_triage` 走「folder + time_range」list 路径，这些成员的周报带 `IMPORTANT` 标签、散落在 priority/自定义文件夹并落在 list 首屏 50 封之外，list 路径捞不到；search 路径（`--query`，跨全文件夹）可一次命中。

**修复（`scripts/extract_weekly_reports.py` + `config/weekly_report.json`）：**
- `scan_triage` 新增 `query` 参数：非空时走跨全文件夹 search 路径（追加 `--query`，filter 仅带 `time_range` 不带 folder）；为空保持旧 folder-list 行为（向后兼容）
- `scan_mailbox` 新增 search 模式：按 `subject_keywords` 逐关键词搜索、按 `message_id` 合并去重；在逐封拉取完整正文前新增 `_prefilter_summaries`（按白名单 email 子串 + 主题关键词预筛 summary），message GET 由 ~50 次降至 ~12 次
- `EmailFilter`：新增 `is_recall_notice` 排除「发件人已撤回邮件」通知；`filter_emails` 同一发件人按日期保留最新一封（团队成员C本周重复 3 封 → 1 封干净版）
- 配置层：`config/weekly_report.json` 新增 `scan.mode`（默认 `search`，保留 `folder` 兜底），schema 版本 3.2 → 3.3；同步 `.example`
- 实测：dry-run 命中 3 → 10 人，10 位白名单提交成员 w27 周报全部落地 `SOURCE/07-成员周报`

**测试补全（+13 用例，384 → 397）：**
- `test_weekly_report_extract.py`（新建，13 用例）：scan_triage search/folder 路径命令组装（`--query` 有无、filter 是否带 folder）/ `scan_mode` 解析（默认 search、非法值兜底）/ `_prefilter_summaries` 白名单+关键词预筛与空白名单透传 / `EmailFilter` 撤回通知排除 + 同人保留最新 + 非白名单/无关键词剔除

---

### 安全加固 + 工程质量优化 + 测试补全（315 → 384 测试）

**安全加固（开源准备）：**
- S1: 双周报 footer 签名去硬编码 — `report_author` 改从 `config/app.json biweekly_report.report_author` 读取，空值时不追加署名行（`analysis/service.py`、`handlers.py`、`biweekly_stage4_assemble.md`）
- S2: 模板敏感内容脱敏 — `biweekly_stage3_direction.md` / `biweekly_report.md` 中的真实员工姓名及具体决策内容替换为通用匿名示例
- S3: `discovery_rules.py` 脱敏 — `LOW_VALUE_TITLES` 移除含真实员工姓名的条目，`STOPWORDS` 移除内部品牌词 `exampleorg`
- S4: LLM Prompt 业务域配置化 — 新建 `wiki/_constants.build_domain_context(app_config)`；`term_extractor` / `asr_hotwords` / `asr_prompt_optimizer` 三处 Prompt 中的公司名/部门名/业务域改从 `config/app.json organization` 字段注入；`handlers.py` 统一构建 `domain_context` 传入；`app.json.example` 新增 `organization` 和 `retrieval` 配置段
- S5: 日志截断敏感内容 — `_sanitize_log_payload`：`markdown` 字段截断至前 200 字，`blocks` 只保留 `relative_path` 和 `score`
- S6: ASR Prompt 示例脱敏 — `asr_hotwords.py` / `term_extractor.py` / `asr_prompt_optimizer.py` 输出格式示例中的真实员工姓名（团队成员J、团队成员E）和项目名（图像采集3.0、质检自动化）替换为通用占位；`biweekly_stage3_direction.md` 反例示范脱敏；`biweekly_stage4_assemble.md` 移除 LLM 无法执行的条件签名指令

**工程质量：**
- Q1: `EnhancedRetriever._cache` 线程安全 — 新增 `threading.Lock`，`_cache_get`/`_cache_set` 加锁，修复 `update_all_pages` 并发场景下的竞态
- Q2: 封装 `ModelManager._models` 私有属性访问 — 新增 `ModelManager.find_model_by_name()` 公开方法，`provider._find_model_by_name` 改为委托调用
- Q3: `QueryRewriter.SYNONYM_MAP` 配置化 — 新增 `__init__(extra_synonyms)` 参数，与默认词典合并；`EnhancedRetriever` 从 `app.json retrieval.synonym_extensions` 加载扩展
- Q4: 检索评分魔法数字命名常量化 — 提取 `_BOOST_DEFINITION=2.0` / `_BOOST_TIMELINE=1.8` / `_BOOST_PROJECT=1.2` 并加注释说明来源
- Q5: RRF 融合参数配置化 — `_rrf_fuse` 增加 `bm25_bonus` 参数，`search()` 从 `app.json retrieval.rrf` 读取，`app.json.example` 新增配置段
- Q6: Wiki 内容提取加固 — `_extract_wiki_content` 优先用严格正则 `---\ntitle:` 定位 frontmatter，降级时记录 warning 日志，防止含 `---` 代码块时误匹配

**测试补全（+69 用例，315 → 384）：**
- `test_complex_input_pipeline.py`（新建，10 用例）：三阶段正常路径 / Stage1 失败 / Stage2 失败降级 / to_dict 序列化
- `test_retrieval_enhanced.py`（新建，22 用例）：RRF 融合排序/权重/纯向量命中/top_k / LRU 缓存线程安全/TTL/驱逐 / QueryRewriter 扩展同义词合并 / parse_ranked_ids / apply_rank_order
- `test_wiki_update_validation.py`（新建，18 用例）：`_validate_update_output` 三分支 / `_extract_wiki_content` 严格正则/代码块/对话前缀/降级 / `build_domain_context` 全配置/空配置/仅 name/无 name
- `test_memory_lifecycle.py`（新建，13 用例）：`_is_item_stale` 5 边界 / `age()` 全超期/全保留/混合 / `list_stale` / `detect_conflicts` / `summarize` 裁剪
- `test_biweekly_pipeline.py`（追加，8 用例）：`_sanitize_log_payload` 5 场景 / `report_author` 追加/不追加/防重复
- `test_provider_fallback.py`（追加，5 用例）：`find_model_by_name` 按名/按 ID/不存在/含 api_key/含 _model_id



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
