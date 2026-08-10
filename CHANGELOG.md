## v3.23.1 (2026-08-10)

遗留修复 + 使用指南（4 文件 / +311）。

### 1. 背景

v3.23.0 发布后发现两个遗留问题：asr-corrector 的 Ctrl+C 在 Python 3.13 下可能失效（与会议助理同款的 SIGINT 问题）；`scripts/verify_hotkey_inject.py` 验证脚本未纳入版本控制。

### 2. 改动（文件路径）

- **asr-corrector Ctrl+C 修复**（`src/iris/wiki/asr/corrector.py`）：Python 3.13 默认 SIGINT 处理无法中断 `time.sleep`（主线程睡眠时不抛 KeyboardInterrupt，进程对 Ctrl+C 无反应、pid 文件不清理）→ `run_forever` 显式注册 `signal.signal(SIGINT, raise KeyboardInterrupt)`。真机验证：SIGINT → 「校正引擎已停止」→ pid 清理
- **`scripts/verify_hotkey_inject.py` 纳入版本控制**：CGEventPost 热键注入验证工具（端到端测试/复现 vocotype 按住说话，须 `--keycode 61` 右 Option）；补充「纯修饰键热键注入左 Option 无反应」实测提示
- **使用指南 `docs/meeting-live-assistant-usage.md`**（按 asr-corrector-usage.md 惯例）：快速开始 / 命令 / 前置条件 / 配置 / 工作链路 / 面板说明 / 过程文档 / 常见问题；README 补链接

### 3. 测试

无新增用例（行为修复）：ASR 相关 194 个 + 全量 2,708 通过（1 个 feed 既有失败与本次无关）。

### 版本升级

| 版本 | 值 | 理由 |
|------|:---:|------|
| 产品版本 | 3.23.0 → 3.23.1 | 修复 asr-corrector 3.13 SIGINT + 工具脚本纳入版本控制 |
| 协议版本 | 3.16（不变） | 无命令变更 |

---

## v3.23.0 (2026-08-10)

实时会议助理 `iris meeting-live-assistant`：会议中实时提炼要点/风险/决策点并提示关键提问（23 文件 / +2,080）。

### 1. 背景

会议中语音信息密度高、转瞬即逝。需要一种「会议当下」的实时助理：逐段转写 → 校正 → 结合知识库分析 → 提示值得追问的问题，同时把过程实时写入 Markdown 文档，会后直接拿到完整记录。与 `transcribe-meeting`（事后批量）互补；与 `asr-corrector` 运行时互斥（独占剪贴板）。

### 2. 改动（文件路径）

- **新模块 `src/iris/assistant/`（9 文件）**：`models.py`（VoiceSegment/SegmentAnalysis/MeetingState/AssistantConfig Pydantic 模型）；`_clipboard.py`（剪贴板轮询 + vocotype 特征判定，复用 corrector `_is_asr_text`/`_looks_like_written_chinese`/`_clipboard_has_rich_text`）；`_corrector.py`（包装 AsrCorrector 双通道：词典 fast 毫秒级 + LLM deep 8s 内部降级 + 上下文入窗）；`_retriever.py`（包装 EnhancedRetriever，构造/查询失败优雅降级）；`_analyzer.py`（LLM 结构化分析：要点/风险/问题/决策点/建议提问，15s deadline + 容错归一化 + 失败降级）；`_session.py`（积压丢弃状态机：Condition + 单槽 pending，处理中 submit 覆盖旧段，dropped_count 累积）；`_doc_writer.py`（过程文档原子重写：frontmatter + 会议累计区 + 逐段记录，tmp + os.replace）；`_panel.py`（ANSI 清屏整帧终端面板，stdout 独占、日志走 stderr）；`live.py`（主编排：`_probe_running` 只读互斥探测 + ProcessRegistry 防重复 + ThreadPoolExecutor(2) 并行深度校正与检索 + 显式 SIGINT handler 保证 3.13 Ctrl+C 可靠退出）
- **CLI 注册（3 文件）**：`src/iris/app/_cli_main.py`（COMMANDS 64→65）、`src/iris/app/cli/_handlers/_assistant.py`（handler + ASSISTANT_HANDLERS）、`src/iris/app/cli/handlers.py`（聚合）；`--output` 共用参数
- **配置（2 文件）**：`src/iris/config/models.py`（AppConfig 加 `assistant` 段）、`config/app.json.example`（output_dir/top_k/llm_model/poll_interval/doc_rewrite_every）
- **模板（2 文件）**：`templates/prompt/meeting_live_analyze.md` + `src/iris/utils/prompting.py` FALLBACK_TEMPLATES 兜底
- **文档**：`docs/meeting-live-assistant-design.md`（方案设计 v1.0，13 项需求冻结）

### 3. 测试

新测试 +63（unit +56：models 11 / session 9 / clipboard 6 / analyzer 11 / doc_writer 10 / live 9；integration +7 端到端：两段全链路、LLM 降级、互斥启动、CLI 注册）。验证：新增 63 全过，unit 全量 1,360（1 个 feed 既有失败与本改动无关），integration 全量 237 全过；真机冒烟：启动 → SIGINT 优雅退出（统计帧 + pid 清理 + 文档保留），asr-corrector 在跑时让位。

### 版本升级

| 版本 | 值 | 理由 |
|------|:---:|------|
| 产品版本 | 3.22.5 → 3.23.0 | 新增实时会议助理功能 |
| 协议版本 | 3.15 → 3.16 | 新增 meeting-live-assistant 命令 |
| 数据版本 | app 3.4 → 3.5 | 新增 assistant 配置段（output_dir/top_k/llm_model/poll_interval/doc_rewrite_every），与 v3.19.26 reminders 段先例一致 |

---

## v3.22.5 (2026-08-10)

ASR 校正引擎热键门控修复：长语音不再被跳过（2 文件 / +215 -4）。

### 1. 背景

用户按住热键让 vocotype 输入 1 分多钟语音，转写结果写入剪贴板后被 asr-corrector 跳过：

```
[Iris] 📋 剪贴板变化 (19 字): 下半年的话，还是取得了非常不错的成绩。
[Iris] ⏭ 跳过：不在监听窗口 (held=False, released_at=0.0, elapsed=1669928.18s)
```

两个叠加问题：
- **热键监听失效即全跳过（P0 bug）**：CGEventTap 无事件流入（辅助功能权限缺失等）时 `start()` 返回 False，但 `run_forever` 只打印警告未置空监听器；`_tick` 门控按「配置了热键（mask>0）」而非「监听器可用」判定 → `in_listen_window` 恒 False → 此后所有剪贴板变化一律跳过。设计意图的「降级为内容特征判定」分支只覆盖热键配置为空的情况，没覆盖启动失败。
- **3 秒监听窗口装不下长语音（设计缺陷）**：vocotype 为「松开热键后才开始转写」，1 分钟语音的转写+写剪贴板耗时远超固定 `_LISTEN_WINDOW_SEC = 3.0`，即使热键正常也会被跳过。

### 2. 改动（`src/iris/wiki/asr/corrector.py`）

- **门控降级修复**：`run_forever` 中 `CGEventTap.start()` 失败时置空 `self._hotkey_monitor`（并提示权限）；`_tick` 门控判定从 `self._hotkey_mask > 0` 改为 `self._hotkey_monitor is not None`——监听器不可用时放行，改由内容特征判定（`_is_asr_text` + 富文本检查）兜底。
- **长语音窗口**：新增 `_listen_window_sec()` = `max(3s, min(按住时长, 120s))`；`_HotkeyMonitor` 记录按下时刻（flagsChanged / keyDown 分支）并暴露 `hold_duration` 属性。1 分钟语音 → 释放后 60s 内的剪贴板变化仍被处理。

### 3. 测试

- `tests/unit/test_asr_corrector.py` +13 用例（3 个新测试类）：`TestListenWindow`（基础 3s / 长语音放宽 / 120s 上限截断）、`TestHotkeyMonitorHoldDuration`（按住时长计算）、`TestListenWindowGateFallback`（监听器不可用降级放行 / 可用且超窗口仍跳过 / 长语音窗口内放行 / 超过 120s 上限后跳过）。
- 验证：全部 ASR 相关测试 183 个 + 单元测试全量 **1,304 用例全通过**（unit 1,291 → 1,304）。

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.22.4 | **3.22.5** | ASR 校正热键门控 P0 bug 修复 + 长语音窗口 |
| 协议版本 | 3.15 | 3.15（不变） | 无新增 CLI 命令 |

---

## v3.22.4 (2026-08-10)

周报提取主题日期不一致自动标注（2 文件 / +60 行）。

### 1. 背景

提取 W32 成员周报时发现：李嘉晨 08-07 发送的周报邮件主题仍写「20260731」（复制上周标题未改日期），归档后「邮件信息」栏主题日期与实际发送日期矛盾，易误读为错误周期。

### 2. 改动（`scripts/extract_weekly_reports.py`）

- 新增 `WeeklyReportMarkdownGenerator._subject_date_mismatch_note()` 静态方法：从主题中提取 `YYYYMMDD` / `YYYY-MM-DD` 日期（正则，兼容时区），与邮件实际发送日期比较，不一致时生成标注文本。
- `generate_content` 邮件信息栏集成：不一致时追加 `- **⚠️ 主题日期与发送日期不一致**: 主题标注 X，实际发送 Y（可能是复制标题未改日期）`；一致或无日期时不加注（零噪音）。
- 已生成的 `20260807-周报-w32-李嘉晨.md` 同步加注（数据文件，不入库）。

### 3. 测试

- `tests/test_weekly_report_extract.py` +4 用例（`test_subject_date_mismatch_note_detects` / `consistent_returns_none` / `generate_content_annotates_mismatch`），文件 12 → 16 用例。
- 验证：全量 2,630 → **2,633 用例全通过**（unit 1,291 / integration 230 / 根目录 1,112）。

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.22.3 | **3.22.4** | 周报提取标注增强 |
| 协议版本 | 3.15 | 3.15（不变） | 无新增 CLI 命令 |

---

## v3.22.3 (2026-08-07)

知识库全面体检修复：索引死数据 45% + 知识图谱 LLM 边清零（5 文件 / +152 -6）。

### 1. 检索索引死数据修复（`chunker.py`）

- **Bug**：`_build_chunks_from_scan` 的 `if deleted_paths or reused_documents > 0:` 分支本意是「增量模式保留未变更旧 chunk」，但全量重建（`deleted_paths` 为空且 `reused>0`）也会进入，把已删除/已归档迁移文档的旧 chunk 全部加回 — 死数据随每次全量重建累积。实测 chunk_summary 中 **4,636 条死 chunk（占 45.1%）**，多为 v3.21.1 `YYYY/` 归档迁移前的旧扁平路径残留，向量索引与 BM25 同步污染，检索候选近半是已删除文档内容。
- **修复**：该分支增加 `incremental` 条件（`_build_chunks_from_scan` 新增参数，三个 build 方法透传）。
- **重建验证**：chunk 10,290 → **5,939**（死数据清零）、向量 10,321 → **5,939**（与 chunk 完全一致，清除 31 条多余残留）、覆盖率 201.7% → **100%**。

### 2. 知识图谱 LLM 边清零修复（`graph.py`）

- **Bug**：`extract_relations` 末尾 `self._edges = wikilink_edges + all_new_edges` — 增量刷新（`full=False`）只保留本次重提取页面的 LLM 边，未重提取页面的旧 LLM 边全部丢弃（8/3 提取 592 条后 1 分钟内即被一次增量刷新清零；v3.22.1 只修了全量模式的去重基准，未覆盖此路径）。
- **修复**：合并时保留未被本次提取覆盖的旧 LLM 边（同 key 以新提取为准，不重复）。
- **恢复**：从 `relations/` 缓存零成本恢复 591 条 LLM 边；增量刷新验证 591 → **633**（新增 42 条且旧边全保留，不再丢失）。

### 3. deep-eval CLI 参数补齐（`_cli_main.py`）

- `--page-filter` / `--sample-rate` 在 handler 已实现（`getattr`）但 argparse 未注册 — skill/CLAUDE.md 文档承诺的用法一直无法执行，补齐对齐。

### 4. 回归测试

- 新增 `tests/unit/test_chunker_full_rebuild.py`（2 用例：全量重建丢弃死路径 / 增量保留未变更并清理 deleted）+ `tests/integration/test_graph.py` `TestGraphLlmEdgePreserve`（2 用例：增量保留旧 LLM 边 / 全量同 key 以新提取为准）。
- 验证：单元测试 1,289 → **1,291 全通过**。

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.22.2 | **3.22.3** | 两个 P0 数据质量 bug 修复 |
| 协议版本 | 3.15 | 3.15（不变） | 无新增 CLI 命令（deep-eval 参数为已有命令补齐） |

---

## v3.22.2 (2026-08-04)

wikilink 注入残留清理（2 文件 / +7 -34）。

### 1. 过时注释修正

- **`feishu/chat_digest.py`**：生成输出注释「含 frontmatter + wikilink 注入」修正为「注入 frontmatter 元数据」。
- **`scripts/extract_weekly_reports.py`**：`generate_content` docstring 与调用点注释移除「wikilink 注入」表述（v3.21.0 收敛后已无实际注入，注释残留误导）。

### 2. wiki_root 死参数链删除

- **`chat_digest.py`**：删除 `_build_markdown` 的 `wiki_root` 参数（函数体内未使用）、`_resolve_wiki_root_safe()` 方法及调用点传参；**保留** `self._wiki_root` 字段与 `_resolve_wiki_root()`（`_load_wiki_context` 加载 Wiki 上下文供 LLM 提炼，真实用途）。
- **`extract_weekly_reports.py`**：删除 `__init__`/`generate_content` 的 `wiki_root` 参数、`self.wiki_root` 字段、main 中 `wiki_root` 初始化与兜底加载块（`if bundle is None` + `bundle.wiki` 提取）及构造传参，共 7 处。

### 3. 验证

- 相关测试（chat_digest / weekly_report_extract / frontmatter）52 个全部通过；语法检查 OK。

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.22.1 | **3.22.2** | 代码清理（注释 + 死代码） |
| 协议版本 | 3.15 | 3.15（不变） | 无新增 CLI 命令 |

---

## v3.22.1 (2026-08-03)

Wiki 发现噪音过滤 + 知识图谱全量重建边去重修复（3 文件 / +76 行）。

### 1. Wiki 候选发现噪音过滤（`discovery.py`）

- **新增 `is_noise_candidate()`**：过滤周报模板固定章节标题（「本内容由AI」「💼 本周工作」「下周计划」「关键指标/数据」等 10 个固定文本 + 6 个 emoji 前缀）。
- **集成到 `discover()` 流水线**：在 `cluster_and_resolve` 之后过滤噪音候选，与 `suppress_path_concentrated_noise` 互补。

### 2. 知识图谱全量重建修复（`graph.py`）

- **Bug 修复**：`full=True` 全量重建时，去重基准 `base_edges` 只保留 wikilink 边。旧逻辑将全部 `self._edges`（含历史 LLM 边）传入 `extract()` 作为去重基准 — 旧 LLM 边既参与去重（新提取相同边被跳过）又被下方过滤丢弃，导致每次全量重建后 LLM 关系边数逐次退化。
- **修复后**：`base_edges` 根据 `full` 参数区分 — 全量重建时只保留 wikilink 边（保留结构语义），增量更新时保留所有旧边。

### 3. 测试

- 新增 `TestNoiseCandidateFilter` 类（`test_wiki_discovery.py`）：参数化噪音过滤测试 + 真实标题保留测试 + `discover()` 集成过滤验证（+50 行）。

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.22.0 | **3.22.1** | Bug 修复 + 小功能增强（Wiki 噪音过滤） |
| 协议版本 | 3.15 | 3.15（不变） | 无新增 CLI 命令 |

## v3.22.0 (2026-08-02)

合并 0802-alpha → main — 开源信息泄露治理全库脱敏（40+ 文件）。

### 1. 敏感默认值配置化（生产代码）

- **`IRIS_BOT_USER_ID` 真实 open_id 改环境变量**（`feed/_feishu_bridge.py` / `feed/_dispatcher.py` / `feed/feed_pipeline.py`）：未配置时跳过飞书 Bot 推送（不再硬编码真实 open_id）。
- **团队名单与 `dept_op_keyword` 默认值清空**（`analysis/_biweekly_collector.py` / `feed/_okr_loader.py` / `feed/feed_config.py`）：`_DEFAULT_TEAM_OKR_NAMES` 与 `dept_op_keyword` 默认空，改由 `app.json` 的 `app.biweekly_report.team_okr_patterns` / `dept_op_keyword` 配置驱动；显式写 `null` 时按空串处理（null 防御，修复脱敏引入的崩溃缺陷）。
- `core/frontmatter_batch.py` / `wiki/asr/*` / `wiki/wikilink_injector.py`：内部业务词与示例泛化。

### 2. 全库泛化（文档与模板）

- **真实人名 → 通用占位**：冯扬/卞凯/李嘉晨等 11 人姓名替换为「本人/团队成员/负责人」等通用表述。
- **企业邮箱 → `example.com`**：`zhuanzhuan.com` 域名全库替换。
- **真实 OKR/项目名/业务指标 → 通用词**：图验技术/拍照3.0/XRay/台球杆/直检率等替换为「图像验证技术/硬件检测/直检率指标」等（`templates/prompt/biweekly_*.md` / `misreadings.md` / `generate_person.txt`）。
- **Skill 文档重写**：iris-okr-check KR 检索词表、iris-feed dry-run 示例、iris-wiki 示例整体替换。
- **DESIGN-feed-collect.md / CHANGELOG.md 二次脱敏**：历史条目中残留的飞书 ID/群名/成员数/内部项目名（zz-algo-plat 等）清理。

### 3. 测试断言同步（61 文件）

- 真实人名/邮箱/OKR 断言 → 通用占位断言；新增 `test_feed_okr_loader.py` 配置驱动用例（72 行变更）。

### 4. 合并冲突解决（1 处）

- `.claude/skills/iris-okr-check/SKILL.md`：合并保留 main 侧「01-目标管理 按年归档 `YYYY/` 子目录」路径修复 + alpha 侧「冯扬→本人姓名」泛化，两侧意图均保留。

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.21.1 | **3.22.0** | 安全治理 feature 合入（敏感配置化 + 全库脱敏） |
| 协议版本 | 3.15 | 3.15（不变） | 无新增 CLI 命令 |

## v3.21.1 (2026-08-02)

SOURCE 归档适配全面修复 — 双周报文件名日期前缀 + 递归查找修复 + Skill 文档 SOURCE 路径更新。

### 1. Bug 修复（3 项）

- **双周报文件名改为日期前缀**（`cli/_handlers/_content.py`）：`_build_biweekly_filename()` 生成 `{YYYYMMDD}-双周报-w{week}-{author}.md`（日期前缀），`resolve_source_archive_path` 正则 `re.match(r"(\d{4})(\d{2})\d{2}-")` 正确匹配 → 双周报归档到 `06-我的周报/YYYY/` 而非 flat。联动：`_biweekly_collector.py` glob 更新为 `rglob("*双周报-*.md")`（兼容新旧格式）；测试断言同步更新。
- **会议纪要刷新脚本递归查找**（`scripts/refresh_meeting_minutes.py`）：`find_source_matches()` 从一层 `iterdir` 改为 `rglob`，已归档到 `05-会议纪要/YYYYMM/` 的纪要可正确备份/去重。
- **风格源文件递归查找**（`analysis/service.py`）：`_stage0b_load_style()` 从扁平 `source_root / "06-我的周报" / style_from` 改为 `report_dir.rglob(style_from)`，`YYYY/` 子目录中的历史双周报可被找到并用作风格参考。

### 2. 脆弱点加固（2 项）

- **feed 简报生成**（`feed/_brief_generator.py`）：`generate()` 使用 `resolve_source_archive_path(self._source_root, "09-工作简报", filename)` 替代硬编码 `exec_date[:6]`，配置驱动；dry-run 保留手动路径避免 mkdir。
- **成员周报提取**（`scripts/extract_weekly_reports.py`）：`_resolve_output_dir` 文档更新，注释与 `config/source_archive.json` 一致性。

### 3. 死代码清理

- 移除 `transcribe_meeting/pipeline.py` 中 `_resolve_source_dir` 和 `_resolve_routed_source_dir` 两个扁平路径函数（零调用者，若复用会写出错）。

### 4. Skill 文档 SOURCE 路径更新（5 文件 / 18 处）

- **iris-okr-check**：OKR 来源/输出/查找路径加 `YYYY/`；5 个数据源目录表加归档子目录列（`06-我的周报/YYYY/`、`05-会议纪要/YYYYMM/`、…）
- **iris-feed**：`09-工作简报/YYYYMM/`、`01-目标管理/YYYY/`
- **iris-report**：`06-我的周报/YYYY/`、`01-目标管理/YYYY/`；`--style-from` 示例更新
- **iris-feishu-import**：路由表追加 `YYYYMM/` / `YYYY/` 归档子目录列
- **iris-meeting**：路由表追加归档子目录列

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.21.0 | **3.21.1** | 3 项 Bug 修复 + 2 项加固 + 死代码清理 + Skill 文档更新 |
| 协议版本 | 3.15 | 3.15（不变） | 无新增 CLI 命令 |

## v3.21.0 (2026-08-02)

批量 frontmatter 补全命令 + wikilink 注入收敛 + 周报按月归档 — SOURCE 文档元数据工程。

### 1. 新增 `frontmatter-batch` 命令（批量 frontmatter 补全）

- 新模块 `core/frontmatter_batch.py`（~610 行）：对 SOURCE 目录下 Markdown 文档批量注入 YAML frontmatter
  - **正则快速通道（零 LLM 成本）**：date（文件名 YYYYMMDD 前缀）/ title（首个 `#` 标题）/ type（9 类目录映射）/ updated，周报作者/邮箱、我的周报 period、会议 participants 等目录特有字段按正则提取
  - **LLM 深度通道**：按 9 类目录字段映射（`CATEGORY_FIELDS`，01~09 各配专属字段）提取 author / participants / period / source_url / version 等，`temperature=0` 输出严格 JSON，字段不覆盖已有值
  - **wikilink 注入（可选）**：懒初始化 `WikilinkInjector`，注入 `[[wikilink]]` 交叉链接
  - **备份/恢复**：处理前自动备份至 `SOURCE/_frontmatter_backup/{ts}/`，支持 `--list-backups` / `--restore` 一键回滚
  - **幂等安全**：已有 frontmatter 默认跳过（`--force` 覆盖）
- 新 handler `app/cli/_handlers/_frontmatter.py`；参数：`--source-dir`（可多次指定，默认全部 9 目录）/ `--no-llm` / `--no-wikilink` / `--no-backup` / `--list-backups` / `--restore` / `--dry-run` / `--force`
- 协议版本 3.14 → 3.15（新增 CLI 命令）

### 2. wikilink 注入收敛

从 4 个管道（`feishu-doc-convert` / `chat-digest` / `transcribe-meeting` / `extract-weekly-reports`）移除 wikilink 注入，统一由 `frontmatter-batch` 按需注入 — 管道输出专注 frontmatter 元数据，交叉链接走批量命令，减少管道耦合与重复代码。

### 3. 周报按月归档

`extract-weekly-reports` 输出自动归入 `YYYYMM` 月份子目录（`_resolve_output_dir`，不存在自动创建），对齐 SOURCE 月度归档策略。

### 4. 双周报 frontmatter 注入

`build-biweekly-report` 输出注入 title / date / type / period / author；`analysis` 服务新增 `period` 字段回传。

### 5. 测试

- 新增 `test_frontmatter_batch.py`（65 用例）：CATEGORY_FIELDS 目录映射 / 正则提取（日期/标题/作者/邮箱/period/参会人）/ LLM JSON 解析（代码块剥离与 JSON 区间截取）/ 幂等跳过 / 备份与恢复 / 类别目录推断
- 已有 2,561 用例零回归，总计 2,626 用例 / 134 文件

### 6. 文档

- CLAUDE.md「当前规模」重构（超长能力列表拆为独立段落）+ 历史版本摘要压缩（v3.19.10 ~ v3.19.24 摘要并入一行，完整历史见 CHANGELOG）

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.20.2 | **3.21.0** | frontmatter-batch 新命令 + wikilink 收敛 + 周报按月归档 + 双周报 frontmatter |
| 协议版本 | 3.14 | **3.15** | 新增 frontmatter-batch CLI 命令 |

## v3.20.2 (2026-07-30)

SOURCE 文档质量系统性提升 + 测试覆盖率提升 — 双线合并。

### Part A：frontmatter 标准化 + wikilink 自动注入 + 周报优化

**1. YAML frontmatter 标准化（核心基础设施）**

- 新增 `core/frontmatter.py`（~244 行）：`build_frontmatter` / `inject_frontmatter` / `parse_frontmatter` / `get_frontmatter_field` / `has_frontmatter` 五个公开函数，11 种 `DOC_TYPES` 常量
- YAML 规范合规：保留字检测、数字形式字符串检测、双引号转义、多行 block scalar 渲染、BOM 兼容
- `inject_frontmatter` 幂等安全，失败不影响文档生成

**2. 四个 CLI 管道 frontmatter 注入**

| 管道 | 字段 |
|------|------|
| `transcribe-meeting` | title, date, type, meeting_type, duration, source, participants, generated, route |
| `extract-weekly-reports` | title, author, date, type, email, week, ai_processed, ai_model |
| `chat-digest` | title, date, type, source_chat, chat_id, chat_type, message_count, time_start, time_end, extracted_at |
| `feishu-doc-convert` | title, date, type, author, source_url, doc_token, route |

统一顺序：wikilink 注入 → frontmatter 注入 → `safe_write_text`，每步独立 try/except 降级。

**3. wikilink 自动注入引擎（零 LLM 成本）**

- 新增 `wiki/wikilink_injector.py`（~339 行）：初始化时扫描 Wiki 目录构建 `title → relative_path` 索引
- 注入逻辑 + 嵌套防护（`_find_safe`）+ 保护区屏蔽（7 类正则），4 管道集成

**4. 成员周报 Prompt 增强** — 4 段落结构 + 量化指标要求 + 项目上下文注入

**5. 周报质量门禁** — `check_quality` 三级判定，不合格标记 `ai_quality: low`，不阻塞写入

### Part B：测试覆盖率系统提升

新增 253 个单元测试（9 个新文件），覆盖率 59.87% → 62.82%。

| Tier | 文件 | 用例 | 覆盖模块 |
|------|------|:---:|------|
| 1 纯函数 | `test_chunker_extended.py` | 53 | `_extract_fields` / `_chunk_lines` / `_split_content` 等 |
| | `test_formatter_extended.py` | 58 | 35 个 `_fmt_*` + `_add_kv` + `format_payload` |
| | `test_asr_hotwords_extended.py` | 27 | `_is_valid_hotword` / `_parse_hotwords_response` |
| | `test_embedder_extended.py` | 18 | `_cache_key` / LRU / `_infer_provider` |
| | `test_lifecycle_extended.py` | 30 | `merge` 6 策略 / `restore_archived` / `maintenance` |
| 2 Mock | `test_memory_updater_extended.py` | 24 | `_should_deep_analyze` / `_auto_resolve_conflict` |
| | `test_feishu_bridge.py` | 16 | `_run_lark_cli` / `raw_to_message` / `get_display_name` |
| 3 核心 | `test_memory_cache.py` | 15 | LRU 驱逐 / TTL 过期 / stats / 线程安全 |
| | `test_agent_adapter.py` | 12 | `AgentCapability` / `IRIS_CAPABILITIES` 校验 |

### 测试

- Part A：新增 `test_frontmatter.py`（31 用例）+ `test_wikilink_injector.py`（23 用例）
- Part B：新增 9 个测试文件（253 用例）
- 已有 2,253 用例零回归

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.20.1 | **3.20.2** | frontmatter 标准化 + wikilink 注入 + 测试覆盖率提升 |
| 协议版本 | 3.14 | 3.14（不变） | 无新增 CLI 命令 |
| 数据版本 | 不变 | 不变 | 无配置 Schema 变更 |

### 统计

| 指标 | 优化前 | 优化后 | 变化 |
|------|:---:|:---:|:---:|
| 测试用例数 | 2,254 | **2,561** | +307 |
| 测试文件数 | 120 | **131** | +11 |
| 代码覆盖率 | 59.87% | **62.82%** | +2.95% |

| 文件 | 行数变化 | 说明 |
|------|:--:|------|
| `core/frontmatter.py` | +244 | 新模块（Part A） |
| `wiki/wikilink_injector.py` | +339 | 新模块（Part A） |
| `core/__init__.py` | +16 / -2 | 导出（Part A） |
| `app/transcribe_meeting/pipeline.py` | +53 / -3 | frontmatter + wikilink（Part A） |
| `feishu/chat_digest.py` | +51 / -6 | frontmatter + wikilink（Part A） |
| `feishu/doc_convert.py` | +31 | frontmatter + wikilink（Part A） |
| `scripts/extract_weekly_reports.py` | +134 / -24 | frontmatter + wikilink + prompt + quality gate（Part A） |
| `templates/prompt/weekly_report_extract.md` | +25 / -13 | 4 段落 Prompt（Part A） |
| `tests/unit/test_frontmatter.py` | +273 | 新测试 31 用例（Part A） |
| `tests/unit/test_wikilink_injector.py` | +293 | 新测试 23 用例（Part A） |
| `tests/unit/test_chunker_extended.py` | ~+530 | 53 用例（Part B） |
| `tests/unit/test_formatter_extended.py` | ~+580 | 58 用例（Part B） |
| `tests/unit/test_asr_hotwords_extended.py` | ~+270 | 27 用例（Part B） |
| `tests/unit/test_embedder_extended.py` | ~+180 | 18 用例（Part B） |
| `tests/unit/test_lifecycle_extended.py` | ~+300 | 30 用例（Part B） |
| `tests/unit/test_memory_updater_extended.py` | ~+240 | 24 用例（Part B） |
| `tests/unit/test_feishu_bridge.py` | ~+160 | 16 用例（Part B） |
| `tests/unit/test_memory_cache.py` | ~+150 | 15 用例（Part B） |
| `tests/unit/test_agent_adapter.py` | ~+120 | 12 用例（Part B） |

---

## v3.20.1 (2026-07-30)

deep_eval 数据源 chunk 摘要路径配置化。

### 改进

- `DeepEvaluator` 初始化时，chunk 摘要文件路径由硬编码 `main_source_chunk_summary.json` 改为根据 `config.data_source.default_source` 动态加载，便于多数据源切换

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.20.0 | **3.20.1** | deep_eval 配置路径改进 |
| 协议版本 | 3.14 | 3.14（不变） | 无 CLI 变更 |
| 数据版本 | feeds.json v1 | feeds.json v1（不变） | 无配置变更 |

---

## v3.20.0 (2026-07-30)

iris-feed 文档提取 — 信息汇聚管道新增飞书文档自动转换与关联。

### iris-feed 文档提取（Step 5）

- 新模块 `feed/_doc_extractor.py`（`DocExtractor`）：从话题消息中收集飞书文档链接（docx/wiki/sheet/base），调用 `FeishuDocConverter` 转换为本地 Markdown，输出 `ConvertedDoc` 列表供简报关联
- **URL 检测**：正则匹配 `feishu.cn` 四类文档链接，单次 run 内 `set` 去重，跨次排重复用 `FeishuDocConverter` 的 `dedup_index`
- **执行时机**：插入 Pipeline Step 5（话题检测之后、简报生成之前），仅处理有文档链接的话题（零链接直接跳过，零 API 成本）
- **简报关联**：`BriefGenerator.generate()` 不再接收空列表，转换后的文档通过相对路径出现在简报「相关文档」段落
- **dry-run 支持**：预览模式返回占位 `ConvertedDoc`（仅含 URL），不实际调用飞书 API
- **失败隔离**：每个文档独立 try/except，转换失败或权限不足不影响其他文档和后续步骤

### 配置扩展

- `feeds.json` 的 `topic_config` 新增两项：
  - `extract_docs`（bool，默认 true）：是否启用文档提取
  - `doc_extract_max`（int，默认 10）：单次最多转换文档数（0 不限制）
- 新增 CLI 参数 `--no-extract-docs`：`feed-collect` 跳过文档提取
- `handle_feed_collect` 输出新增文档提取统计行（`--no-extract-docs` 时显示「已跳过」）
- `PipelineResult.converted_docs` 补齐（此前始终为空）

### 测试

- 新增 `test_feed_doc_extractor.py`（37 用例）：URL 提取 10 / 模式匹配 9 / URL 收集 4 / 提取器主流程 14（含 dry-run、成功、排重、失败、异常、max_docs 截断、混合场景）
- 已有 feed 测试 226 用例零回归

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.19.26 | **3.20.0** | 新增 iris-feed 文档提取功能 |
| 协议版本 | 3.13 | **3.14** | 新增 `--no-extract-docs` CLI 参数 |
| 数据版本 | feeds.json v1 | feeds.json v1（不变） | `topic_config` 新增可选字段，向后兼容 |

| 文件 | 行数变化 | 说明 |
|------|:--:|------|
| `feed/_doc_extractor.py` | +199 | 新模块 |
| `feed/feed_pipeline.py` | +18 / -8 | Step 5 接入 + bundle 存储 |
| `feed/feed_config.py` | +2 / -0 | 默认配置项 |
| `feed/__init__.py` | +2 / -0 | 导出 |
| `app/_cli_main.py` | +2 / -0 | `--no-extract-docs` |
| `app/cli/_handlers/_feed.py` | +12 / -3 | 参数透传 + 输出统计 |
| `.claude/skills/iris-feed/SKILL.md` | +16 / -5 | 文档提取功能说明 |
| `tests/unit/test_feed_doc_extractor.py` | +432 | 新测试（37 用例） |

---

## v3.19.26 (2026-07-29)

检索质量与知识库时效性四项优化 — chunk 重叠 + source_fingerprint 指纹追踪 + 向量索引模型守卫 + 主动提醒引擎。

### 1. chunk 切块重叠（检索召回质量）

- `_split_content` 新增 `overlap_chars` 参数：相邻 segment 之间携带上一段末尾内容（从句子边界起），跨段落承接信息（前提在上段末尾、结论在下段开头）不再丢失
- 新增 `_overlap_tail` / `_apply_overlap` 纯函数；`IngestionConfig` 新增 `chunk_overlap_chars`（默认 150，`data_source.json` 可配）
- 重叠取自原始上一块而非扩展后内容，避免级联膨胀

### 2. Wiki source_fingerprint 源文档指纹追踪（增量编译闭环）

- 页面 frontmatter 新增 `source_fingerprint` 段：记录生成时引用的源文档 relative_path + hash 前 12 位（`discovery_utils` 新增 render/strip/inject/parse 四个函数）
- `build_page` / `_update_page_with_content` 生成与增量更新时自动注入/刷新指纹
- `is_wiki_stale` 重写：有指纹页面按「任一源文档 hash 变化或删除 → 过时」精准判定；源文档未变则不再按 30 天轮转重生成（省 LLM 成本）；无指纹旧页面兜底按天数
- `discover-wiki`（`_check_existing_wiki`）与 `metrics-export`（stale_pages 统计）接入 hash 索引
- **配套修复**：`write_hash_index` 已有条目 hash 永不更新的 bug（`if rp not in index` → hash 变化时刷新条目）

### 3. 向量索引 embedder 模型守卫 + --force-rebuild

- 模型不匹配从「warning + 旧向量继续混用」改为**硬失败**：抛 `VectorIndexModelMismatchError`（混用两个模型的向量空间使余弦相似度失去意义，静默带病运行比报错更危险）
- 新增 CLI 参数 `--force-rebuild`：全量重建向量索引（此前日志提示该参数但它并不存在），同时清理已删除文档的残留向量
- daily-start 的 `_daily_vector_index` 单独捕获返回 `status: model_mismatch`，不阻断维护链

### 4. 主动提醒引擎（新增 `reminders` 命令）

- 新模块 `analysis/reminders.py`（`ReminderEngine`）：基于文件名 YYYYMMDD 日期 + mtime，零 LLM 成本检测三类信号：
  - **栏目断供**（category_inactive）：SOURCE 分类目录超阈值无更新（默认 30 天，会议纪要/周报类收紧至 10-14 天）
  - **成员周报缺失**（weekly_report_missing）：活跃成员（45 天窗口内）周报断档 ≥14 天
  - **项目停滞**（project_stalled）：项目 Wiki 页 source_fingerprint 引用源文档全部 ≥21 天未更新
- daily-start 集成为第 8 步（静默失败）；`--pretty` 输出按类型分组；阈值 `app.json` 的 `reminders` 段可配

### 顺带修复（3 处预存问题）

- **PDF 切块 0 chunk**：`_chunk_document` 为生成器函数，PDF 分支 `return _chunk_pdf_document(...)` 的返回值被生成器协议丢弃 → 改为普通函数返回可迭代对象（默认配置只扫 `*.md` 故未暴露）
- **`AppConfig` 未声明字段被 Pydantic 丢弃**：`retrieval` / `organization` 段静默失效，`app.json` 中 RRF 权重配置从未生效 → 显式声明 `retrieval` / `organization` / `reminders` 三字段
- **hash 索引条目不更新**（见第 2 项配套修复）

### 测试

- 新增 `test_source_fingerprint.py`（17 用例）+ `test_reminders.py`（19 用例）
- `test_chunker_pure.py` +13（重叠）、`test_vector_index.py` +5（模型守卫/强制重建）
- 合计 +54 用例

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.19.25 | **3.19.26** | 四项能力优化 + 3 处修复 |
| 协议版本 | 3.12 | **3.13** | 新增 reminders 命令 + --force-rebuild 参数 |
| 数据版本 | app 3.3 / data_source 3.2 | **app 3.4 / data_source 3.3** | 新增 reminders 段 / chunk_overlap_chars 字段 |

---

## v3.19.25 (2026-07-28)

iris-feed 简报质量跃升 — 两阶段 LLM 架构 + 去截断 + Prompt 重写 + 结构化输出增强。

### 合并 0728-beta

- **输入截断保护**：`_topic_detector.py` 新增 `_fill_fallback_summary` 兜底方法，Phase 2 失败时从原始消息生成简单摘要
- **清理死代码**：移除已废弃的 `_llm_detect` / `_parse_llm_response` 方法（已被 Phase 1/2 架构替代）
- **max_tokens 统一提升至 8192**：Phase 1 和 Phase 2 均使用 8192 max_tokens

### L1 快速修复（5 项）

- **讨论要点编号修正**：`_brief_generator.py` 模板讨论要点由硬编码 `1.` 改为 `enumerate` 正确编号
- **去除消息截断**：`_topic_detector.py` 去掉 `m.content[:200]`，充分利用 deepseek-v4-flash 1M 上下文窗口，传入完整消息原文
- **放宽输入限制**：`msgs[:10]`→`msgs[:20]`，`max_tokens` 4096→8192
- **Prompt 增强**：Phase 2 Prompt 要求 quotes 3-5 条、discussion_points 含 `{point, detail, speaker}` 结构化字段、decisions 含 `{content, by}`
- **简报兜底优化**：`_build_quotes_section()` 合并 LLM 输出 quotes + 原始消息采样（不足 3 条时补充），`key_status` 为空时自动从首条 discussion_point 提取

### L2 两阶段 LLM 架构

- **Phase 1（轻量）**：规则分割 → LLM 检测话题边界/合并跨群/命名/OKR 匹配，Prompt 精简不输出深度摘要
- **Phase 2（深度）**：逐话题并发调用 LLM（ThreadPoolExecutor, max_workers=4），传入该话题全部消息原文，独立生成摘要/讨论要点/决策/引述
- **架构收益**：每个话题得到独立深度分析，摘要质量显著提升；Phase 2 失败时自动兜底
- **并发设计**：多话题 Phase 2 并行执行，延迟近似单次调用

### Bug 修复

- **`_extract_json` 嵌套数组误提取**：JSON 对象内含数组字段时，`{` 优先匹配先于 `[`，修复 Phase 2 输出被截断导致解析失败的问题

### 测试

- `test_feed_topic_detector.py` 新增 10 用例：Phase 1 集成/Phase 2 深度摘要/并发执行/兜底降级/`_extract_json` 边界/`_parse_json_safe` 安全解析
- feed 测试 193→202，全量 2,154→2,162

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.19.24 | **3.19.25** | iris-feed 简报质量跃升 |
| 协议版本 | 3.12 | **3.12** | 未新增/变更 CLI 命令 |

### 涉及文件

3 文件 · +675 / -255 行

---

## v3.19.24 (2026-07-28)

全量代码质量加固（第二轮）— P0 基础设施修复 + P1 安全/测试/工具链升级 + P2 日志与风格统一。

### P0 基础设施修复（3 项）

- **Dockerfile 修复**：调整 COPY 顺序（先 src 后 pip install），添加 libmupdf-dev 系统依赖，使用 constraints.txt 确保可复现构建；新增 `.dockerignore`
- **CI 安全门禁**：移除 pip-audit 的 `continue-on-error: true`，安全漏洞将阻断 CI；修复 libmupdf-dev 安装吞错（`|| true`）
- **硬编码路径消除**：新增 `resolve_data_path()` 工具函数（`utils/paths.py`），修复 `_wiki.py` 中 8 处 CWD-relative 路径为基于项目根的绝对路径

### P1 安全/测试/工具链升级（4 项）

- **feed 测试补齐**：新增 6 个测试文件（193 用例），覆盖 config/filter/detector/okr/brief/pipeline；单元测试 947→1,124
- **API 密钥 SecretStr 保护**：`ModelItem.api_key` + `EmbeddingConfig.api_key` 改用 Pydantic `SecretStr`，`BaseConfigModel` 自动解包以兼容现有 dict-style 访问
- **pre-commit 工具链升级**：ruff v0.4.0→v0.11.0；新增 8 个基础钩子（trailing-whitespace/end-of-file-fixer/check-yaml/check-json/check-toml/check-merge-conflict/detect-private-key/check-added-large-files）
- **静默异常修复**：`_biweekly_cache.py` 3 处静默 `pass` 改为 `logger.warning`；`_biweekly_collector.py` 日期解析保留（正常 fallback 控制流）

### P2 日志与风格统一（3 项）

- **logger.exception 替换**：provider/model_manager/storage/agent_adapter/analysis 核心模块 10+ 处 `logger.warning`→`logger.exception`，自动包含 traceback
- **导入风格统一**：feed/trello/complex_input 三处绝对导入→相对导入；output/utils 的 `__init__.py` 补充公共导出
- **CI 覆盖率合并**：单元测试 + 集成测试覆盖率通过 `--cov-append` 合并；setup-python 内置 pip 缓存

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.19.23 | **3.19.24** | 全量代码质量加固（第二轮） |
| 协议版本 | 3.12 | **3.12** | 未新增/变更 CLI 命令 |

### 涉及文件

16 文件 · +340 / -40 行

---

## v3.19.23 (2026-07-28)

全量代码质量加固 — P0 严重缺陷修复 + 架构债务消除 + 测试补齐 + 性能优化。

### P0 严重缺陷修复（9 项）

- **原子写入统一**（4 处）：`feed/_cursor_tracker.py`、`feed/feed_config.py`（×2）、`feed_config.save_pending` 改用 `atomic_write_json()` + `FileLock`
- **读/写加锁统一**（3 处）：`memory/long_term.py`（UserProfile + Correction 双 Store）、`feishu/_shared.py` `load_dedup_index`，新增 `_load_unlocked()` 避免可重入死锁
- **`get_active_model_info` api_key 修复**：`llm/model_manager.py` 传入 `sensitive=True`
- **会议转录日期修复**：`app/transcribe_meeting/pipeline.py:62` 补回缺失的 `date_part =` 赋值
- **PDF finally 块修复**（3 处）：`ingest/scanner.py`、`complex_input/pdf_adapter.py`（×2）预声明 `doc = None`
- **向量索引防御**：`retrieval/vector_index.py` 增加 `StopIteration` 保护
- **消息获取错误传播**：`feed/_chat_fetcher.py` 新增 `ChatFetchError` 异常，区分 API 失败与无新消息
- **用量统计空 rows 防御**：`app/cli/_handlers/_system.py` 提前 `if not rows` 检查

### P1 架构债务消除

- **ASR 向后兼容存根删除**（5 文件）：`wiki/asr_formatter.py`、`asr_hotwords.py`、`asr_prompt_optimizer.py`、`asr_version.py`、`term_extractor.py`
- **工具函数去重**：`_extract_json_object` 从 `qa/memory_updater.py` 和 `memory/session_miner.py` 统一迁移到 `utils/llm_parsing.py`
- **ConfigBundle 迁移**：工厂函数 `ConfigBundle()` → `make_config_bundle()`，新增 `ConfigBundle = ConfigBundleV2` 类型别名
- **`pyproject.toml` 清理**：移除已删除文件 `term_extractor.py` 的 E402 豁免

### 测试与性能

- **feed 包测试补齐**：新增 `tests/unit/test_feed_core.py`（16 用例），覆盖 MessageFilter / CursorTracker / PipelineResult
- **deep_eval 并发化**：`AccuracyVerifier.verify()` 新增 `max_workers` 参数（默认 5），ThreadPoolExecutor 并行调用 LLM
- **BM25 参数配置化**：`retrieval/searcher.py` 支持通过 `app.json` → `retrieval.bm25.{k1,b}` 配置
- **覆盖率阈值**：`fail_under` 53 → 55（实际 56.68%）

### 代码质量

- **死代码删除**（2 处）：`llm/provider.py` `_is_deepseek_thinking_model`、`config/workspace.py:138`
- **Import 规范化**（4 处）：`_feishu_bridge.py`、`_dispatcher.py`、`client.py`、`chat_digest.py` 函数体内 import 移至模块顶部
- **Formatter key 名修复**：`output/formatter.py` `stage1_output` → `stage1_prompt`

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.19.22 | **3.19.23** | 全量代码质量加固 |
| 协议版本 | 3.12 | **3.12** | 未新增/变更 CLI 命令 |

### 涉及文件

39 文件 · +263 / -225 行

---

## v3.19.22 (2026-07-27)

iris-feed OKR 语义匹配 + LLM deadline 实时超时控制 + ASR 独立熔断器。

### 新增功能

- **OKR 语义匹配** (`src/iris/feed/_okr_loader.py`)：从 `SOURCE/01-目标管理` 解析 OKR 文档，注入话题检测 Prompt，LLM 自动匹配话题与 KR，简报模板新增「OKR 关联」章节
- **LLM deadline 实时超时控制**：`provider.py` 新增 `deadline` 参数，降级链每次模型尝试前检查剩余时间，超出即中止；`service.py` deadline 场景跳过缓存；`set_circuit_breaker()` 支持独立熔断器实例
- **ASR 独立熔断器 + 超时配置**：`AsrCorrector` 新增 `llm_timeout_ms` 参数（默认 8000ms），通过 `asr_profiles.json` 的 `llm.timeout_ms` 配置；ASR 实时场景使用独立熔断器（threshold=2, reset=30s）；`_shutdown_requested` 安全关闭事件
- **feed-list OKR 标签语义化**：`feed-list` 命令展示 OKR 标签时自动解析为实际描述

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.19.21 | **3.19.22** | OKR 集成 + LLM/ASR 增强 |
| 协议版本 | 3.12 | **3.12** | 未新增 CLI 命令 |

### 涉及文件

| 文件 | 变更 |
|------|------|
| `src/iris/feed/_okr_loader.py` | 新增（OKR 解析） |
| `src/iris/feed/__init__.py` | 修改（导出 OKRLoader） |
| `src/iris/feed/_topic_detector.py` | 修改（OKR 上下文注入 + 匹配输出） |
| `src/iris/feed/_brief_generator.py` | 修改（OKR 关联章节） |
| `src/iris/feed/feed_pipeline.py` | 修改（接入 OKRLoader） |
| `src/iris/app/cli/_handlers/_feed.py` | 修改（feed-list OKR 语义化） |
| `src/iris/llm/provider.py` | 修改（deadline 参数 + 独立熔断器） |
| `src/iris/llm/service.py` | 修改（deadline 传播 + 缓存旁路） |
| `src/iris/wiki/asr/corrector.py` | 修改（llm_timeout_ms + shutdown） |
| `src/iris/app/cli/_handlers/_wiki.py` | 修改（ASR 超时配置 + 独立熔断器） |
| `pyproject.toml` | 修改（产品版本 3.19.21→3.19.22） |
| `CLAUDE.md` · `CHANGELOG.md` · `README.md` | 修改（文档更新） |

---

## v3.19.21 (2026-07-27)

信息汇聚管道 `iris-feed` — 飞书聊天记录自动挖掘话题生成简报。

### 新增功能

- **信息汇聚管道** (`src/iris/feed/`，11 文件，~2,200 行)：完整的信息汇聚 Pipeline
  - `FeedPipeline` — 7 步编排（消息获取→噪音过滤→话题检测→简报生成→分发）
  - `ChatFetcher` — 遍历关注会话拉取飞书消息，支持增量游标追踪
  - `MessageFilter` — 7 条规则噪音过滤（红包/接龙/过短/系统消息/纯转发）
  - `TopicDetector` — 两步话题检测（30min 规则窗口分割 + LLM 跨群聚合）
  - `BriefGenerator` — 话题简报 Markdown 生成，归档到 `SOURCE/09-工作简报/YYYYMM/`
  - `Dispatcher` — 分发器（auto_import 直接入库 / confirm 待确认队列）
  - `FeishuBridge` — 飞书 API 桥接层（消息搜索/群聊发现/Bot 单聊推送）
  - `CursorTracker` — 分群游标追踪（支持增量拉取）
- **9 个 CLI 命令**：`feed-setup`（交互式配置向导，自动发现群聊）· `feed-list`/`feed-add`/`feed-remove`/`feed-config`（配置管理）· `feed-collect`（主命令：执行信息汇聚）· `feed-pending`/`feed-confirm`/`feed-ignore`（待确认话题管理）
- **飞书 Bot 单聊推送通道**：Bot 直接发送单聊消息通知（`+messages-send --as bot --user-id`），无需建群
- **配置体系**：`config/feeds.json`（运行时，gitignored）· `config/feeds.json.example`（版本控制示例）

### 版本升级

| 层 | 旧版本 | 新版本 | 理由 |
|------|:---:|:---:|------|
| 产品版本 | 3.19.20 | **3.19.21** | 新增 feed 模块 + CLI 命令 |
| 协议版本 | 3.11 | **3.12** | CLI 命令集新增 9 个命令 |

### 涉及文件

| 文件 | 变更 |
|------|------|
| `src/iris/feed/__init__.py` | 新增 |
| `src/iris/feed/_types.py` | 新增（7 类型定义） |
| `src/iris/feed/feed_config.py` | 新增（配置加载/管理） |
| `src/iris/feed/_feishu_bridge.py` | 新增（飞书 API 桥接） |
| `src/iris/feed/_cursor_tracker.py` | 新增（游标追踪） |
| `src/iris/feed/_chat_fetcher.py` | 新增（消息获取） |
| `src/iris/feed/_message_filter.py` | 新增（噪音过滤） |
| `src/iris/feed/_topic_detector.py` | 新增（话题检测） |
| `src/iris/feed/_brief_generator.py` | 新增（简报生成） |
| `src/iris/feed/_dispatcher.py` | 新增（分发） |
| `src/iris/feed/feed_pipeline.py` | 新增（Pipeline 编排） |
| `src/iris/app/cli/_handlers/_feed.py` | 新增（CLI 处理器） |
| `config/feeds.json.example` | 新增（配置示例） |
| `src/iris/app/cli/handlers.py` | 修改（注册 FEED_HANDLERS） |
| `src/iris/app/_cli_main.py` | 修改（注册 9 命令 + CLI 参数） |
| `src/iris/__init__.py` | 修改（协议版本 3.11→3.12） |
| `pyproject.toml` | 修改（产品版本 3.19.20→3.19.21） |
| `CLAUDE.md` · `CHANGELOG.md` | 修改（文档更新） |
| `DESIGN-feed-collect.md` | 新增（技术设计文档） |

---

## v3.19.20 (2026-07-24)

ASR 反馈反向优化引擎 — feedback.jsonl 驱动词典自动进化。

### 新增功能

- **反馈反向优化引擎** (`feedback.py` +252/-1)：三个核心函数实现用户反馈自动学习闭环
  - `find_zombie_rules()` — 检测从未命中的僵尸替换规则（≥50 条反馈样本触发）
  - `build_feedback_recommendations()` — 三维度分析：淘汰僵尸规则 / LLM 发现提升为词典（阈值≥3次）/ 补充热词
  - `apply_feedback_optimizations()` — 原地应用优化建议（移除僵尸 / 添加提升 / 追加热词）
- **build-asr-prompt 集成** (`_wiki.py` +51)：构建 ASR Prompt 时自动运行反馈反向优化，≥50 条反馈记录时输出优化摘要，异常时不影响主流程
- **LLM 调用失败日志增强** (`provider.py` +2)：模型调用失败/异常时增加 warning 日志便于排障

### 涉及文件

| 文件 | 变更 |
|------|------|
| `src/iris/wiki/asr/feedback.py` | +252 / -1 |
| `src/iris/app/cli/_handlers/_wiki.py` | +51 |
| `src/iris/wiki/asr/__init__.py` | +4（导出新函数） |
| `src/iris/llm/provider.py` | +2（warning 日志） |
| `tests/unit/test_asr_feedback.py` | 新增，31 用例 |

---

## v3.19.19 (2026-07-23)

测试覆盖全面优化 — 90 新增用例，P1 三模块接近全绿，P2 纯逻辑增强。

### 新增测试文件

| 文件 | 用例 | 覆盖模块 | 覆盖率变化 |
|------|:---:|------|:---:|
| `tests/unit/test_graph_engine_fallback.py` | 21 | `_graph_engine.py` | 61% → **97%** |
| `tests/unit/test_asr_formatter.py` | 26 | `asr/formatter.py` | 59% → **98%** |
| `tests/unit/test_navigation_pure.py` | 23 | `navigation.py` | 67% → **70%** |
| `tests/unit/test_wiki_generator_pure.py` | +8 | `generator.py` | 纯逻辑增强 |

### P1：核心模块覆盖率提升

- **`_graph_engine.py`（61%→97%，+21 用例）**：完整覆盖纯 Python 回退路径（build / neighbors / find_path / orphans / bridges / degree_stats），确保 NetworkX 不可用时功能不退化
- **`asr/formatter.py`（59%→98%，+26 用例）**：覆盖 `format_hotwords_file`（去重逻辑）、`format_replace_dict`（高危过滤/长度限制/去重/上限截断）、`render_asr_prompt`（standard/compact 双格式 + domain_term/concept/project 各类型块）
- **`navigation.py`（67%→70%，+23 用例）**：覆盖 `_is_wiki_broken_link` 全部 7 个豁免分支（源文档引用/噪音/技术术语/外部概念/精确/模糊/前缀/归一化/字符序列/数字修复）、`append_changelog`、`_atomic_write`、`_char_sequence_match`

### P2：generator 纯逻辑增强

- `_extract_wiki_content` 5 个 heuristic 分支全覆盖
- `_render_with_fallback` 模板/fallback 双路径
- `_validate_update_output` frontmatter 恢复/title 修复/不可恢复回退/结尾代码块剥离
- `check_reference_quality` 引用计数/描述判定/无引用处理

### 涉及文件

| 文件 | 改动 |
|------|------|
| `tests/unit/test_graph_engine_fallback.py` | 新增，21 用例 |
| `tests/unit/test_asr_formatter.py` | 新增，26 用例 |
| `tests/unit/test_navigation_pure.py` | 新增，23 用例 |
| `tests/unit/test_wiki_generator_pure.py` | +8 用例（18→26） |

---


## v3.19.18 (2026-07-23)


知识库质量全面加固 + LLM 用量追踪体系完善。

### P1 修复

- **wiki-pipeline 已有页面检测** — 分离检测与过滤逻辑，始终运行 `_check_existing_wiki()`；文件名匹配改用 `slugify_title()` 对齐页面创建时的命名规则。检测准确率 0% → 85%。
- **知识图谱 LLM 关系边** — `build-graph --full` 批量提取语义关系，新增 986 条 LLM 边（负责/使用/属于/依赖），图总边数 1,098 → 2,161。

### P2 修复

- **断链清零** — 移除 5 个页面中 12 处无效 wikilink，wiki-lint `broken_count` 11 → 0。
- **零出链清零** — 27 个页面添加关联链接（人物→组织架构/团队，概念→相关领域），wiki-lint `zero_outbound_count` 27 → 0。
- **重复检测优化** — 跳过正文 < 200 字符的页面（空模板），重复对 920 → 676，相似度从 1.0 降至 0.6-0.8。

### P3 修复

- **wiki-lint 索引质量检测** — `_discover_index_paths()` 自动发现数据源，替代硬编码 `main_source`。修复后 `source_documents: 0 → 743`、`vector_index_exists: false → true`。
- **向量索引首次构建** — 9,019 chunks × 1,024 维，text-embedding-v3 (百炼)，39.7 MB。

### 用量追踪体系

- **Embedding 用量纳入统计** — `TextEmbedder._record_usage()` 调用 `UsageTracker`，text-embedding-v3 调用单独统计。
- **CLI / Skill 来源标记** — `api_calls` 表新增 `source` 列，`--call-source` CLI 参数 + `IRIS_CALL_SOURCE` 环境变量，Provider 层兜底读取。9 个项目 Skill 已更新传递 `--call-source skill`。

### 代码变更

| 文件 | 变更 |
|------|------|
| `src/iris/wiki/discovery.py` | `_check_existing_wiki()` + `slugify_title()` 匹配 |
| `src/iris/wiki/navigation.py` | `_discover_index_paths()` + 重复检测跳过短页面 |
| `src/iris/llm/usage_tracker.py` | `source` 列 + 迁移 + `record()` 参数 |
| `src/iris/llm/provider.py` | `source` 提取（route_context → env var 兜底） |
| `src/iris/llm/service.py` | `_source` 字段 + `generate()` 注入 |
| `src/iris/retrieval/embedder.py` | `_record_usage()` 用量追踪 + `_infer_provider()` |
| `src/iris/app/_cli_main.py` | `--call-source` 参数 |
| `.claude/skills/*/SKILL.md` (×9) | 注入 `--call-source skill` |
| Wiki 页面 (×32) | 断链移除 + 零出链修复 + 反向链接 + 新页面 |

### 知识库最终状态

| 指标 | 值 |
|------|:--|
| Wiki 页面 | 219 |
| 断链 | 0 |
| 零出链 | 0 |
| 向量索引 | 9,019 条 (100%) |
| 知识图谱 | 2,161 边 (1,175 wikilink + 986 LLM) |
| LLM 用量 | 644 次 / ~3.78M tokens (CLI + Skill 分离) |

---

代码质量全面加固 — P0 静默异常修复 + P1 重复代码消除 + P2 工程基础设施增强 + P3 代码优化 / 测试精化。

### 修复 (P0)

- **`memory/session.py` 静默吞异常**：旧路径迁移失败时 `except OSError: pass` 无日志，改为 `logger.warning` 记录源路径、目标路径和异常信息

### 重构 (P1)

- **`llm/service.py` 缓存检查 DRY**：`generate()` 和 `generate_async()` 中 ~14 行重复的缓存检查逻辑提取为 `_check_cache()` 私有方法
- **`analysis/service.py` 死代码清理**：删除 9 个仅委托 `BiweeklyCollector` 的向后兼容方法（−48 行），包括 `_load_op_document` / `_load_recent_biweeklies` / `_load_previous_biweekly` / `_collect_recent_files` / `_extract_date_from_path` / `_extract_date_from_frontmatter` / `_extract_person_from_filename` / `_build_citation_label` / `_stage4_assemble_and_review`
- **`wiki/generator.py` prompt 构建 DRY**：4 处 `_build_*_prompt` 方法的模板→fallback 重复模式提取为 `_render_with_fallback()` 辅助方法

### 新增 (P2)

- **依赖安全审计**：Makefile 新增 `audit` 目标（`pip-audit`），CI 新增对应步骤（`continue-on-error: true`）
- **git tag 发布流程**：`CONTRIBUTING.md` 新增发布流程文档（7 步：更新版本号 → changelog → commit → tag → push → GitHub Release）
- **依赖约束文件**：新增 `constraints.txt`（PyMuPDF / python-docx / numpy / pydantic / networkx / httpx / beautifulsoup4），Makefile / CI 使用 `-c constraints.txt` 确保可复现构建

### 优化 (P3)

- **测试分类精化**：`tests/conftest.py` 新增 14 个顶层纯逻辑文件白名单，正确标记为 `unit`（单元测试 526→794）
- **lint 清理**：修复 `_wiki.py` 中 3 个预存 lint 问题（2 处 f-string 无占位符 + 1 处 E741 歧义变量名 `l`）

### 测试

- `test_biweekly_files.py` 引用更新：`AnalysisReportService` → `BiweeklyCollector`（适配已删除的委托方法）

### 涉及文件

| 文件 | 改动 |
|------|------|
| `src/iris/memory/session.py` | P0: +6/-2 |
| `src/iris/llm/service.py` | P1: 提取 `_check_cache()` |
| `src/iris/analysis/service.py` | P1: −48 行死代码 |
| `src/iris/wiki/generator.py` | P1: 提取 `_render_with_fallback()` |
| `Makefile` | P2: +audit +install-dev |
| `.github/workflows/ci.yml` | P2: +pip-audit +constraints |
| `CONTRIBUTING.md` | P2: +发布流程 +audit |
| `constraints.txt` | P2: 新增，15 行 |
| `tests/conftest.py` | P3: +14 文件白名单 |
| `tests/test_biweekly_files.py` | P3: 引用更新 |
| `src/iris/app/cli/_handlers/_wiki.py` | lint: f-string + E741 |

---


## v3.19.17 (2026-07-22)

SOURCE 目录按月/年归档 — 9 目录 3 级归档策略。

### 归档策略

`config/source_archive.json` 定义 3 种归档模式：

| 模式 | 目录 | 子目录格式 |
|:--|:--|:--|
| **yearly** | 01-目标管理、02-部门管理、03-方案报告、06-我的周报、08-参考资料 | `{category}/{YYYY}/` |
| **monthly** | 04-讨论思考、05-会议纪要、07-成员周报、09-工作简报 | `{category}/{YYYYMM}/` |
| **flat** | 其他（默认） | `{category}/` |

### 新增

- **工具函数** `resolve_source_archive_path()` 在 `utils/paths.py` — 按文件名 `YYYYMMDD-` 前缀和配置自动计算归档路径，创建子目录
- **迁移脚本** `scripts/source_monthly_archive.py` — 一次性搬迁，已运行将 740 文件按规则归档

### 修改

所有向 SOURCE 写入的模块均改为归档路径：
- `feishu/_shared.py` — `resolve_source_sub_dir()` 支持 filename 参数
- `feishu/doc_convert.py` / `chat_digest.py` — 飞书文档/聊天提炼归档
- `transcribe_meeting/pipeline.py` — 会议转录归档
- `cli/_handlers/_content.py` — 双周报归档
- `analysis/_biweekly_collector.py` — 3 处 `glob` → `rglob` 递归读取子目录

### 涉及文件

| 文件 | 改动 |
|------|------|
| `config/source_archive.json.example` | 新增归档配置模板 |
| `src/iris/utils/paths.py` | 新增 `resolve_source_archive_path()` + `get_archive_mode()` |
| `src/iris/feishu/_shared.py` | `resolve_source_sub_dir()` 增加 filename 参数 |
| `src/iris/feishu/doc_convert.py` | 归档路径调用新函数 |
| `src/iris/feishu/chat_digest.py` | 同上 |
| `src/iris/app/transcribe_meeting/pipeline.py` | 会议转录归档路径 |
| `src/iris/app/cli/_handlers/_content.py` | 双周报归档路径 |
| `src/iris/analysis/_biweekly_collector.py` | 3 处 glob → rglob 递归读取 |
| `scripts/source_monthly_archive.py` | 新增迁移脚本 |

---

## v3.19.16 (2026-07-22)

合并 0722-alpha — 多 Agent 并发安全 + iris-okr-check Skill。

### 新增 (A)

- **iris-okr-check Skill**：新增第 9 个项目级 Skill，支持从 SOURCE 数据源提取近两周会议纪要/讨论思考/工作简报/成员周报，对照 OKR 原文逐 KR 提炼进展小结与支撑细则，输出结构化检查记录并归档到 `01-目标管理/`。提供 Tier 象限划分可视化输出、交叉检查等完整能力。

### 涉及文件

| 文件 | 改动 |
|------|------|
| `.claude/skills/iris-okr-check/SKILL.md` | 新增 skill 定义（版本 1.0.0）|

## v3.19.15 (2026-07-22)

多 Agent 并发安全 — 三层防护体系全面实施。

### P0：FileLock 推广 + SQLite WAL

**步骤 1** — RMW 模式 FileLock 加锁（6 文件）：
- `ingest/chunker.py`: `write_hash_index()` RMW 段整体包裹 FileLock
- `memory/session.py`: `save_interaction()` 全文程锁内完成
- `memory/working.py`: `update()` 全文程锁内完成
- `feishu/_shared.py`: `save_dedup_index()` 写入前 FileLock 保护
- `wiki/graph.py`: `save()` nodes+edges 双文件同锁内原子写入
- `retrieval/vector_index.py`: `save()` vectors/ids/meta 三文件锁内写入

**步骤 2** — SQLite WAL + 重试（1 文件）：
- `llm/usage_tracker.py`: WAL 模式 + busy_timeout=5000 + 3 次指数退避重试
- `record()` 静默吞异常 → warning 级别日志

### P1：功能加固

**步骤 3** — 双周报缓存加锁（2 文件）：
- `analysis/_biweekly_cache.py`: `flush_brief_index()` FileLock
- `analysis/service.py`: `_save_brief_index()` 静态方法同上

**步骤 4** — Agent 记忆隔离（3 文件）：
- `utils/paths.py`: 新增 `get_agent_data_dir()`，按 `IRIS_AGENT_ID` 分目录 + 安全过滤
- `memory/session.py`: `SessionMemoryStore` agent 专属路径 + 旧数据自动迁移
- `memory/working.py`: `WorkingContextStore` agent 专属路径 + 旧数据自动迁移

**步骤 5** — 日志归档 TOCTOU 修复（1 文件）：
- `utils/logging.py`: 归档判断用独立 fcntl 锁，先加锁再检查文件大小

### P2：体验优化

**步骤 6** — 进程注册表（3 文件）：
- `core/locks.py`: 新增 `ProcessRegistry`，PID 文件 + stale 检测
- `wiki/asr/corrector.py`: ASR 守护进程启动注册
- `app/cli/_handlers/_data.py`: watch 守护进程启动注册

**步骤 7** — 追加写 fcntl 保护（1 文件）：
- `wiki/asr/feedback.py`: JSONL 追加写前 fcntl.LOCK_EX

### 代码审查

3 轮审查修复 7 个问题：冗余 trim / 未使用 import / 死代码 / Agent 隔离路径迁移 / 两遍调用重复等。

### 设计文档

新增 `docs/multi-agent-concurrency-design.md` 完整方案文档（含适用/不适用场景边界速查表）。

---

## v3.19.14 (2026-07-22)

记忆自动更新引擎 — 从手动/半自动演进为全自动化记忆学习系统。

### Phase 1：LLM 双通道记忆提取器

双通道架构：正则快速匹配（显式命令"记住"/"纠正"/"我喜欢"，免费毫秒级）+ LLM 深度分析（完整对话上下文，轻量模型按需触发）。

每次 `iris ask` 分两遍调用：第一遍仅问题文本走正则通道，第二遍等回答生成后走 LLM 深度通道，两遍结果自动合并去重。

### Phase 2：会话模式挖掘器

新增 `src/iris/memory/session_miner.py` — `SessionPatternMiner`，用 LLM 从多次会话中识别高频主题/偏好模式/新事实，自动晋升为长期记忆。

触发机制：Q&A 结束时懒检查（距上次 ≥24h 自动触发，后台线程不阻塞响应）+ daily-start 兜底。

### Phase 3：全自治生命周期

- 老化归档：daily-start 默认自动执行，memory-maintenance 默认 `--auto-age`
- 纠正自动确认：LLM 提取 ≥5 次一致 → `[AUTO-CONFIRMED]`，正则提取 ≥5 次 → 同
- 纠正自动裁决：正则提取检测"不是 X，而是 Y"模式，preferred 指向被否定值时自动修正为 affirmed
- 写入时自动压缩：`UserProfileMemoryStore._save()` 所有路径统一 trim（likes ≤15、dislikes ≤15、styles ≤15、notes ≤20）
- 写时压缩由 `_trim_list()` 实现：去重 + FIFO 保留最新条目

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/iris/memory/session_miner.py` | 会话模式挖掘器（~300 行） |
| `templates/prompt/memory_extract.md` | LLM 记忆提取 prompt 模板 |
| `tests/unit/test_session_miner.py` | 会话挖掘单元测试（15 用例） |
| `tests/unit/test_memory_updater_llm.py` | 记忆更新器 LLM 通道测试（14 用例） |
| `docs/memory-auto-update-design.md` | 记忆自动更新完整设计文档 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/iris/qa/memory_updater.py` | 重写：双通道架构 + 会话挖掘懒触发 + 冲突自动解决（~500 行） |
| `src/iris/qa/service.py` | 两遍记忆更新：第一遍正则（answer=None），第二遍 LLM（skip_regex=True） |
| `src/iris/qa/helpers.py` | 新增 `_merge_updates()` 去重合并 |
| `src/iris/memory/long_term.py` | `_save()` 写入时自动压缩；新增 `_trim_list()` |
| `src/iris/memory/__init__.py` | 导出 `SessionPatternMiner` |
| `src/iris/app/cli/_handlers/_system.py` | daily-start：自动老化 + 会话挖掘兜底 + session_mine 输出 |
| `CLAUDE.md` | 记忆系统文档重写：5→6 子模块，新增自动更新引擎章节 |
| `CHANGELOG.md` | 本条目 |
| `pyproject.toml` | 版本 3.19.13 → 3.19.14 |

### 测试

新增 29 个用例，全量 1,858 全部通过。

### 代码审查

第二轮审查发现并修复 7 个问题：regex 通道两遍重复执行（`skip_regex` 参数）、JSON 回退路径缺类型检查、`_promote` 批量晋升优化、会话挖掘阻塞 Q&A 响应（后台线程）、`_extract_json_object()` 括号计数替代脆弱正则、`_apply_extracted` 单次 load-save、`_auto_resolve_conflict` 分支处理 LLM/正则。

---

## v3.19.13 (2026-07-21)

ASR shutdown SIGINT 保护 — 清理流程统一信号屏蔽。

### 问题

用户按 `Ctrl+C` 停止 ASR 引擎时，`finally` 块的清理流程分两步执行：
1. `_hotkey_monitor.stop()` — 含 `thread.join(timeout=3.0)`
2. `_shutdown_executor()` — 关闭线程池 + 取消 pending 任务

若用户在 `join()` 阻塞期间再次按 `Ctrl+C`，Python 在 `join()` 处抛出 `KeyboardInterrupt`，直接跳过 executor 关闭，残留线程在 atexit 阶段再次触发异常。

### 修复

- 将 SIGINT 屏蔽从 `_shutdown_executor()` 内部提升到 `run_forever()` 的 `finally` 块顶层
- 入口统一 `signal(SIGINT, SIG_IGN)`，保护 `hotkey_monitor.stop()` + `_shutdown_executor()` 整个序列
- `finally` 中 `signal(SIGINT, orig_handler)` 保证恢复原始处理器
- `_shutdown_executor()` 简化：移除冗余 SIGINT 处理（由调用方统一管理）

### 涉及文件

| 文件 | 改动 |
|------|------|
| `corrector.py` | `run_forever()` finally 块统一 SIGINT 屏蔽；`_shutdown_executor()` 简化 |

---

## v3.19.12 (2026-07-21)

ASR 引擎 LLM 推理模式管控 + 路由路径修复 + 上下文效果评估。

### LLM 思考模式关闭 (P0)

- **问题**：`deepseek-v4-flash` 默认开启 thinking 模式，ASR 校正时输出冗长 CoT 推理过程（数百字），导致耗时 2-5 秒且频繁触发「输出超长降级为词典结果」
- **修复**：`AsrCorrector._correct_llm()` 两处 LLM 调用添加 `extra_body={"thinking": {"type": "disabled"}}`（LLMService 路径 + Provider 回退路径）
- **相关修复**：`EnvironmentConfiguredLLMProvider.generate()` 路由路径 `_try_call` 闭包漏传 `extra_body`，导致 thinking 关闭参数被静默丢弃 → 补传 `extra_body=request_data.extra_body`

### 上下文 A/B 对比模式 (新特性)

- `AsrCorrector` 新增 `context_ab` 参数 + CLI `--context-ab` 开关
- 开启后：上下文非空时，每句跑两次 LLM（带/不带上下文），对比差异并记录到 feedback JSONL
- `_correct_llm()` 新增 `force_no_context` 参数，支持强制跳过上下文注入
- `AsrCorrection` / `_append_feedback_jsonl` 支持 `context_ab` 字段序列化

### 涉及文件

| 文件 | 改动 |
|------|------|
| `corrector.py` | LLM 调用补 `extra_body`、`_record`/`_llm_refine` 支持 A/B 对比、`_append_feedback_jsonl` 序列化 |
| `provider.py` | 路由路径 `_try_call` 补传 `extra_body` |
| `_types.py` | `AsrCorrection` 新增 `context_ab` 字段 |
| `feedback.py` | `save_correction` 序列化 `context_ab` |
| `_cli_main.py` | 新增 `--context-ab` 参数 |
| `_wiki.py` | handler 读取并传入 `context_ab` |

---

## v3.19.11 (2026-07-21)

五大方向全面优化 — 测试加固 + 架构演进 + 代码质量提升。

### 方向 1: 测试覆盖率提升（+76 新测试，7 文件）
- `tests/unit/test_clipboard_io.py`: mock subprocess，`_clipboard_io.py` 18%→95%
- `tests/unit/test_memory_updater.py`: mock memory stores，`memory_updater.py` 42%→82%
- `tests/unit/test_trello_client_pure.py`: IP/DNS 纯函数测试
- `tests/unit/test_wiki_discovery.py`: 标题/术语检测纯函数
- `tests/unit/test_person_enricher.py`: mock FeishuClient 测试
- `tests/unit/test_wiki_generator_pure.py`: 数据类/初始化测试
- `tests/unit/test_asr_corrector.py`: 扩展 LLM mock + 边界用例

### 方向 2: analysis/service.py God Class 拆解
- `_biweekly_helpers.py` 提取 `DEFAULT_STYLE_GUIDE` + `assemble_biweekly_sections()` 纯函数
- `service.py` `_stage4a_assemble` 委托给纯函数，`_DEFAULT_STYLE_GUIDE` 委托常量

### 方向 3: LLM 调用统一网关
- `LLMService.generate()` 新增 `extra_body`/`use_cache` 参数
- `_make_cache_key()` 加入 `temperature` 区分不同温度的缓存键
- `AsrCorrector` 支持 `set_llm_service()`，优先使用 LLMService（享受缓存/熔断器）
- CLI handler 自动注入 LLMService

### 方向 4: 缓存层统一抽象
- 新增 `core/memory_cache.py`: `MemoryCache[T]`（LRU + TTL + 可选线程安全）
- `LLMResponseCache` 增加 `threading.Lock`（`generate_async` 并发安全修复）
- `generate_async` 同步缓存逻辑与 `generate()`

### 方向 5: Wiki/ASR 模块整理
- 4 个兼容 shim 文件增加 `DeprecationWarning`
- 测试导入路径修正到规范路径

### 影响范围
19 文件，+887 / -64 行。1,829 测试通过（含 1 预存 flaky）。

---

## v3.19.10 (2026-07-21)

ASR 引擎全面质量加固 — P0 字符串截断 + P1 正确性 + P2 健壮性 + P3 性能优化，共十四项修复。

### P0 严重 Bug

- **`prompt_optimizer.py` `protected_terms` 字符串截断**：`join(generator)[:60]` 对 join 后的字符串切片，导致术语被拦腰截断（如 `"概念BM25算"`）。改为先取列表前 N 项再 join。

### P1 正确性修复（4 项）

- **`corrector.py` 非修饰键校验**：新增 `_check_key()` 函数，热键含非修饰键（如 Z / F5）时联动校验，避免仅靠修饰键误触发。
- **`hotwords.py` 超时保护**：`executor.map` 无 timeout → 改用 `as_completed` + timeout，支持部分结果保留。
- **`formatter.py` `max_chinese` 参数化**：从硬编码 `max_chinese=10` 改为函数参数，与 `max_chars` 语义一致。
- **`coverage.py` 去重 `_count_chinese`**：复用 `iris.utils.tokenization.count_chinese`，移除本地重复实现。

### P2 健壮性修复（4 项）

- **`corrector.py` 书面中文预检查**：新增 `_looks_like_written_chinese()` 函数，在调用昂贵的 `osascript` 前快速过滤书面中文（标点≥2或含口语填充词），减少子进程开销。
- **`extractor.py` JSON 截断 warning**：`_parse_misreadings_response` 解析失败时 `logger.warning` 记录丢失术语数，避免静默丢数据。
- **`feedback.py` 空字符串校验**：`extract_mappings_from_corrections` / `extract_llm_discoveries` 增加 `if not wrong or not right: continue`。
- **`corrector.py` 冗余 `_last_text` 赋值移除**。

### P3 性能与代码质量（5 项）

- **`corrector.py`** `_diff_changes` / `_looks_like_written_chinese` import 升至模块顶部。
- **`corrector.py` Aho-Corasick 写指针优化**：`result_chars[:pos]` 切片复制 → `del result_chars[write_pos:]` 原地删除，减少内存分配。
- **`extractor.py` / `hotwords.py` worker 数动态获取**：`os.cpu_count()` 替代硬编码上限 8/6。
- **`_text_detector.py` 单字符代码分档**：`[{};]` 从一次触发改为需 ≥2 个代码特征才拦截，避免 ASR 转写中偶发单字符被误判。
- **`extractor.py` / `hotwords.py` 失败批次重试**：LLM 调用失败自动重试一次（2s 退避）。

### 影响范围

`src/iris/wiki/asr/` 子包 8 文件，+313 / -114 行。全部 111 个现有单元测试通过。

---

## v3.19.9 (2026-07-20)

双周报流水线全面质量加固 — P0 数据遗漏防护 + P1 内容质量提升，共九项修复。

### P0 修复（数据遗漏防护，3 项）

- **Stage 1 全空兜底**：LLM 返回有效 JSON 但四个级别全空时，触发 owner-map + 成员周报分级分配（owner 匹配 → high，其他成员周报 → medium，其余 → low），防止方向级数据静默遗漏
- **Stage 1 owner-map 注入**：文件清单标注作者字段 + prompt 显式输出 owner→子方向映射表，引导 LLM 将 owner 文件归入 high/medium
- **Stage 1 缓存方向数校验**：`load_stage1_filter()` 新增 `expected_count` 参数，缓存方向数不完整时自动废弃重跑，避免脏缓存导致部分方向 0 文件

### P1 改进（内容质量提升，6 项）

- **Stage 3 约束重构**：`max_items` 硬约束 → 子方向全覆盖 + 每子方向 ≤3 条 + 每条 ~50 字精简。解决 3 子方向抢 4 条配额导致真伪检测被合并的问题
- **Stage 3 brief 优先级排序**：按 Stage 1 分发（high/medium/low）+ owner 匹配计算优先级，确保高质量 brief 优先进入 LLM 上下文
- **Stage 4b 审查维度扩展**：新增子方向覆盖完整性检查（维度 4），条目数量规则改为每子方向 ≤3 条，审查项 10→11 重新编号
- **Stage 2 方向上下文增强**：注入子方向名称/责任人/目标信息 + `relevant_directions` 合并 Stage 1 分发，避免跨方向内容遗漏
- **Stage 2 截断提示优化**：超长文件（>50K 字）截断时附加提示，降低 LLM 对「无相关信息」的置信度
- **Stage 1 超时兜底**：并行超时后逐个补跑未完成方向（原方案静默丢弃），超时时间 120s→240s
- **key_indicators 端到端贯通**：Stage 3 合成 + Stage 4b 审查均注入 `key_indicators` 上下文
- **子方向顺序检测改进**：三级 fallback 匹配（全名 → 15 字符 → 10 字符）+ warning 提示优化

### 相关文件

`src/iris/analysis/service.py` (+170/-46)、`_biweekly_helpers.py` (+99)、`_biweekly_cache.py` (+12)、`_biweekly_collector.py` (+4)、`templates/prompt/biweekly_stage{3,4}*.md`（+34）

---

## v3.19.8 (2026-07-20)

检测路径全面改进 — 4 条检测路径（复杂输入 / ASR 文本 / ASR 覆盖 / Wiki 深度评估）P0~P2 十四项修复，二轮核查追加 4 项。

### P0 修复（正确性 Bug）

- **`coverage.py` 死代码删除**：`is_dangerous_mapping()` 第 55 行条件与第 53 行完全相同，永远不会执行，删除
- **`_reference_parser.py` RANGE_PATTERN 贪婪正则**：原 `r".*(.+\.md):..."` 的贪婪 `.*` 截断深层路径（如 `SOURCE/05-会议纪要/report.md:109-116` 只捕获到 `.md` 后缀），改为 `r"([^\s\[\]]+\.md):(\d+)-(\d+)"`，调用方同步改为 `search()` 支持行中任意位置
- **`deep_eval.py` 字符串表达式未赋值**：`"\n".join(...)` 结果被丢弃，修复方案 `problem` 字段永远为空；修复为 `page_list_str = "\n".join(...)` 并注入字段
- **`deep_eval.py` SourceLocator 加载无异常处理**：`_locator.load()` 遇文件损坏/缺失直接崩溃；包 `try/except (OSError, ValueError)` 输出定向提示

### P1 修复（设计缺陷）

- **`_types.py` 裸泛型类型**：`CoverageReport`/`DictQualityReport`/`AsrCorrection` 共 9 个字段 `list`/`dict` 改为 `List[str]`/`List[Tuple[str,str,str]]`/`Dict[str,int]`；导入补齐 `Tuple`
- **`coverage.py` 槽位效率去重 + 参数化**：`effective` 计算对噪音词 `set()` 去重防 negative；`analyze_coverage()` 新增 `max_slots: int = 500` 参数（向后兼容），docstring 同步更新
- **`_text_detector.py` 代码正则补全 + 参数签名化**：补 `from\s+\S+\s+import`，去除覆盖过宽的 `return\s`（中英混合场景误伤）；`_is_asr_text()` 三个阈值改为有默认值的参数
- **`_source_locator.py` 路径归一化双端一致**：`load()` 阶段对 chunk 的 `relative_path` 同步归一化（原只在 `lookup()` 阶段归一化，`./` 前缀路径无法命中）；`find_sibling_sources` 使用归一化路径；`search_sources_by_keywords` 的 `exclude_path` 参数归一化后比对；行号无精确匹配时 fallback 前添加 `logger.warning`

### P2 测试（+79 用例，总量 1,753）

- **新建 `tests/unit/test_text_detector.py`**（40 用例）：`_count_chinese` + `_is_asr_text` 长度边界 / 中文比例 / 代码特征 / URL / Markdown 全分支覆盖；含一个代码特征测试改用中英混合输入真正由 `{}` 触发（而非中文比例过滤）
- **`test_complex_input_detector.py`** 新增 6 个 `InputDetector.detect()` 集成用例：PNG 编码 / PDF 非编码 / 超大图跳过 / 不存在路径 / 混合类型 / 纯文本
- **`test_deep_eval_pure.py`** 新增 3 个 RANGE_PATTERN 回归用例：简单路径 / 深层路径（回归防贪婪截断）/ search 匹配行中嵌入路径
- **`test_deep_eval.py`** 新增 7 个 SourceLocator 用例：`./` 前缀归一化 / `/` 前缀归一化 / 反斜杠归一化 / 行号 fallback 回末尾 chunk / fallback `logger.warning` caplog（`lookup` + `lookup_with_context`）

---

## v3.19.7 (2026-07-19)

全面质量加固 — P0/P1/P2 七项优化：可靠性、向量缓存、代码拆分、单元测试补全、LLM 熔断。

### 改进

- **`_wiki.py` 静默异常修复（P0）**：5 处 `except Exception: pass` 全部补充 `logger.warning/debug`，涵盖 asr_profiles.json 加载、手动热词合并、ai_settings.json 写入、postprocess.json 写入等场景，CLI 错误不再无感知吞噬
- **向量缓存（P0）**：`retrieval/embedder.py` 新增 LRU + TTL 缓存层（`OrderedDict` + `threading.Lock`，128 条 / 600s），相同 query 反复 ask 时命中缓存不重复调用 embedding API，镜像 `enhanced.py` 既有模式
- **`corrector.py` 拆分（P1）**：834→712 行，剪贴板 I/O 提取至 `wiki/asr/_clipboard_io.py`，ASR 文本检测提取至 `wiki/asr/_text_detector.py`，`corrector.py` 保留 re-export 确保向后兼容
- **LLM 熔断机制（P2）**：`provider.py` 新增 `_CircuitBreaker`（threshold=5, reset_after=60s），集成到 `_fallback_loop`，连续失败自动开路、60s 后半开重试、成功后复位，防止 API 宕机时每次阻塞 90s

### 测试

- 新增 `tests/unit/test_biweekly_helpers.py`：48 个纯函数测试（`_biweekly_helpers.py` 全部无 I/O 函数）
- 新增 `tests/unit/test_graph_engine.py`：26 个图算法测试（`build` / `neighbors` / `find_path` / `orphans` / `bridges` / `degree_stats`，零 mock）
- 新增 `tests/unit/test_config_models.py`：35 个 Pydantic schema 测试（`BaseConfigModel` dict 兼容、validator 边界、嵌套模型组装）
- 单元测试总量：379 用例（+109），全部通过

---

## v3.19.6 (2026-07-19)

ASR 校正引擎加固 — max_mappings 配置化、替换词典热加载、手动热词合并机制。

### 改进

- **`max_mappings` 上限扩展与配置化**：替换词典上限 990→2000，移至 `config/asr_profiles.json` profile 配置，优先级链「CLI 参数 > profile > 默认值 2000」，后续调整零代码改动
- **替换词典热加载**：新增 `set_dict_path()` + `_check_dict_reload()`，每 5 秒检测 `data/asr_replace_dict.json` mtime，变化时自动重建 Aho-Corasick 自动机，`build-asr-prompt --deploy` 后无需重启进程
- **手动热词合并机制**：新增 `data/asr_manual_hotwords.txt`，用户手动添加的热词在 `--deploy` 时自动与 LLM 生成热词合并去重后写入 vocotype，手动热词永久保留不被覆盖

### 文档

- 配置示例 `asr_profiles.json.example` 同步更新，三个 profile 新增 `max_mappings` 字段

---

## v3.19.5 (2026-07-19)

全面质量加固 — 双周报流水线优化、ASR 子系统健壮性提升、测试覆盖补全。

### 改进

- **双周报 Stage 4 拆分**：`_stage4_assemble_and_review` 拆为 `_stage4a_assemble`（纯结构拼接，无 LLM）+ `_stage4b_review`（专项 LLM 审查），消除 9 项审查指令竞争；Stage 4 Prompt 去掉「组装终稿」任务描述，聚焦质量审查
- **`_TEAM_OKR_PATTERN` 配置化**：硬编码团队名单提升为 `app.biweekly_report.team_okr_patterns` 配置项，支持 `dept_op_keyword`（部门关键词）+ `team_okr_patterns`（排除列表）两个新字段，零代码改动应对组织架构变化；`app.json.example` 同步更新
- **Stage 3 子方向顺序后置校验**：新增 `_s3_check_subarea_order()`，合成完成后检查输出子方向顺序是否与 OP 定义一致，不一致记录 warning（不修改输出）
- **ASR 音近推断示例动态化**：`_render_v2` 改为从实际 `terms` 中动态选取 2 个人名 + 1 个项目名作为推断示例，替代硬编码的「大冒险→大模型」，更贴合当前知识库
- **`generate_misreadings` 超时修复**：移除 `min(len(batches), 8)` 上限（最大 720s），改为 `len(batches) * 90`，避免术语量大时批次超时丢失映射

### 修复

- **`load_op_document` fallback 日志**：兜底使用非部门级文件时，日志升级为 `warning` 并打印文件名，用户可感知
- **`app.json.example` 配置歧义**：移除 `"team_okr_patterns": []`（显式空列表会完全禁用过滤，与注释描述矛盾），改为不配置此项，注释说明"不配置 = 使用内置默认值；显式 `[]` = 完全禁用"
- **`_biweekly_helpers.py` import 位置**：`import logging` 移至文件顶部导入块（原在第 465 行末尾）
- **Stage 4 Prompt 变量名**：`{{direction_sections}}` 改为 `{{assembled_report}}`，语义与实际传入内容（完整组装文档）一致

### 测试

- **`test_biweekly_collector.py`**：新增 OKR 过滤逻辑 4 个测试用例（部门级优先、团队排除、fallback warning、自定义关键词）
- **`test_config.py`**（`TestWarnUnresolvedPlaceholders`）：2 个回归测试——`${IRIS_DATA_DIR}` 不误报 + 真正缺失变量仍触发 warning
- **`tests/unit/test_asr_prompt_optimizer.py`**（新建）：19 个单元测试覆盖 `_render_v2` 和 `_pick_inference_examples` 的边界情况（空术语、动态示例、映射数量上限、接口一致性等）
- 总测试数：**407 通过**（较上版新增 30 个）

---

## v3.19.4 (2026-07-19)

双周报生成逻辑优化 & 配置加载修复。

### 修复

- **`_biweekly_collector.py` OP 文档选择**：`load_op_document()` 曾误取个人/团队 OKR（如李四 OKR），改为按「数据部门」关键词正选 + 正则排除 `-团队名-人名-OKR` 模式
- **`loader.py` 占位符误报**：`_warn_unresolved_placeholders` 在 `resolve_path_vars` 之前执行，导致 `${IRIS_PROJECT_ROOT}` 等路径占位符误报

### 改进

- **Stage 0a Prompt**：支持 `### KR1：` 子方向格式；同 KR 下多个 KP 合并为一条 sub_area
- **Stage 3 Prompt 全面重写**：
  - `## ` 方向标题精简化（不照搬 OKR 原文）
  - 每方向关键进展最多 **4 条**，每条只保留核心结论 + 1-2 个数据点
  - 来源按时间优先级取最新（近一周优先）
  - 严格按 KR 编号顺序排列
  - 同 KR 下多个 KP 合并输出
- **Stage 4 Prompt 审查维度**：增加标题精炼、条目数、来源新鲜度检查
- **`app.json`**：新增 `report_author` 配置，启用双周报尾注
- **`llm.json`**：新增 3 条 ASR 路由规则（`asr_correction` / `asr_misreading` / `asr_hotword`），用量统计可独立区分 ASR 消耗

---

## v3.19.3 (2026-07-19)

交互体验 — `build-asr-prompt` 三阶段实时进度输出。

### 新增

- **`_progress.py`**：线程安全进度追踪器 `ProgressTracker`，并发批次实时输出，锁保护防交错
- **Phase 2 逐批进度**：`extractor.py` 误识别生成阶段新增逐批完成输出（此前完全静默）
- **Phase 级耗时**：每个 Phase 完成后输出耗时和产出摘要
- **总耗时汇总**：流程结束时打印全流程耗时和各阶段产出

### 修复

- **Phase 3 标签修正**：「LLM Prompt 优化压缩」→「校正提示词渲染」（实际无 LLM 调用，为 Python 模板直渲染）

---

## v3.19.2 (2026-07-19)

ASR Phase 1 基础设施 — 反馈驱动的反向优化闭环做准备。

### 新增

- **`_AhoCorasick.list_patterns()`**：返回全部已加载替换规则，供 Phase 1 僵尸规则检测使用
- **`extract_llm_discoveries()`**：从 feedback 中仅提取 `[LLM]` 标记的修正条目，区分词典命中 vs LLM 发现
- **`_daily_asr_audit()`**：daily-start 第 6 步 ASR 覆盖审计，纯本地零 LLM 成本，无产物时静默跳过

### 修复

- **`extract_mappings_from_corrections`** 修复 `[LLM]` 前缀未剥离导致解析错误的 bug
- **`run_forever`** 启动日志中模式计数改用 `list_patterns()`，修复预存的 `pattern_count` 变量未插值 bug

---

## v3.19.1 (2026-07-19)

ASR 子系统代码质量加固 — 6 项修复/优化。

### 修复

- **JSONL 反馈格式统一**：`save_correction()` 与 `_append_feedback_jsonl()` 字段集对齐，`llm_time_ms` 写入和加载路径一致
- **热词去重逻辑修正**：移除去重前的前置截断 `[:max_hotwords * 2]`，改为遍历全部候选后截断，避免高重复率场景下热词数不足
- **死代码清理**：移除 `prompt_optimizer.py` 中已废弃的 `build_optimize_prompt()` 和 `_clean_text()` 方法（V3 起由 `_render_v2()` 替代）

### 优化

- **等待策略改进**：`_replace_text_in_place` 从固定 `delay 0.2s` 改为基线等待 + 剪贴板稳定性轮询，总等待 ≤1.15s
- **常量复用**：`coverage.py` 用 `get_wiki_prefix()` 替换函数内硬编码的 prefix_map

---

## v3.19.0 (2026-07-19)

ASR 实时校正引擎 — 从离线配置编译器升级为 vocotype 实时校正服务。

### 新增模块

- **`iris-asr-corrector`** 常驻守护进程：剪贴板监听 vocotype ASR 输出，Aho-Corasick 多模式匹配 + LLM 编辑助手双重校正，自动反馈数据采集（`data/asr_feedback.jsonl`）
- **`iris-asr-audit`** 覆盖分析：热词覆盖率、噪音检测、高危映射检查、格式错误检查，纯本地秒级运行
- **`iris-asr-report`** 手动纠错：从剪贴板读取 ASR 原文 + 用户提供正确文本，写入反馈数据

### 策略 Prompt V3

- Python 模板直渲染替代 LLM 生成，消除输出不稳定性
- 编辑助手角色：纠错 + 润色 + 领域保护名单
- Prompt ~930 字，支持热加载（文件变化自动重载）

### 质量加固

- 替换词典高危映射过滤：单字高频中文（在、是、的…）不得作为误识别目标
- deepseek-v4-flash 推理关闭（`thinking: disabled`），消除 CoT 泄漏
- CoT 安全网：LLM 输出 > 输入 ×3 时自动降级为词典结果
- 词级 diff 追踪 LLM 修改（替代字符级拆分）
- 处理耗时追踪：`llm_time_ms` 字段

### CLI 变更

- 新增 `asr-corrector`、`asr-audit`、`asr-report` 三个命令
- `build-asr-prompt` 新增 `--deploy` 一键部署到 vocotype
- 协议版本 3.10 → 3.11

---

## v3.18.9 (2026-07-17)

代码质量加固 — 并发安全、可观测性、成本控制、配置化。

### 并发安全（P1）

- **内存系统 FileLock**：`CorrectionMemoryStore` / `UserProfileMemoryStore` 的读-改-写操作（`apply_text_update` / `delete` / `save`）改用 `FileLock` 包裹，防止多进程并发时后写者覆盖前写者修改；`manager.export_to_file()` 改用原子写入（`_atomic_write_json`）替换直接 `write_text`

### 可观测性（P2）

- **向量索引模型追踪**：`meta.json` 新增 `embedder_model` 字段，切换嵌入模型后加载时发出 `WARNING` 提示重建索引；`TextEmbedder` 暴露 `model` 属性
- **`.env` 行尾注释剥离**：`load_env_file()` 对非引号值剥离行尾 ` # comment`，避免注释内容污染变量值
- **未解析占位符警告**：配置加载后若仍含 `${VAR}` 占位符（变量缺失），以 `WARNING` 日志输出字段路径，提升配置问题诊断效率

### 成本控制（P2）

- **Stage2 多模态 `max_tokens`**：`generate_multimodal()` 在 Provider / LLMService / pipeline 三层新增 `max_tokens` 参数；Stage2（adv_model 分析）默认 `max_tokens=4096`，防止大型 PDF/视频输出失控

### 健壮性与可配置性（P3）

- **lark-cli 不可用立即 fallback**：`_run()` 检测 `FileNotFoundError` 后直接抛出 `FeishuClientError`（提示安装命令），不再做 3 次无效退避重试
- **Wiki 证据阈值配置化**：`CANDIDATE_EVIDENCE_THRESHOLDS` 移至 `wiki.json` 的 `discovery.evidence_thresholds` 节点，`build_candidates()` 优先读取外部配置，代码常量保留为回退默认值

---

## v3.18.8 (2026-07-17)

性能优化 — PersonEnricher 飞书 API 频率限制修复。

### 人物丰富优化

- **预先过滤已丰富页面**：新增 `_needs_enrichment()` 方法，在调用飞书通讯录 API 前先检查 frontmatter，跳过已含 department/email 的页面（436 人中绝大多数可跳过），从根本上减少 API 调用量
- **自适应批间延迟**：成功时逐步恢复至 3s，失败（检测到频率限制）时翻倍至上限 30s，避免退避后再次触发的振荡
- **批次大小调低**：`_BATCH_SIZE` 20 → 10，单批 API 负载减半

---

## v3.18.7 (2026-07-17)

工程优化 — CI/CD 基础设施、测试分层重组、Wiki 模块重构。

### CI/CD 基础设施

- 新建 `Makefile`：`test`、`test-unit`、`test-integration`、`lint`、`lint-fix`、`format`、`clean` 目标
- 新建 `.pre-commit-config.yaml`：ruff check + format hooks
- 新建 `.github/workflows/ci.yml`：Python 3.9-3.12 矩阵，lint → unit → integration → coverage
- 新建 `Dockerfile`：Python 3.9 开发环境

### 测试分层重组

- 新建 `tests/unit/`（199 用例，0.5s 快速反馈）和 `tests/integration/`（1,314 用例）
- 通过 `pytest_collection_modifyitems` 自动按路径标记，`make test-unit` / `make test-integration` 可独立运行
- 新增 46 个核心基础设施测试：`FakeLLMProvider`、`SharedThreadPool`、`script_loader`、`atomic_write_json`、常量校验等
- 测试总数 1,467 → 1,513，覆盖率 59.86% → 60.42%

### Wiki 模块重构

- 从 `graph.py`（751→215 行）提取 `_graph_engine.py`（174 行）：`_GraphEngine` + `GraphNode` + `GraphEdge` 独立，消除与 `_relation_extractor.py` 的循环导入隐患
- ASR 子系统物理重组：5 文件移入 `wiki/asr/` 子包，提取 `_types.py`（`AsrTerm`/`AsrPromptVersion`）零依赖叶子节点，切断五文件循环导入
- 原路径保留 shim 文件，所有外部调用方向后兼容

### 代码质量

- 修复 8 个 F821（未定义名称）：补充 `logger`、`Optional` 导入、`TYPE_CHECKING` 守卫
- 修复 4 个 E741（模糊变量名）+ 2 个 F402（循环变量遮蔽导入）
- ruff 对 F821/E741/F402 零错误

---

## v3.18.6 (2026-07-17)

开源脱敏补充清理 — 上一轮安全审查遗漏项修复。

### 内部标识符通用化

- 数据源默认名称通用化（7 处：配置示例、代码默认值、元数据路径、帮助文本）
- 移除内部共享工作空间名称注释（`_source_locator.py`）

### 第三方服务引用脱敏

- 行程单提取脚本重命名为通用名称 + 内容品牌词脱敏
- CLI 命令名同步更新

### CHANGELOG 反向泄露修复

- 移除历史脱敏记录中反向暴露的原始值（部门/项目/公司/个人/路径名）

### 文档同步

- CLAUDE.md、README.md、CHANGELOG.md 版本号与内容同步至 v3.18.6

### 版本

- 产品版本 3.18.5 → **3.18.6**
- 协议版本不变（3.10）
- 数据版本不变

---

## v3.18.5 (2026-07-17)

新增项目级 Skill — `iris-daily-start` 每日启动维护。

### Skill 化

- **新增 `iris-daily-start` Skill**（`.claude/skills/iris-daily-start/SKILL.md`）：将 CLI 命令 `daily-start` 封装为 Claude Code 项目级 Skill，用户可通过自然语言「daily start」「每日启动维护」一键触发全自动管道
  - 5 步管道：记忆同步与维护 → 扫描切块 + 向量索引 → Wiki 自动维护 + 人员丰富 + 图谱刷新 → 导航索引 → LLM 用量概要
  - 与 `iris-wiki` Skill 互补：daily-start 是全自动日常维护，iris-wiki 是交互式知识策展
- CLAUDE.md 更新：Skill 数量 7→8，技能列表新增 `iris-daily-start`

### 版本

- 产品版本 3.18.4 → **3.18.5**
- 协议版本不变（3.10）
- 数据版本不变

---

## v3.18.5a (2026-07-17)

更新 adv_model 降级链配置。

### 配置

- 默认模型：`qwen3.6-plus` → **`qwen3.7-plus`**
- 重构降级链（6 级，按优先级）：
  1. `qwen3.7-plus` — 默认增强模型
  2. `qwen3.7-plus-2026-05-26` — 1 级 fallback
  3. `qwen3.6-plus-2026-04-02` — 2 级 fallback
  4. `qwen3.6-flash` — 3 级 fallback，轻量多模态
  5. `qwen3.6-27b` — 4 级 fallback，纯文本
  6. `qwen3.5-plus-2026-04-20` — 兜底 fallback
- 移除旧版本：`qwen3.6-plus`、`qwen3.5-plus`（无快照版本）

### 版本

- 产品版本不变（3.18.5，配置 patch）
- 协议版本不变（3.10）
- 数据版本 3.5（llm.json schema 不变）

---

## v3.18.4 (2026-07-17)

代码质量优化 — 正确性修复、技术债清理、模块拆分、测试补充。

### 正确性修复（P0）

- **`retrieval/enhanced.py` 裸 except 修复**：`EnhancedRetriever.__init__` 中 ConfigBundleV2 读取的 `except Exception: pass` 改为 `logger.debug`，异常不再静默丢失
- **`_rrf_fuse` 向量命中条件修复**：纯向量命中补充条件从 `len(hit_by_id) < top_k`（常量，逻辑错误）改为 `len(result) < top_k`（正确判断当前结果数）

### 技术债清理（P1）

- **`retrieval/enhanced.py` rrf 配置统一**：`retrieval_cfg` 存为 `self._retrieval_cfg`，`search()` 中 rrf 配置直接复用，消除 `__init__` 和 `search()` 双轨访问不一致
- **`core/async_http.py` 接入 `shared_pool`**：移除独立 `_sync_pool` 全局变量，改用 `shared_pool.get_executor(8)`；`thread_pool.py` 新增 `get_executor()` 公共方法

### 模块拆分（P2）

- **`wiki/graph.py` 关系提取层独立**：LLM 关系提取逻辑（`_extract_page_relations`、`_parse_triples`、`_triple_obj_to_edge`、`_save_page_relations`、`_find_changed_pages`、`_format_entity_list`、`_RELATION_EXTRACT_PROMPT`、`_safe_filename`）迁移至新文件 `wiki/_relation_extractor.py`
  - `graph.py`：973 → **747 行**（−226 行）
  - `WikiGraph.extract_relations()` 变为薄委托层，调用 `RelationExtractor.extract()`
  - 向下兼容：`_safe_filename` 在 `graph.py` 重导出，已有测试不变

### 测试补充（P3）

- **DeprecationWarning 修复**：`test_memory_lifecycle.py` `datetime.utcnow()` → `datetime.now(timezone.utc).replace(tzinfo=None)`，消除最后一个 DeprecationWarning
- **`llm/service.py` mock 测试**（+14）：缓存命中/未命中/写入、`LLMProviderError` 传播、`get_cache_stats`/`clear_cache`、`generate_multimodal`、`generate_async` 缓存命中和同步路径
- **`memory/session.py` 测试**（新文件，+23）：`SessionMemoryStore` 全路径（load/save/disabled/dedup/wiki_topics）+ 纯函数（`_build_topics`、`_update_topic_threads`、`_build_recent_summary`）
- **`test_graph.py` 迁移**：`TestParseTriples` 从 `WikiGraph._parse_triples()` 迁移为直接测试 `RelationExtractor._parse_triples()`

### 版本

- 产品版本 3.18.3 → **3.18.4**
- 协议版本不变（3.10）
- 数据版本不变
- 测试：1,439 → **1,467**（+28），测试文件 83 → **85**
- `llm/service.py` 覆盖率：68% → **97%**；`memory/session.py`：85% → **100%**

---

## v3.18.3 (2026-07-17)

全面测试补充 + 覆盖率里程碑 — 纯函数测试、CLI 集成测试、核心模块覆盖。

### 测试补充（+216 用例）

- **纯函数模块**（5 新文件）：`feishu/_shared` (23)、`retrieval/vector_index` (15)、`ingest/pdf_extractor` (7)、`complex_input/detector` (15)、`wiki/asr_hotwords` (21)、`trello/llm+models` (13)
- **CLI 集成测试**（1 新文件）：`test_cli_handlers` (21) — check_config/route_model/scan/search/status/diagnose/memory/wiki_lint/graph/usage
- **扩展已有测试**：`analysis_helpers` (+13)、`cli_helpers` (+13)、`navigation` (+9)、`wiki_discovery` (+3)
- **数据类测试**（2 新文件）：`person_enricher` (8)、`trello_pure` (13)

### 覆盖率里程碑

- 53% → **60%**（+7%）
- 1,223 → **1,439**（+216）
- 73 → **83** 测试文件（+10）
- 警告 9 → **0**

### 版本

- 产品版本 3.18.2 → **3.18.3**
- 协议版本不变（3.10）
- 数据版本不变

---

## v3.18.2 (2026-07-16)

文档版本号同步 + 测试补充 + 去抖 bug 修复。

### Bug 修复

- **文件监听去抖**：`ingest/watcher.py` 中 `_debounce` 首次事件因 `time.monotonic()` 在 macOS 上起始值过小（~0.005s）被 `now - 0 >= 2.0` 条件误过滤，改为 `key not in self._recent_events` 首次放行

### 测试

- 新增测试：`config/secrets` 12 用例、`ingest/watcher` 5 用例、`output/formatter` 扩展 12 用例
- 单元测试 1,265 → **1,291**（+26），覆盖率 53%→**54%**

---

## v3.18.1 (2026-07-16)

全栈代码质量优化 — 异常处理、性能、线程池、缓存、配置架构全面升级。

### 稳定性修复（P0）

- **异常吞噬修复**（15 文件）：所有 `except Exception` + 裸 `pass` 改为 `logger.error/warning/debug` + 具体异常类型
- **文件 I/O 编码统一**：全部 `read_text`/`write_text` 显式 `encoding="utf-8"`
- **subprocess 超时审查**：确认 Keychain 10s / 飞书 client 60s 合理

### 可维护性提升（P1）

- **共享线程池**：新增 `core/thread_pool.py`，替换 6 个模块中每次新建 `ThreadPoolExecutor` 的模式
- **ConfigBundle 统一 Pydantic v2**：`ConfigBundle` 从 dataclass 迁移为工厂函数，委托给 `ConfigBundleV2.from_dicts()`，缺失字段自动填充默认值
- **大文件拆分**：`evaluation/deep_eval.py` 883→773 行，抽出 `_reference_parser.py`（Wiki 引用解析）
- **核心模块测试**：新增 42 用例（`write_guard` 8 + `locks` 8 + `feishu client` 11 + 其他 15）

### 性能优化（P2）

- **LLM 缓存 LRU 驱逐**：从每 50 次全目录扫描改为内存 `OrderedDict` O(1) 维护
- **BM25 统计缓存**：全局 BM25 统计量写入磁盘缓存，通过 chunk 索引 mtime 判新

### 工程成熟度（P3）

- **警告过滤**：`pyproject.toml` 过滤 SwigPy DeprecationWarning 和 pytest 临时文件清理警告（9→0）
- **飞书退避优化**：退避算法 `1.2^n` → `2^n + 随机抖动`
- **字段默认值**：`ModelItem.supported_inputs`/`use_cases`、`WikiConfig` 全字段添加默认值
- **数据源验证宽松**：`DataSourceConfig` 全部禁用时改为 warn 而非 raise
- **文件监听去抖修复**：`ingest/watcher.py` 中 `_debounce` 的首次事件因 `time.monotonic()` 起始值过小被误过滤，改为 `key not in self._recent_events` 首次放行

### 版本

- 产品版本 3.18.0 → **3.18.1**
- 协议版本不变（3.10）
- 数据版本不变

### 测试

- 新增测试模块：`config/secrets`（12 用例）、`ingest/watcher`（5 用例）、`output/formatter` 扩展（+12 用例）
- 单元测试 1,223 → **1,291**（+68），73 文件，覆盖率 53%→**54%**

---

## v3.18.0 (2026-07-15)

开源脱敏全面清理（方案B深度清理）—— 为开源发布做准备的系统性安全加固。

### 源码脱敏

- `src/iris/evaluation/deep_eval.py`：两处硬编码内部服务名称替换为通用描述
- `src/iris/llm/provider.py`：内部算法平台代号（原名含内部标识）→ `custom-algo-platform`
- `src/iris/wiki/discovery_rules.py`：`HIGH_VALUE_TOPIC_HINTS` 业务方向特征泛化，`STOPWORDS` 移除残留学号/内部词
- `src/iris/wiki/graph.py` / `backlink.py`：文档字符串示例中的内部项目名替换为通用占位符
- `templates/prompt/biweekly_stage3_direction.md`：错误示范中的内部业务术语替换
- `templates/wiki/generate_person.txt` / `generate_generic.txt`：示例中的内部项目名替换

### 测试脱敏（15 个文件）

- 内部部门名 → 通用占位符
- 内部项目名 → 通用占位符
- 公司名称 → 通用占位符
- 个人姓名 → 通用占位符
- 内部品牌词 → 通用词

### 文档脱敏

- **CHANGELOG.md** 全量审查（983 行）：移除所有版本记录中反向泄露的真实姓名、内部项目代号、企业邮箱域名
- **CLAUDE.md**：Obsidian vault 路径变量化（内部路径名 → 通用变量）
- **pyproject.toml** / **LICENSE**：作者名匿名化

### Git 历史全面重写

- 使用 `git filter-repo` 全文替换全部 92 个提交中的敏感信息
- 创建备份分支后执行，完成后验证并删除

### 新增文件

- `CONTRIBUTING.md`：贡献指南（提交规范/开发环境/安全注意事项）
- `SECURITY.md`：安全政策（漏洞报告流程/安全实践/支持版本）

### .gitignore 强化

- 新增 `.claude/settings.local.json` 规则
- 新增 `.git-filter-replacements.txt` 规则

### 版本

- 产品版本 3.17.1 → **3.18.0**
- 协议版本不变（3.10）
- 数据版本不变

### 测试

- 单元测试 1,223 通过不变

---

## v3.17.1 (2026-07-15)

SOURCE 结构优化：新增「工作简报」栏目 + bug 修复。

### 新功能

- **SOURCE 新增 09-工作简报/**：用于存放从聊天记录、多会议总结、邮件等多渠道聚合生成的项目进展简报和工作简报。规范命名 `{YYYYMMDD}-简报-{topic}（from{source_type}）.md`。
- **简报归档**：已迁移两份简报至新栏目（W28 会议纪要聚合 + 某流程优化方案推动进展）。

### Bug 修复

- **argparse 冲突修复**：`--incremental` 参数在 CLI 中定义两次导致 `transcribe-meeting` 等命令无法运行，已移除重复定义。

### 文档更新

- `CLAUDE.md`：SOURCE 目录更新为 9 类分层，新增 09-工作简报 说明。
- `SOURCE/_INDEX.md`：新增工作简报栏目说明及命名规范。

### 测试

- 单元测试 1,223 通过不变

### 维护（2026-07-15）

全维度 Wiki / 索引 / 知识图谱质量评估与自动修复。

**评估结果**
- Wiki lint：192 页，43 条断裂链接，81 条孤立页，1 个草稿
- 深度评估：232 条引用中 121 条源缺失，2 处信息遗漏（涉及 3 页）
- 知识图谱：192 节点 / 763 边，16 个孤立节点，图密度 0.021

**自动修复**
- **断裂链接**：AI评估框架 + 项目Beta时间线 2 页重新生成，断裂 43→41
- **草稿发布**：项目Alpha `draft` → `active`
- **源索引**：重建 chunk 索引（703→4,096）+ 向量索引（7,004→7,035）
- **信息补充**：智能问答服务等 3 页重新生成，补充遗漏内容
- **图谱优化**：孤立节点 16→4（-75%），wikilink 边 461→476（+15），桥接节点 58→64（+6），图密度 0.0208→0.0212
- **frontmatter 修复**：wiki-lint --fix 修复 1 处缺失

**后续操作**
- `daily-start` 全面更新完成（记忆同步 → 自治维护 → 扫描/切块/向量 → Wiki 增量 → 图谱刷新 → 用量统计）

---

## v3.17.0 (2026-07-15)

代码质量全面优化（P0-P3）：全局变量安全加固 + 异常处理精细化 + 模板加载统一 + ThreadPool 超时 + LRU 缓存驱逐 + 大文件拆分 + 测试大幅扩充。

### 工程质量

**P0 — 资源安全**
- `core/async_http.py`：全局 `ThreadPoolExecutor` 注册 `atexit` 清理，消除进程退出时线程池泄漏
- `config/loader.py`：`_plaintext_keys_warned` 全局标志用 `threading.Lock` 双重检查锁保护，消除多线程竞速

**P0 — 异常捕获精确化**
- `wiki/graph.py`：`except (LLMProviderError, Exception)` 拆分为两条分支（LLM 错误 warning，意外错误 error+exc_info），消除最危险的静默失败点

**P1 — feishu 模块诊断性提升**
- `feishu/chat_digest.py`：新增 logger，LLM 调用失败拆分为 `LLMProviderError`/`Exception`，写入失败改为 `OSError`
- `feishu/doc_convert.py`：新增 logger + `URLError` import，路径安全检查改为 `OSError/ValueError`，下载失败改为 `OSError/URLError/FeishuClientError`

**P1 — 模板加载统一**
- 新增 `utils/template_loader.py`：`load_template(relative_path)` 统一从 `templates/` 加载
- 消除 4 处重复实现（`wiki/generator.py`、`complex_input/pipeline.py`、`wiki/term_extractor.py`、`evaluation/deep_eval.py`）

**P2 — ThreadPool 超时标准化**
- `wiki/term_extractor.py`：`executor.map` 添加 `timeout=批次数*90`，捕获 `FuturesTimeoutError`
- `wiki/generator.py`：`as_completed` 添加 `timeout=max(300, 页数*60)`，超时时记录未完成页列表
- `analysis/service.py`：Stage 1/2/3 三处均添加超时（30/60/60 秒/项），消除线程泄漏风险

**P2 — LRU 缓存驱逐**
- `llm/cache.py`：`LLMResponseCache` 新增 `max_entries`（默认 2000）参数
- 每写入 50 次触发 `_evict_lru()`，按 `cached_at` 删除最旧条目，防止磁盘无界增长
- `stats()` 新增 `max_entries`、`evictions` 字段

**P2 — 超大文件拆分**
- `analysis/service.py` 1232→**805** 行（-35%）：模块级辅助函数提取为 `analysis/_biweekly_helpers.py`（464 行）
- `evaluation/deep_eval.py` 1100→**883** 行（-20%）：数据类提取为 `evaluation/_types.py`，`SourceLocator` 提取为 `evaluation/_source_locator.py`

**P3 — LLM 调用接入缓存层**
- `wiki/graph.py` `_extract_page_relations`：从 `provider.generate()` 改为 `llm_service.generate(temperature=0)`，重复提取同一页面命中缓存免 API 调用

### 测试

- 912 → **1223**（+311）：新增 13 个测试文件
  - `test_biweekly_helpers.py`（60 用例）、`test_source_locator.py`（24）、`test_context_loader.py`（14）
  - `test_llm_cache_lru.py`（13）、`test_retrieval_searcher_pure.py`（19）、`test_metrics_and_types.py`（19）
  - `test_memory_long_term.py`（18）、`test_memory_manager.py`（12）、`test_deep_eval_pure.py`（20）
  - `test_wiki_searcher_pure.py`（26）、`test_tokenization.py`（19）、`test_chunker_pure.py`（26）
  - `test_wiki_discovery_utils_extended.py`（46）

### 版本

- 产品版本 3.16.0 → **3.17.0**
- 协议版本不变（3.10，无新 CLI 命令）
- 数据版本不变

### 项目指标

- 源文件：117 → **122**（+5：`_biweekly_helpers` / `_types` / `_source_locator` / `template_loader` / `analysis/_biweekly_helpers`）
- 测试文件：59 → **72**（+13）
- 单元测试：912 → **1223**（+311）
- 覆盖率阈值：50% → **53%**
- `analysis/service.py`：1232 → **805** 行
- `evaluation/deep_eval.py`：1100 → **883** 行

---

## v3.16.0 (2026-07-15)

全栈优化（P0-P3）：结构化日志 + async/await + 多工作空间 + 文件监听 + Prompt 外部化 + Wiki 引用校验 + LLM 缓存 + 增量 Chunk + 图谱引擎升级 + Config 迁移 + 指标导出 + 测试补齐。

### 新功能

**LLM 响应缓存（llm/cache.py）：**
- `LLMResponseCache`：基于 prompt hash 的磁盘缓存，两级目录结构（`data/cache/llm_responses/{prefix}/{hash}.json`）
- 仅缓存 temperature=0 的确定性调用，temperature>0 自动跳过
- 可配置 TTL（默认 3600s），过期自动清理
- 集成到 `LLMService.generate()`：命中缓存直接返回，未命中写入缓存
- `LLMService.get_cache_stats()` / `clear_cache()`：监控命中率和手动清空

**增量 Chunk 构建（ingest/scanner.py + chunker.py）：**
- `MarkdownScanner.scan_source_by_name(incremental=True)`：增量扫描，比较当前文件与上次扫描摘要（mtime + hash），仅返回新增/修改文件
- 同步检测已删除文件（`_deleted_paths`），chunker 自动清理旧 chunk
- 未变更文件保留已有 chunk，避免不必要的重新分块
- CLI：`iris build-chunks --incremental`（`_cli_main.py` 新增 `--incremental` 参数）

**知识图谱引擎升级（wiki/graph.py）：**
- 新增 `_GraphEngine` 抽象层：优先使用 NetworkX（`networkx>=3.0`），不可用时自动回退纯 Python
- NetworkX 模式下使用 `nx.DiGraph` 存储，`shortest_path` / `degree` / `in_degree` 等图算法开箱即用
- 重构 `neighbors()` / `find_path()` / `find_orphans()` / `find_bridges()` / `density_report()` 委托给引擎
- `pyproject.toml` 新增 `[project.optional-dependencies] graph = ["networkx>=3.0"]`

### 代码重构

**Prompt 模板外部化（P1-2）：**
- `generator.py`：`_build_generic_update_prompt` / `_build_person_update_prompt` / `_fallback_markdown` 优先加载外部模板（`templates/wiki/update_generic.txt` 等），保留内联 fallback
- `deep_eval.py`：`ACCURACY_PROMPT_TEMPLATE` / `PAGE_ACCURACY_PROMPT_TEMPLATE` / `COMPREHENSIVENESS_PROMPT_TEMPLATE` → `_get_accuracy_prompt()` 等函数，从 `templates/prompt/*.md` 加载
- `pipeline.py`：`_STAGE1_TEMPLATE` / `_STAGE3_TEMPLATE` → `_get_stage1_template()` / `_get_stage3_template()`
- `term_extractor.py`：`_build_misreadings_prompt` 新增 `_load_misreadings_template()` 外部加载
- 新增 9 个模板文件：`templates/wiki/update_generic.txt` / `update_person.txt` / `fallback_markdown.txt` + `templates/prompt/accuracy_check.md` / `page_accuracy_check.md` / `comprehensiveness_check.md` / `stage1_instruction.md` / `stage3_integrate.md` / `misreadings.md`

**Wiki 生成内置引用描述校验（P0-2）：**
- 所有 4 个 Wiki 生成/更新 Prompt 模板增加强制引用格式要求：每条引用附 10-30 字事实断言描述
- `WikiGenerator.check_reference_quality()`：解析 `## 参考来源` 章节，按描述完整性评级（good/fair/poor/no_refs）
- `build_page()` / `_update_page_with_content` 集成质量记录：生成/更新后自动检查引用质量，poor/fair 时日志警告

**ConfigBundle 渐进迁移试点（P1-3）：**
- `qa/service.py` 文档化 Pydantic 迁移路径
- `retrieval/enhanced.py` 兼容新旧两种配置访问方式（优先 ConfigBundleV2 属性访问，fallback dict）

### 测试

- 754 → **912**（+158）：
  - 新增 `test_qa_helpers.py`（`infer_evidence_type`/`intent_title`/`group_title`/`is_memory_only_instruction`/`infer_question_type`/`block_bonus`，41 用例）
  - 新增 `test_wiki_pure.py`（`_slugify_title`/`normalize_title`/`extract_terms`/`extract_persons`/`_extract_wiki_content`/`_validate_update_output`/`check_reference_quality`，41 用例）
  - 新增 `test_llm_cache.py`（`LLMResponseCache` put/get/stats/clear/TTL/目录结构，15 用例）
  - 新增 `test_wiki_discovery.py`（`should_merge`/`merge_candidates`/`prefer_candidate_title`/`build_candidates`/`suppress_path_concentrated_noise`，24 用例）
  - 新增 `test_qa_context.py`（`PromptContextPacker.pack`/`_compress_text`，12 用例）
  - 新增 `test_memory_working.py`（`WorkingContextStore` save/load/update/render_for_prompt，10 用例）
  - 新增 `test_p3_features.py`（`MetricsExporter`/`WorkspaceConfig`/`SourceWatcher`，19 用例）
  - `qa/helpers.py` **100%**，`memory/working.py` **94%**，`qa/context.py` **87%**，`llm/cache.py` **76%**，`wiki/discovery_utils.py` **65%**

### 工程基础设施

- 覆盖率阈值 **49% → 50%**（`fail_under = 50`）
- 可选依赖：`networkx>=3.0`（graph）、`httpx>=0.27`（async）

### 版本

- 产品版本 3.14.1 → **3.16.0**
- 协议版本 3.9 → **3.10**（新增 `metrics-export` / `watch` / `workspace` 命令）
- 数据版本不变

### 项目指标

- 源文件：110 → **117**（+7：cache / async_http / metrics / workspace / watcher / logging 重写 / graph engine 内置）
- 模板文件：16 → **25**（+9：外部化 Prompt 模板）
- 测试文件：50 → **57**（+7）
- 单元测试：754 → **912**（+158）
- CLI 命令：48 → **51**（+metrics-export / watch / workspace）
- 覆盖率：49% → **50.37%**

---

## v3.14.1 (2026-07-15)

代码质量全面优化：CLI 模块拆分 + 高复杂度函数重构 + 测试补齐 + 覆盖率基础设施 + 异常审查 + 文档完善。

### 代码重构

**CLI handlers 按功能域拆分（app/cli/handlers.py → _handlers/ 子模块）：**
- 1480 行单文件 → 80 行聚合层 + 4 子模块（`_wiki` / `_data` / `_content` / `_system`，最大 478 行）
- `_cli_main.py` 零改动，`COMMAND_HANDLERS` 向后兼容
- 所有 handler 函数及内部 helper 通过 `handlers.py` 重新导出

**analysis/service.py 高复杂度函数重构：**
- `_stage3_synthesize_directions()`（原 180 行，复杂度 ~43）→ 提取 `_s3_synthesize_direction_section()` 方法 + 5 个模块级辅助函数（`_s3_build_direction_index` / `_s3_index_briefs_by_direction` / `_s3_build_concept_boundaries` / `_s3_load_historical_context` / `_s3_extract_strategic_insights`）
- 新增 `_s3_format_briefs()` 静态方法，分离 brief 格式化逻辑

### 测试

- 687 → **754**（+67）：
  - 新增 `test_validation.py`（safe_int/float/parse_json/required_keys/get_str/get_list，32 用例）
  - 新增 `test_embedder.py`（TextEmbedder 构造/配置/向量提取，16 用例）
  - 新增 `test_llm_service.py`（GenerationResult 不可变性/LLMService 构造/generate 路由上下文，10 用例）
  - 新增 `test_navigation.py`（NavBuildResult/字符序列匹配/日期提取/死链检测，19 用例）

### 工程基础设施

- **pytest-cov 集成**：`pyproject.toml` 新增 `[tool.coverage.run]` / `[tool.coverage.report]`，基线覆盖率 49%，`fail_under = 49`
- **ruff BLE001 豁免**：`pyproject.toml` 新增 `[tool.ruff.lint]` 配置，项目级豁免盲 except（有意容错策略）
- **`retrieval/searcher.py`** 静默吞错处补充 `logger.warning`（Chunk 索引加载失败）

### 文档

- README 补充「开发环境」章节（安装/测试/项目结构图）
- CLAUDE.md 指标更新：测试 754 / 覆盖率 49% / 110 文件 / handlers 4 子模块
- 优化清单 memory 同步至当前状态

### 版本

- 产品版本 3.14.0 → **3.14.1**
- 协议版本保持 **3.9**（无新 CLI 命令）
- 数据版本不变

### 项目指标

- 源文件：106 → **110**（+4：_handlers/ 子模块）
- 测试文件：46 → **50**（+4）
- 单元测试：687 → **754**（+67）
- CLI 命令：48（不变）

---

## v3.14.0 (2026-07-15)

四方向全面优化：测试覆盖补齐 + 用量统计深化 + 图谱查询命令 + VIDEO 多模态。

### 新功能

**LLM 成本估算与预算预警（llm/usage_tracker.py）：**
- 新增价格表 `config/llm_pricing.json`（gitignored，`.example` 版本控制）：按 provider/model 配置每 1000 token 单价 + 货币 + 可选月度预算
- `load_pricing()` / `lookup_price()`：加载价格表并按 (provider, model) 匹配单价，支持 `_default` 兜底，缺失静默返回空表
- `UsageTracker.stats_with_cost()`：在 `stats()` 基础上按段估算成本，返回 `{rows(含 cost), unpriced_models, currency}`；部分定价时只计已定价模型成本，未定价模型单列
- `usage-stats --cost`：pretty 模式增列「估算费用」+ 货币 + 未定价模型提示；JSON 模式 rows 内含 `cost`
- `daily-start` 集成用量概要：payload 新增 `usage_summary`（今日/本周/本月调用与 token）+ 超预算时 `budget_warning`，纯本地读取零 LLM 调用

**知识图谱查询命令 `graph-query`：**
- `iris graph-query --op <neighbors|related|path|orphans|bridges|density>`，复用 `WikiGraph` 全部查询 API，零图谱逻辑改动
- 参数：`--node`（目标节点）/`--to`（path 终点）/`--hops`（邻居跳数）/`--min-degree`（桥接阈值）
- pretty 模式为每种 op 提供可读输出：邻居按类型分组、路径渲染为 `A →[关系]→ B`、密度报告表格、桥接节点跨类型标注
- 图谱未构建时提示先运行 `build-graph`

**VIDEO 多模态支持（complex_input/video_adapter.py）：**
- 新增 `VideoAdapter`：ffmpeg 均匀抽取关键帧（base64 → EncodedImage）+ ffmpeg 抽音轨 → Whisper 转写
- 优雅降级：ffmpeg 缺失抛 `VideoAdapterError`（上层降级为"暂不支持"）；whisper 缺失/无音轨时仅丢转写、保留帧分析
- `pipeline._stage2_video`：转写文字 + 帧图片组装为多模态 content 送 adv_model；既无帧也无转写时明确跳过
- 三阶段流水线 Stage 2 现覆盖 图片/PDF/DOCX/VIDEO 四类，移除 VIDEO "暂不支持" 占位路径

### 测试

- 554 → **687**（+133）：
  - 新增 `test_analysis_helpers.py`（analysis 纯函数）/`test_mindmap.py`/`test_scanner.py`/`test_trello_models.py`/`test_trello_formatter.py`/`test_trello_service.py`/`test_video_adapter.py`
  - 扩展 `test_deep_eval.py`（AccuracyVerifier）/`test_usage_tracker.py`（价格表+成本）/`test_graph.py`（graph-query handler）/`test_complex_input_pipeline.py`（VIDEO 路由与集成）
  - VIDEO 抽帧经真实 ffmpeg 端到端验证

### 版本

- 产品版本 3.13.0 → **3.14.0**
- 协议版本 3.8 → **3.9**（新增 `graph-query` 命令，命令集变化）

### 项目指标

- 源文件：105 → **106**（+1：video_adapter.py）
- 测试文件：40 → **46**（+6）
- 单元测试：554 → **687**（+133）
- CLI 命令：47 → **48**（+graph-query）

---

## v3.13.0 (2026-07-14)

### 新功能

**LLM 用量统计（llm/usage_tracker.py）：**
- 全新 `UsageTracker`：每次 LLM 调用自动记录到本地 SQLite（`data/llm_usage.db`），零新依赖（stdlib `sqlite3`）
- 捕获维度：时间戳 / 模型 / provider / 路由角色 / 匹配规则 / 输入 token / 输出 token / 是否多模态
- 捕获点在 provider 层汇聚：文本走 `_fallback_loop()`、`force_model` 直连、多模态走 `generate_multimodal()`，三条路径全覆盖
- 从 API 响应提取 usage：OpenAI 兼容用 `prompt_tokens` / `completion_tokens`，Anthropic 用 `input_tokens` / `output_tokens`
- 聚合查询支持四种时间粒度：`day` / `week` / `month` / `year`（SQLite `strftime` 分组，无额外依赖）
- 支持分模型统计（`stats_by_model()`）与汇总统计（`stats()`），可按模型名和起始日期过滤
- 全程静默失败：DB 初始化 / 写入 / 查询任一失败均不影响主流程 LLM 调用

**CLI 命令 `usage-stats`：**
- `iris usage-stats [--by day|week|month|year] [--model <name>] [--since YYYY-MM-DD] [--pretty]`
- `--pretty` 输出：时间段汇总表（调用次数 / 输入 / 输出 / 合计 token）+ 末段的按模型分布明细
- 默认 JSON 输出便于程序消费

### 数据流改造

- `LLMResponse` / `GenerationResult` 新增 `prompt_tokens` / `completion_tokens` 字段（默认 0，向后兼容）
- `_call_openai_chat` / `_call_anthropic` / `_dispatch_provider_call` / `_fallback_loop` 返回值由 `str` 改为 `(text, prompt_tokens, completion_tokens)` 元组，token 数逐层透传至 tracker

### 覆盖范围说明

- 仅统计 Iris 自身经 provider 发出的 LLM 调用（CLI 命令 + 调用 CLI 的 Skill）
- 不含：Claude Code 本体（走 Anthropic API，在 Iris 之外）、Whisper 音频转写（非 token 计费）、飞书接口

### 测试

- 538 → **554**（+16）：新增 `test_usage_tracker.py` 覆盖初始化 / record / 四种时间粒度聚合 / 模型过滤 / 起始日期过滤 / 按模型分布 / 非法粒度校验
- `test_provider_fallback.py`：mock 返回值同步为 `(text, pt, ct)` 元组

### 项目指标

- 源文件：104 → **105**（+1：usage_tracker.py）
- 测试文件：39 → **40**（+1）
- 单元测试：538 → **554**（+16）
- CLI 命令：46 → **47**（+usage-stats）

---

## v3.12.1 (2026-07-14)

### 性能优化

**知识图谱单次扫描（wiki/graph.py）：**
- `refresh()` 重构：Wiki 目录只扫描一次，节点层 / wikilink 边层 / LLM 边层共用同一份 `WikiPageInfo` 列表，消除 3 次重复 I/O
- `build_nodes()` / `extract_relations()` / `_find_changed_pages()` 新增 `_pages` 参数接受预加载列表
- `_out_edges` 持久有向索引：`_rebuild_adjacency()` 同时维护，`find_path()` 不再每次调用重建 O(E) 临时字典
- `BacklinkBuilder.build_from_wiki_pages()` 新方法：从已加载 `WikiPageInfo` 构建反向引用索引，零文件 I/O

**QA 图谱惰性缓存（qa/service.py）：**
- 新增 `_graph_cache` 字段：会话内首次调用时加载 `WikiGraph`，后续命中缓存不再重复读磁盘
- 提取 `_get_graph()` 方法统一管理加载逻辑

### Bug 修复

**三元组解析兼容性（wiki/graph.py）：**
- `_parse_triples()` 重写：优先尝试整体解析 JSON 数组/对象，回退至逐行解析；原仅支持逐行格式，LLM 返回 JSON 数组时全部丢失
- 提取 `_triple_obj_to_edge()` 辅助方法，消除重复校验逻辑
- 行解析容错：跳过 `[`、`]` 单行，去除行尾逗号

**DOCX 流水线优化（complex_input/pipeline.py）：**
- DOCX 路径跳过 Stage 1 LLM 调用（Stage 2 已是纯文本提取，不需要 adv_model 指令）
- PDF 失败检测改为 `any_pdf_success` flag，替代原本依赖 `total_images > 0` 的脆弱逻辑

**其他修复：**
- `qa/service.py`：移除图谱上下文中的 emoji 前缀；`list[str]` → `List[str]` 修复 Python 3.9 兼容
- `wiki/graph.py _rebuild_adjacency()`：LLM 边现在也在无向邻接表中双向展开（原只有 wikilink 边双向），修复 `neighbors()` 结果不全问题

### 工程

- `pyproject.toml` 新增 `[tool.pytest.ini_options]`：`pythonpath = ["src"]`，`testpaths = ["tests"]`，无需 `PYTHONPATH` 前缀即可运行测试
- `BacklinkIndex.unique_inbound_edges`：字段重命名（兼容旧 `total_links`），语义更准确

### 测试

- 532 → **538**（+6）：`test_backlink.py` 覆盖 `build_from_wiki_pages()`；`test_graph.py` 覆盖 JSON 数组解析 / `_out_edges` 索引 / `_triple_obj_to_edge`；存量测试修复（`test_biweekly_collector` / `test_pydantic_config`）

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
- `src/iris/analysis/service.py`：`_SUB_AREA_KEYWORDS` 字典清空，移除 11 个硬编码内部业务子领域词条（11 个内部业务子领域词条，内容含公司业务信息，此处不列原文），改为用户自定义注释说明；移除注释中员工姓名
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
- `src/iris/analysis/service.py`：`_SUB_AREA_KEYWORDS` 字典清空，移除 11 个硬编码内部业务子领域词条（11 个内部业务子领域词条，内容含公司业务信息，此处不列原文），改为用户自定义注释说明；移除注释中员工姓名
- `src/iris/wiki/navigation.py`：`EXTERNAL_CONCEPT_PATTERNS` 从 15 个业务特定正则缩减为 2 个通用示例，添加用户自定义说明
- `src/iris/wiki/term_extractor.py`、`src/iris/analysis/_biweekly_types.py`：移除注释/文档字符串中的员工姓名与内部项目代号
- `templates/prompt/biweekly_stage3_direction.md`、`biweekly_stage1_filter.md`：示例中的内部项目代号替换为通用占位符
- `scripts/extract_weekly_reports.py`：移除注释中的员工姓名及真实企业邮箱域名

**测试数据脱敏（9 个测试文件）：**
- 员工真实姓名（团队成员A / 团队成员B / 团队成员C / 团队成员D / 团队成员E / 团队成员F 等）→ 张三 / 李四 / 王小明 / 王五 / 赵六
- 内部项目代号（5 个内部项目代号）→ 项目Alpha / Beta / Gamma / Delta / Epsilon
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

### extract-travel-invoice PDF 文字直接提取 + 表格输出格式

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

### extract-travel-invoice 代码审查修复（8 项）

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

**问题**：白名单 12 人本周实际 10 人提交周报，`extract-weekly-reports` CLI 仅扫到 3 人，静默漏掉 7 人（团队成员B、团队成员X、团队成员A、团队成员G、团队成员D、团队成员H、团队成员I）。根因 `LarkMailScanner.scan_triage` 走「folder + time_range」list 路径，这些成员的周报带 `IMPORTANT` 标签、散落在 priority/自定义文件夹并落在 list 首屏 50 封之外，list 路径捞不到；search 路径（`--query`，跨全文件夹）可一次命中。

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
- S6: ASR Prompt 示例脱敏 — `asr_hotwords.py` / `term_extractor.py` / `asr_prompt_optimizer.py` 输出格式示例中的真实员工姓名（团队成员J、团队成员E）和项目名（两个内部项目代号）替换为通用占位；`biweekly_stage3_direction.md` 反例示范脱敏；`biweekly_stage4_assemble.md` 移除 LLM 无法执行的条件签名指令

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
- 人物歧义处理：6 人手动排歧，飞书信息统一修正

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
