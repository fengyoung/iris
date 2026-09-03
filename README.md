# Iris 3.30.0

工作知识助手 — 个人知识库（Obsidian Wiki）与飞书知识库集成。

## 版本

**v3.30.0** — [三阶段质量优化](CHANGELOG.md)：① 开源冲刺——F401 门禁 + 395 处未使用导入清理、测试数据残留泛化；② 代码质量——`corrector.py` 1,542→873 / `live.py` 1,044→871 模块拆分（旧导入路径兼容），ruff C901 `max-complexity=20` 门禁并重构全部 11 个超限函数（最高 53），库层 print→logger；③ 长期改进——`IrisError` 统一异常基类（19 个异常挂接、原标准库父类保留）、mypy 非阻断基线 193 errors（`make typecheck`）、新拆模块与 `sync_memory` 专项单测 +257。附带修复循环导入 `config.loader ↔ core/__init__` 与 `MergeBuffer` 首句空 Flush。覆盖率 65.82%→68%（`fail_under` 55→65），全量 3,261 用例通过。协议版本 3.21（不变）。

**v3.29.1** — [Wiki 增量更新指纹预检补全](CHANGELOG.md)：修复 daily-start 卡死——`update_all_pages()` 此前对全部 241 页每页无差别调 LLM 判断是否需更新（并发 6 路），压垮中转导致超时/无 content；现补全 v3.28.4 的 `is_wiki_stale()` 指纹预检（discovery/metrics 已用但该路径漏用）：加载 `chunk_hash_index`，源文档未变页面零 LLM 跳过（241 页全跳过约 5 秒），从「无差别全量重扫」退化为「按源文档指纹的精准增量」。协议版本 3.21（不变）。

**v3.29.0** — [飞书消息图片理解沉淀](CHANGELOG.md)：新增 `MessageImageAnalyzer`（`feishu/image_analyzer.py`），让 feed-collect 与 chat-digest 两条消息管道真正「看」图——图片消息下载后经多模态 LLM 生成中文描述并注入下游，而非把 `[Image: img_v3_xxx]` 当噪音文本或 `[图片]` 占位。`FeishuClient.download_message_image()`（`im +messages-resources-download`）补齐消息图下载；配置 `image_understanding:{enabled,max_per_run}` 控制成本 + 按 `image_key` 跨管道缓存。已知边界：只处理 `msg_type=="image"` 独立图，post 内嵌图不单独分析，单张失败降级占位。

**v3.28.5** — [LLM 调用统一到 LLMService](CHANGELOG.md)：消除「已建 LLMService 又 `get_provider()` 绕过响应缓存」的残留路径（scripts 两脚本、ASR hotwords/extractor、检索 planner/enhanced、build-asr-prompt 共 7 文件），`provider.generate(LLMRequest(...))` 统一适配为 `llm.generate(prompt, route_context=...)`；保留 corrector `_provider` 测试降级、route-model 查询路由、get_provider() 诊断等非绕过路径。协议版本 3.21（不变）。

**v3.28.4** — [提醒引擎项目停滞判定兜底 + 模板占位符根因修复](CHANGELOG.md)：项目停滞信号三重兜底（指纹 → SOURCE 同名文档 → 周报正文内容级匹配），消除软硬一体/XRay/视频稽查/数据标注平台/售后归因/AI巡检等活跃项目误报；`project_stall_ignore` 配置支持已完结/已移交项目静默；修复模板中 `[[Wiki-链接]]` 示例文字被 LLM 复制进页面（根因+存量清零）。app 配置版本 **3.7**，协议版本 3.21（不变）。

**v3.28.3** — [开源前信息安全复审](CHANGELOG.md)：变更日志/文档/测试/模板全库二次脱敏（真实姓名、业务指标、内部项目名泛化）；git 历史作者邮箱迁移 noreply；CI 最小权限 + `.gitignore` 纵深防御。协议版本 3.21（不变）。

**v3.28.2** — [batch-transcribe 批量会议纪要修复](CHANGELOG.md)：`TranscribeMeetingPipeline.run_batch` 缺失导致命令一执行即抛 AttributeError（handler 调用时方法从未实现）——补全实现（按扩展名分流音频 Whisper 转写/已有转写文本、单文件失败不中断批量、批量层 TaskReporter 埋点），handler 补传 `--to-source`，回归测试 +2，并真实端到端验证批量路由归档。协议版本 3.21（不变）。

**v3.28.1** — 两条修复线：① [LLM 思考文本污染修复](CHANGELOG.md) — `content` 为空时不再静默回退返回 `reasoning_content`（思考模型 max_tokens 耗尽时思考文本曾直接写入双周报产物），改为抛错走重试/降级链；Stage 4b 质量审查失败安全回退组装稿；② [深度审查批次 1 数据止损](CHANGELOG.md) — 6 路并行代码审查后修复 6 个 P0（记忆裁剪方向反转、增量切块丢 chunk、向量索引死向量/旧向量、wikilink 注入吞正文、feed pending 覆盖、人物页清空手工 email）+ 4 个 P1 功能坏死（文件日志、会话挖掘懒触发、建议提问、会议任务面板埋点）。回归测试 +19。**升级后需执行一次 `build-chunks --write-summary` + `build-vector-index --force-rebuild`。**协议版本 3.21（不变）。

**v3.28.0** — [全项目工程治理](CHANGELOG.md)：修复 SQLite 连接泄漏与 FileLock inode 竞态；统一原子写入和安全配置契约；向量索引按完整代际发布；补齐 `workspace` CLI、daily-start 图谱状态、跨进程 LLM 缓存治理；统一 CI/Makefile/pre-commit 质量门禁。协议版本 **3.21**，app 配置版本 **3.6**。

**v3.27.2** — [LLM 配置修复与新视觉模型默认](CHANGELOG.md)：`find_model_by_name` Pydantic 兼容修复（回归修复 v3.11 迁移后 force_model 对真实配置失效 + SecretStr 显式解包）；adv_model 新默认 `deepseek-v4-flash-vision-exp`（实验性视觉模型，priority 70 最高优先级，qwen3.8-max 降为第 2 优先级）；iris-feishu-import 批量导入用法修正（`--url` 不可重复传参，改逗号分隔）。验证：LLM 相关 107 全过，ruff 零告警。协议版本 3.20（不变）。产品版本 3.27.1→**3.27.2**。

**v3.27.1** — [双周报生成写作风格固化](CHANGELOG.md)：重写 Stage 3 合成模板 — 总结段改逐项目「目标→思考→决策→下一步」（「我们」视角，含正/反示例）；关键进展项目级聚合（每 sub_area 1 个加粗条目 + ≤3 子项，挑选最关键进展）；DEFAULT_STYLE_GUIDE 同步（默认生成即该风格）+ 防回归测试 +6。背景：某期首版总结宽泛空洞、关键进展过细；Stage 3 合成 240s 超时静默丢弃末方向（素材未缺却输出「无实质进展」）。验证：biweekly 相关 133 全过，ruff 零告警。协议版本 3.20（不变）。产品版本 3.27.0→**3.27.1**。

**v3.27.0** — [任务面板 `iris task-panel`](CHANGELOG.md)：Web 只读展示层查看 iris 任务状态与进程（操作仍在 CC CLI）。常驻守护（start/stop/status/install，launchd 崩溃自动拉起）+ 零新依赖（stdlib ThreadingHTTPServer + 单 HTML + 原生 JS 轮询 + 深色主题）+ 混合数据源（TaskReporter 埋点上下文管理器 + ps 探测兜底 + stale 判定，存储 `data/tasks/` 原子写 + flock 历史）+ 首批埋点接入 5 命令（daily-start 8 阶段 / build-chunks / build-wiki / transcribe-meeting / meeting-live-assistant）。端口默认 8765，`--port` > `IRIS_TASK_PANEL_PORT`。测试 taskpanel 专项 69 全过。协议版本 3.19→**3.20**。产品版本 3.26.3→**3.27.0**。

**v3.26.2** — [meeting-live-assistant 面板双主题视觉方案](CHANGELOG.md)：新模块 `_theme.py`（Theme + DARK/LIGHT 两套 ANSI 256 色配色）· 整帧全区填充（底色 dark `#262626` / light `#E4E4E4`，形成「控制台仪表盘」沉浸观感）· 语义色贯穿（要点/决策✅绿 · 提议💬黄 · 待定❓灰 · 风险⚠橙 · 冲突🔥红 · 话题📌青 · 待办📋蓝 · 说话人🗣紫 · 建议提问💡亮黄 · 告警红底亮黄字 · VU 渐变）· 布局安全（纯文本算宽 + ANSI 后包裹，超宽自动降级）· 配置 `assistant.panel_theme: dark|light`。测试 assistant 专项 186（+8），ruff 零告警。协议版本 3.19（不变）。产品版本 3.26.1→**3.26.2**。

**v3.26.1** — [meeting-live-assistant 深度审查后全量优化](CHANGELOG.md)：29 项四阶段优化 + 3 项评估修复。P0 可用性（ASR 崩溃自动重初始化 / `s` 热键推送暂停恢复 / 面板 LLM 阶段指示 + 系统告警区）· P1 体验（面板宽度自适应 / VU 电平条 / USB 麦克风热插拔重连 / 超长会议保护 / 剪贴板遗留清理）· P2 能力（建议提问事件驱动触发 / 批内多说话人提示 / 热词校验 / 噪声地板冻结 / 空会议清理 / `max_segment_chars` 生效）· P3 工程（buffer O(n²)→O(1) / 增量阶段性总结可见化 / `m` 键手动话题边界 / forced_cut 连续发言标注 / 混合文本噪音判定）+ 评估修复（死代码删除 / 建议节流 / 总结渲染进文档）。测试 assistant 专项 178（+5），全量 unit 1,428 全过，ruff 零告警。协议版本 3.19（不变）。产品版本 3.26.0→**3.26.1**。

**v3.26.0** — [meeting-live-assistant 升级为「实时 AI 会议参谋」](CHANGELOG.md)：四层 12 项能力（防御层：噪音门控/容量控制/内容感知合并 · 理解层：话题检测+去重/决策置信度/冲突检测 · 交互层：洞察推送/热键 · 沉淀层：议程注入/待办提取/按话题文档）+ 说话人区分全模块（SpeakerLabel + VAD 间隙门控 + LLM 后验 + per-speaker 上下文）+ 核心修复（VAD 尾部丢失 40ms 帧切片、LLM 降级链 deadline 压入 timeout + 熔断器阈值 2）。测试 2,858 全通过。协议版本 3.19（不变）。产品版本 3.24.3→**3.26.0**。

**v3.24.2** — asr-corrector 写回修正：full 模式跳过词典写回仅 LLM 最终结果一次输出（消除两次写回闪烁）；取消 Cmd+A 全选覆盖、全场景恢复逐字符 Delete 删除（Cmd+A 跨 App 不可靠导致原文残留+校正追加=文本重复）。协议版本 3.18（不变）。

**v3.24.0** — [meeting-live-assistant × asr-corrector 全面优化](CHANGELOG.md)：写回机制重构（快照校验 + 成功才更新状态，根除长文本超时截断）+ 反馈反向优化管线时序修正（补充热词/提升发现/僵尸淘汰全部生效）+ 替换词典交叉冲突防护（音近人名不误伤）+ assistant 预取原子化（futures 注册与 pending 原子，双跑消除）+ 热键监控器 Event 化（启动零等待 + 唤醒正确 run loop）+ LLM 调用治理（deadline 补全 + 并发上限 + 相似度门槛拦幻觉）+ `--max-asr-length` 参数化。测试 +23（合计 2,770 用例）。协议版本 3.17→3.18。

**v3.23.3** — [meeting-live-assistant 全量优化](docs/meeting-live-assistant-usage.md)：双段流水线（段 N 分析期间段 N+1 的深度校正/检索已并行，关键路径 25s→15s）+ 短段门控（<15 字确认语零 LLM 成本）+ `--fast-only` 仅词典模式；退出路径加固（尾段不再丢 + 原子写并发防御）；AI 会议总结（退出时一次 LLM 写文档「会议总结」区）；检索链路 deadline 根治（全链路唯一无 deadline 的 LLM 调用点）；长段支持（2000 字覆盖 120s 长语音）+ 幽灵段抑制 + 限时去重；互斥对称（asr-corrector 启动也探测助理）。测试 +34（合计 2,747 用例全过）。协议版本 3.16→3.17。

**v3.23.2** — wiki-update 备份文件全链路过滤：`*.bak.1.md` 备份被 5 处计入（Wiki 检索结果 / lint 页面计数 / wikilink 标题索引 / status 页面数 / 提醒引擎重复停滞信号），统一按 `.bak.` 过滤；回归测试 +3（合计 2,713 用例）。协议版本 3.16（不变）。

**v3.23.1** — 遗留修复 + 使用指南：① asr-corrector Ctrl+C 修复（Python 3.13 SIGINT 无法中断 `time.sleep`，`run_forever` 显式注册 handler，Ctrl+C 可靠优雅退出）；② `scripts/verify_hotkey_inject.py` 纳入版本控制（CGEventPost 热键注入验证工具，须 `--keycode 61` 右 Option）；③ 会议助理[使用指南](docs/meeting-live-assistant-usage.md)。测试全量 2,709 用例（2,708 通过）。协议版本 3.16（不变）。

**v3.23.0** — 实时会议助理 `meeting-live-assistant`：会议中按住 vocotype 热键说话，松开即逐段转写 → ASR 校正（词典 + LLM）→ 知识库检索 → LLM 分析（要点/风险/问题/决策点/建议提问）→ 终端面板实时提示 + Markdown 过程文档实时写入（`--output` > `assistant.output_dir` > `data/meeting-live/`）。积压丢弃策略（分析慢于说话时只处理最新段）+ 与 asr-corrector 运行时互斥（独占剪贴板）。测试 +63（合计 2,709 用例）。协议版本 3.15→3.16。

**v3.22.5** — ASR 校正引擎热键门控修复：① CGEventTap 启动失败（辅助功能权限缺失）时热键门控「卡死为全跳过」— `run_forever` 失败后置空监听器，降级为内容特征判定（`_is_asr_text` + 富文本检查兜底），不再误拦真实 ASR 输出；② 监听窗口与热键按住时长挂钩（`max(3s, min(按住时长, 120s))`）— vocotype 松开热键后才转写，1 分钟长语音的剪贴板结果不再因超时被跳过。测试 +13（合计 2,646 用例）。协议版本 3.15（不变）。

**v3.22.4** — 周报提取主题日期不一致自动标注：`extract_weekly_reports.py` 新增 `_subject_date_mismatch_note`（检测主题 `YYYYMMDD` 日期 ≠ 邮件发送日期，覆盖复制标题忘改日期场景，如团队成员 08-07 主题仍写 20260731），不一致时邮件信息栏自动加 ⚠️ 标注；一致或无日期不加注。测试 +4（合计 2,633 用例）。协议版本 3.15（不变）。

**v3.22.3** — 知识库全面体检修复：检索索引死数据清除（`chunker.py` 全量重建误追加已删除文档旧 chunk，实测 4,636 死 chunk 占 45.1%，重建后 chunk 10,290→5,939 / 向量 10,321→5,939 / 覆盖率 201.7%→100%）+ 知识图谱 LLM 边清零修复（`graph.py` 增量刷新丢边，从 relations 缓存恢复 591 条，验证 591→633 不再丢）+ deep-eval CLI 参数补齐（`--page-filter`/`--sample-rate`）+ 回归测试 +4。协议版本 3.15（不变）。

**v3.22.2** — wikilink 注入残留清理：修正 chat_digest / extract_weekly_reports 过时注释（v3.21.0 已移除管道 wikilink 注入）+ 删除 wiki_root 死参数链（`_build_markdown`/`generate_content` 参数与透传链共 10 处）。协议版本 3.15（不变）。

**v3.22.1** — Wiki 候选发现噪音过滤（`is_noise_candidate`，过滤周报模板固定章节标题）+ 知识图谱全量重建边去重修复（`full=True` 时去重基准只保留 wikilink 边，防止 LLM 边逐次退化）。协议版本 3.15（不变）。

**v3.22.0** — 合并 0802-alpha → main，开源信息泄露治理全库脱敏：`IRIS_BOT_USER_ID` 真实 open_id 改环境变量（未配置跳过飞书推送）+ 团队名单与 `dept_op_keyword` 默认值清空（app.json 配置驱动，null 防御）+ 真实人名/企业邮箱/OKR/项目名/业务指标全库泛化（生产代码 + 模板 + Skill + 文档二次脱敏）+ 测试断言同步（61 文件）。协议版本 3.15（不变）。

**v3.21.1** — SOURCE 归档适配全面修复：双周报文件名改为日期前缀（正确归档到 `YYYY/` 子目录）+ 会议纪要刷新脚本递归查找 + 风格源文件递归查找 + feed 简报使用 `resolve_source_archive_path` + 移除 pipeline 扁平路径死代码 + 5 个 Skill 文档 SOURCE 路径更新（iris-okr-check/iris-feed/iris-report/iris-feishu-import/iris-meeting）。协议版本 3.15（不变）。

**v3.21.0** — SOURCE 元数据工程：新增 `frontmatter-batch` 批量补全命令（新模块 `core/frontmatter_batch.py`，正则快速通道 + LLM 深度通道 + wikilink 可选注入 + 备份恢复，9 类目录字段映射）+ wikilink 注入收敛（4 管道移除，统一由批量命令按需注入）+ 周报按月归档（YYYYMM 子目录）+ 双周报 frontmatter 注入。测试 +65（合计 2,626 用例）。协议版本 3.14→3.15（新增 frontmatter-batch 命令）。

**v3.20.2** — SOURCE 文档质量系统性提升：YAML frontmatter 标准化（新增 `core/frontmatter.py`，4 个 CLI 管道注入元数据）+ wikilink 自动注入引擎（新增 `wiki/wikilink_injector.py`，零 LLM 成本）+ 成员周报 Prompt 增强 + 质量门禁。测试覆盖率系统提升 59.87% → 62.82%（+253 用例，9 个新测试文件）。测试合计 2,561 用例。协议版本 3.14（不变）。

**v3.20.1** — deep_eval 配置路径改进：chunk 摘要文件路径由硬编码 `main_source_chunk_summary.json` 改为根据 `default_source` 动态加载，便于多数据源切换。协议版本 3.14（不变）。

**v3.20.0** — iris-feed 文档提取（Step 5）：从话题消息中自动收集飞书文档链接，调用 FeishuDocConverter 转换为本地 Markdown，简报中关联引用。新增 `--no-extract-docs` 参数。新模块 `_doc_extractor.py`（199 行）+ 37 测试用例。协议版本 3.14。

**v3.19.26** — 检索与知识库时效性四项优化：chunk 切块重叠（150 字）+ Wiki source_fingerprint 源文档指纹追踪（过时判定精准化）+ 向量索引模型不匹配硬失败与 `--force-rebuild` + 主动提醒引擎 `reminders`（栏目断供/周报缺失/项目停滞，零 LLM 成本）。顺带修复 PDF 切块 0 chunk、hash 索引不更新、RRF 配置未生效三处预存问题。协议版本 3.13。

**v3.19.25** — iris-feed 简报质量跃升：两阶段 LLM 架构 + 去截断 + Prompt 重写 + 结构化输出增强。合并 0728-beta：输入截断保护 + 死代码清理。

**v3.19.18** — 知识库质量全面加固 + 代码质量全面加固：wiki-pipeline 已有页面检测修复（0%→85%）、知识图谱 LLM 语义关系提取（+986 边）、断链清零（11→0）、零出链清零（27→0）、向量索引构建（9,019 条）、LLM 用量追踪体系完善（embedding 纳入 + CLI/Skill 来源标记）；P0 静默异常修复、P1 DRY 消除、P2 工程增强（pip-audit / constraints.txt / git tag 发布流程）、P3 测试精化（单元测试 526→794）+ lint 清零。13 + 11 文件。全量 1,858→1,948 测试通过。

**v3.19.17** — SOURCE 目录按月/年归档：9 目录 3 级策略（yearly 5 目录 + monthly 4 目录），自动按文件名生成年月子目录；740 文件已搬迁。v3.19.16 合并 0722-alpha：多 Agent 并发安全 + iris-okr-check Skill。v3.19.15 多 Agent 并发安全；记忆自动更新引擎。9 文件，+284 / -15 行。全量 1,858 测试通过。

**v3.19.16** — 合并 0722-alpha：多 Agent 并发安全（FileLock 推广至 6 处 RMW + SQLite WAL + Agent 记忆隔离 + 进程注册表）+ 新增 `iris-okr-check` 项目级 Skill（OKR 双周逐项检查）。v3.19.15 多 Agent 并发安全；记忆自动更新引擎。19 文件。全量 1,858 测试通过。

**v3.19.15** — 多 Agent 并发安全：FileLock 推广至 6 处 RMW + SQLite WAL 模式 + Agent 记忆隔离（`IRIS_AGENT_ID`）+ 进程注册表 + 日志归档 TOCTOU 修复。v3.19.14 记忆自动更新引擎：LLM 双通道提取 + 会话模式挖掘 + 全自治生命周期。3 轮审查，19 文件 / +215 -208 行。全量 1,858 测试通过。

**v3.19.14** — 记忆自动更新引擎：Phase 1-3 全面实施，29 新增测试。**v3.19.13** — ASR SIGINT 保护。**v3.19.12** — LLM 思考模式关闭。**v3.19.11** — 五大方向优化（19 文件，+887 / -64 行，1,829 测试）。

## 开发路线

| 步骤 | 内容 | 状态 |
|------|------|------|
| **步骤 1** | 最小化迁移：非知识库能力迁移（搭骨架） | ✅ 完成 |
| **步骤 2** | 本地知识库重构（新 Wiki 体系 + 能力重构） | ✅ 完成 |
| **步骤 3** | 飞书 → 本地知识库提炼 | ✅ 完成 |

## 快速开始

```bash
# 安装
pip install -e .

# Iris 是仓库型应用；从其他目录启动时可显式指定仓库根目录
export IRIS_PROJECT_ROOT=/path/to/iris3

# 配置
cp .env.example .env
cp config/app.json.example config/app.json
cp config/llm.json.example config/llm.json
cp config/data_source.json.example config/data_source.json
# 编辑上述文件，填入 API Key 和路径
# llm.json 已预配置 DeepSeek (文本) + 百炼 Qwen (多模态) 双 Provider
# 可选：cp config/llm_pricing.json.example config/llm_pricing.json 后填入单价，启用 usage-stats --cost 成本估算

# 初始化知识库
python scripts/run_cli.py scan-source
python scripts/run_cli.py build-chunks
python scripts/run_cli.py build-vector-index   # 需配置 embedding

# 日常维护
python scripts/run_cli.py daily-start
```

## 核心能力

| 类别 | 命令 | 说明 |
|------|------|------|
| 数据管道 | `scan-source`, `build-chunks`, `build-vector-index`, `frontmatter-batch` | 文档扫描 / 切块 / 向量索引 / 批量补全 YAML frontmatter（正则+LLM+wikilink+备份恢复） |
| 检索问答 | `search`, `ask` | 混合检索（BM25 全文 + 向量）+ LLM 问答（支持图文输入，图谱增强） |
| Wiki | `discover-wiki`, `build-wiki`, `wiki-update` | 发现 / 生成 / 增量更新 |
| 知识图谱 | `build-graph [--full] [--page]`, `graph-query --op ...` | 实体节点 + wikilink 边 + LLM 关系提取，增量更新；邻居/相关/路径/孤页/桥接/密度查询 |
| 质量保障 | `wiki-lint`, `wiki-lint --fix`, `deep-eval` | 结构检查 + 孤页检测 + 内容准确性/全面性校验 |
| 报告 | `build-report`, `build-mindmap`, `build-biweekly-report` | 专题报告 / 思维导图 / 双周报 |
| 会议 | `transcribe-meeting`, `batch-transcribe`, `build-asr-prompt` | 转录纪要 / 批量处理 / ASR 三段校正 |
| 飞书 | `feishu-doc-convert`, `chat-digest` | 文档转换 / 聊天记录提炼 |
| 记忆 | `memory-*`, `working-set`, `sync-memory` | 记忆管理 / 工作上下文 |
| 人物 | `enrich-persons` | 飞书通讯录自动补充人物 Wiki 的部门/邮箱信息 |
| 工具 | `process`, `trello`, `extract-weekly-reports`, `extract-travel-invoice` | 富媒体处理（图片/PDF/DOCX/视频）/ 看板 / 周报提取 / 行程单报销 |
| 用量 | `usage-stats [--by day/week/month/year] [--cost]` | LLM 调用/token 消耗统计（分模型 + 汇总，多粒度聚合，可选成本估算） |
| 提醒 | `reminders` | 主动提醒：栏目断供 / 成员周报缺失 / 项目停滞（零 LLM 成本，daily-start 已集成） |
| 系统 | `daily-start`, `check-config`, `status`, `diagnose`, `workspace` | 日常维护（含图谱增量刷新）/ 配置检查 / 工作空间查看 |
| ASR 校正 | `asr-corrector`, `asr-audit`, `asr-report` | vocotype 实时语音转写纠错润色（[使用指南](docs/asr-corrector-usage.md)） |
| 会议助理 | `meeting-live-assistant` | 实时 AI 会议参谋：本地麦克风转写（FunASR）+ 逐段提炼要点/风险/决策/建议提问 + 话题追踪 + 说话人区分 + 洞察推送 + 热键 + 按话题过程文档（[使用指南](docs/meeting-live-assistant-usage.md) · [方案设计](docs/meeting-live-assistant-design.md)） |
| 任务面板 | `task-panel` | Web 只读展示 iris 任务状态与进程：常驻守护 + 任务埋点 + 探测兜底（[使用指南](docs/task-panel-usage.md) · [方案设计](docs/task-panel-design.md)） |

工程可靠性与运维约定见 [可靠性设计](docs/engineering-reliability-design.md) 和 [可靠性使用指南](docs/engineering-reliability-usage.md)。

## 知识库结构

```
SOURCE/                     LLM-WIKI/
├── 01-目标管理/             ├── 01-领域/    (领域知识地图)
├── 02-部门管理/             ├── 02-概念/    (核心概念术语)
├── 03-方案报告/             ├── 03-项目/    (项目知识沉淀)
├── 04-讨论思考/             ├── 04-人物/    (团队成员画像)
├── 05-会议纪要/             ├── index.md   (总索引)
├── 06-我的周报/             └── changelog.md
├── 07-成员周报/
├── 08-参考资料/
└── 09-工作简报/
```

## 模型配置

| 角色 | 默认模型 | 提供商 | 能力 | 降级链 |
|------|---------|--------|------|--------|
| `base_model` | deepseek-v4-flash | DeepSeek | 纯文本 | → deepseek-v4-pro |
| `adv_model` | deepseek-v4-flash-vision-exp | DeepSeek | 文本 + 图片 | → qwen3.8-max → qwen3.7-flash-2026-07-15 → qwen3.7-flash → qwen3.7-plus-2026-05-26 |

路由规则（8 条）：用户显式指定 → 多模态输入 → Prompt 生成 → 复杂分析 → Wiki 重建 → 问答 → 文本兜底。

## 技术栈

- Python 3.11+
- OpenAI 兼容 LLM API（DeepSeek / 百炼 Qwen 多模态）
- Pydantic v2（配置类型安全校验）
- lark-cli（飞书接口，步骤 3）
- macOS Keychain（可选密钥存储）
- PyMuPDF / python-docx（PDF/DOCX 处理）
- ffmpeg（视频抽帧/抽音轨，视频处理必需）+ openai-whisper（音轨转写，可选）
- 2,970 个测试用例（150 个测试文件），覆盖率 65.82%

## 开发环境

```bash
# 克隆并安装（含开发依赖）
git clone <repo-url> && cd iris3
pip install -e ".[dev]"

# 快速命令（Makefile）
make test            # 运行全部测试
make test-unit       # 运行 unit 标记测试
make test-integration # 运行 integration 标记测试
make test-cov        # 运行测试 + 覆盖率报告
make lint            # Ruff 代码检查
make lint-fix        # Ruff 自动修复
make format          # 代码格式化
make clean           # 清理缓存

# 或直接使用 pytest
python -m pytest tests/ -q
python -m pytest tests/ -q --cov=iris --cov-report=term

# 提交前检查（pre-commit）
pre-commit install   # 安装 Git hooks
pre-commit run --all-files  # 手动全量检查

# 配置（参考快速开始章节）
cp config/*.json.example config/  # 然后编辑各 .json 填入实际值
```

### 项目结构

```
iris3/
├── src/iris/           # 27 模块
│   ├── app/cli/        # CLI 入口 + 67 个公开命令
│   ├── analysis/       # 分析服务（报告/思维导图/双周报）
│   ├── complex_input/  # 多模态三阶段（图片/PDF/DOCX/VIDEO）
│   ├── config/         # 配置加载 + Pydantic 校验
│   ├── core/           # 类型/锁/存储/Agent 适配
│   ├── evaluation/     # Wiki 深度评估
│   ├── feed/           # 信息汇聚管道（飞书→话题→简报）
│   ├── feishu/         # 飞书文档转换 + 聊天提炼
│   ├── ingest/         # 文档扫描 + 切块
│   ├── llm/            # Provider/路由/LLMService/用量统计
│   ├── memory/         # 记忆系统（6 子模块）
│   ├── output/         # 格式化 + DOCX 输出
│   ├── qa/             # 检索增强问答
│   ├── retrieval/      # BM25 + 向量 + RRF 混合检索
│   ├── trello/         # Trello 看板
│   ├── utils/          # 工具函数
│   └── wiki/           # Wiki 体系（最大模块，含图谱/ASR/反向引用）
│       └── asr/         #   ASR 提示词子系统（术语提取/热词/Prompt优化/版本管理）
├── scripts/            # CLI 入口 + 委托脚本
├── templates/          # Prompt / Wiki 模板
├── tests/              # 2,970 用例（150 文件）
│   ├── unit/           #   纯逻辑单元测试（1,580 用例）
│   └── integration/    #   集成测试（245 用例）
├── config/             # *.json gitignored，*.example 版本控制
├── .github/workflows/  # CI 流水线（Python 3.11-3.13 矩阵）
├── Makefile            # 常用开发命令
├── Dockerfile          # 开发容器
└── pyproject.toml      # 项目配置 + pytest/coverage/ruff 设置
```

## 版本历史

| 版本 | 日期 | 要点 |
|------|------|------|
| **v3.22.4** | 2026-08-10 | 周报提取主题日期不一致自动标注：`_subject_date_mismatch_note` 检测主题日期 ≠ 发送日期（复制标题忘改日期），邮件信息栏自动加 ⚠️ 标注，+4 测试。协议版本 3.15（不变） |
| **v3.22.3** | 2026-08-07 | 知识库全面体检修复：索引死数据清除（chunker 全量重建误追加，chunk 10,290→5,939 / 覆盖率 100%）+ 图谱 LLM 边清零修复（恢复 591 条，增量不再丢）+ deep-eval CLI 参数补齐 + 回归测试 +4。协议版本 3.15（不变） |
| **v3.22.2** | 2026-08-04 | wikilink 注入残留清理：过时注释修正（chat_digest/extract_weekly_reports）+ wiki_root 死参数链删除（10 处）。协议版本 3.15（不变） |
| **v3.22.1** | 2026-08-03 | Wiki 发现噪音过滤（周报模板章节标题）+ 知识图谱全量重建边去重修复（LLM 边退化）。协议版本 3.15（不变） |
| **v3.22.0** | 2026-08-02 | 合并 0802-alpha：开源信息泄露治理全库脱敏 — 真实 open_id 配置化 / 团队名单与 dept_op_keyword 默认值清空（app.json 驱动，null 防御）/ 人名·邮箱·OKR·项目名·业务指标全库泛化 / 测试断言同步（61 文件）。协议版本 3.15（不变） |
| **v3.21.1** | 2026-08-02 | SOURCE 归档适配修复：双周报日期前缀 + 会议纪要/风格源递归查找 + feed 简报 `resolve_source_archive_path` + 死代码清理 + 5 Skill 路径更新（18 处）。协议版本 3.15（不变） |
| **v3.21.0** | 2026-08-02 | 批量 frontmatter 补全命令 `frontmatter-batch`（正则+LLM+wikilink+备份恢复，9 类目录字段映射）+ wikilink 注入收敛 + 周报按月归档 + 双周报 frontmatter 注入，+65 测试。协议版本 3.15 |
| **v3.20.1** | 2026-07-30 | deep_eval chunk 摘要路径配置化：`main_source` 硬编码改为 `default_source` 动态加载。协议版本 3.14（不变） |
| **v3.20.0** | 2026-07-30 | iris-feed 文档提取（Step 5）：飞书文档链接自动转换为本地 Markdown 并关联到简报，+37 测试。协议版本 3.14 |
| **v3.19.26** | 2026-07-29 | 检索与时效性四项优化：chunk 重叠 / Wiki source_fingerprint 指纹追踪 / 向量索引模型守卫 + --force-rebuild / 主动提醒引擎 reminders；修复 PDF 切块 0 chunk、hash 索引不更新、RRF 配置未生效。协议版本 3.13，+54 测试 |
| **v3.19.25** | 2026-07-28 | iris-feed 简报质量跃升：两阶段 LLM + 去截断 + Prompt 重写 + 结构化输出增强。合并 0728-beta：输入截断保护 + 死代码清理（7 文件/+725/-268） |
| **v3.19.24** | 2026-07-28 | 全量质量加固第二轮：P0 Dockerfile/CI/路径 + P1 feed +177/SecretStr/pre-commit + P2 logger.exception/导入统一/覆盖率合并（16 文件） |
| **v3.19.23** | 2026-07-28 | 全量代码质量加固：P0 修复 9 项 / ASR 存根删除 / 工具去重 / ConfigBundle 迁移 / feed 测试 +16 / deep_eval 并发化 / BM25 可配置 / 死代码清理（27 文件/+263/-225） |
| **v3.19.22** | 2026-07-27 | iris-feed OKR 语义匹配 + LLM deadline 实时超时控制 + ASR 独立熔断器（9 文件/199 行） |
| **v3.19.21** | 2026-07-27 | 信息汇聚管道 iris-feed：飞书聊天→话题检测→简报生成（11 文件/9 CLI/飞书 Bot 推送），协议版本 3.11→3.12 |
| **v3.19.20** | 2026-07-24 | ASR 反馈反向优化引擎：feedback.jsonl 驱动词典自动进化（僵尸规则/LLM发现提升/热词补充），build-asr-prompt 集成，+31 测试 |
| **v3.19.19** | 2026-07-23 | 测试覆盖全面优化：P1 _graph_engine 61→97% + asr/formatter 59→98% + navigation 67→70%，新增 3 测试文件 +90 用例（1,858→1,948），覆盖率 58.17→59% |
| **v3.19.18** | 2026-07-23 | 知识库质量全面加固 + 代码质量全面加固：wiki-pipeline 检测修复 / LLM 语义关系提取(986边) / 断链零出链双清零 / 向量索引构建 / 用量追踪 / DRY 消除 / pip-audit / lint 清零 |
| **v3.19.17** | 2026-07-22 | SOURCE 目录按月/年归档：9 目录 3 级策略（yearly/monthly/flat），自动生成年月子目录，740 文件搬迁 |
| **v3.19.16** | 2026-07-22 | 合并 0722-alpha：多 Agent 并发安全（FileLock 推广至 6 处 RMW + SQLite WAL + Agent 记忆隔离 + 进程注册表）+ 新增 iris-okr-check Skill |
| **v3.19.15** | 2026-07-22 | 多 Agent 并发安全三层防护体系（P0 FileLock 推广 + SQLite WAL / P1 缓存锁+Agent 隔离+TOCTOU / P2 进程注册表+JSONL 锁），3 轮审查 7 修复 |
| **v3.19.14** | 2026-07-22 | 记忆自动更新引擎：Phase 1 LLM 双通道提取 + Phase 2 会话模式挖掘 + Phase 3 全自治生命周期，29 新增测试 |
| **v3.19.13** | 2026-07-21 | ASR shutdown SIGINT 保护：清理流程统一信号屏蔽（finally 块），防止二次 Ctrl+C 中断 hotkey monitor 线程 join + executor 关闭 |
| **v3.19.12** | 2026-07-21 | ASR 引擎：LLM 思考模式关闭 + 路由路径 extra_body 修复 + 上下文 A/B 对比模式（`--context-ab`） |
| **v3.19.11** | 2026-07-21 | 五大方向优化：+76 测试 / LLM 统一网关(extra_body+use_cache) / MemoryCache 通用缓存 / God Class 拆解 / Wiki shim 废弃化（19 文件，+887/-64 行，1,829 测试） |
| **v3.19.10** | 2026-07-21 | ASR 引擎质量加固（P0~P3）：protected_terms 截断 / 热键校验 / 超时+参数化 / 预检查 / Aho-Corasick 优化 / worker 动态 / 重试（8 文件，+313/-114 行） |
| **v3.19.9** | 2026-07-20 | 双周报流水线质量加固（P0~P1）：Stage 1 全空兜底 / owner-map 注入 / 缓存校验 + Stage 3 子方向覆盖重构（全覆盖 / ≤3条/子方向 / ~50字）+ Stage 2/4b 增强 + brief 优先级排序 + 超时补跑（9 文件，+295 行） |
| **v3.19.8** | 2026-07-20 | 检测路径全面改进（P0~P2）：4 Bug 修复（死代码 / 正则贪婪截断 / 字符串表达式未赋值 / 缺失异常处理）+ 6 设计缺陷修复（代码正则补全 / 路径归一化双端一致 / 泛型类型修正 / 槽位效率去重 / RANGE_PATTERN search 化）+ 新建 test_text_detector 等 +79 测试（1,753，100 文件） |
| **v3.19.7** | 2026-07-19 | 全面质量加固（P0~P2 七项）：`_wiki.py` 静默异常补日志 / embedding 向量 LRU 缓存 / `corrector.py` 拆分（`_clipboard_io.py` + `_text_detector.py`）/ LLM `_CircuitBreaker` 熔断器 / +109 测试（1,674，99 文件） |
| **v3.19.6** | 2026-07-19 | ASR 校正引擎加固：max_mappings 990→2000 配置化 / 替换词典热加载 / 手动热词合并机制 |
| **v3.19.5** | 2026-07-19 | 全面质量加固：Stage 4 拆分 / `_TEAM_OKR_PATTERN` 配置化 / Stage 3 顺序后置校验 / ASR 动态推断示例 / 超时修复 / +30 测试（407 通过） |
| **v3.19.4** | 2026-07-19 | 双周报生成逻辑优化：方向标题精简化 / ≤4 条关键进展 / 来源按时间最新 / 严格 KR 顺序 + OP 文档选择修复 + 配置加载占位符误报修复 |
| \*\*v3.19.3\*\* | 2026-07-19 | 交互体验：build-asr-prompt 实时进度输出（ProgressTracker + Phase 2 逐批进度 + 耗时汇总） |
| \*\*v3.19.2\*\* | 2026-07-19 | ASR Phase 1 基础设施：反馈解析修复、模式枚举 API、daily-start ASR 审计 |\
| \*\*v3.19.1\*\* | 2026-07-19 | ASR 代码质量加固：JSONL 反馈格式统一、热词去重修正、死代码清理、剪贴板等待策略改进、常量复用 |\
| \*\*v3.19.0\*\* | 2026-07-19 | ASR 实时校正引擎：iris-asr-corrector 常驻守护进程，剪贴板监听 vocotype ASR 输出双重校正，一键部署，自动反馈闭环 |\
| \*\*v3.18.9\*\* | 2026-07-17 | 代码质量加固：内存系统并发安全（FileLock）、向量索引模型变更检测、`.env` 行尾注释剥离、Stage2 多模态 `max_tokens` 控制、lark-cli fallback、Wiki 证据阈值配置化 |
| **v3.18.8** | 2026-07-17 | 性能优化：PersonEnricher 飞书 API 频率限制修复（预先过滤已丰富页面 + 自适应批间延迟 + 批次大小调低） |
| **v3.18.7** | 2026-07-17 | 工程优化：CI/CD 基础设施（Makefile/CI/pre-commit/Dockerfile）+ 测试分层重组（unit/integration，1,467→1,513，覆盖率 60.42%）+ Wiki 模块重构（graph.py 751→215 行、ASR 子包物理隔离）；ruff F821/E741/F402 零错误 |
| **v3.18.6** | 2026-07-17 | 开源脱敏补充清理：内部标识符通用化 + 第三方服务引用脱敏 + CHANGELOG 反向泄露修复；测试 1,467 通过 |
| **v3.18.5** | 2026-07-17 | 新增 `iris-daily-start` Skill（每日启动维护一键触发）；Skill 8 个 |
| **v3.18.4** | 2026-07-17 | 代码质量优化：正确性修复（`_rrf_fuse` + 裸 except）+ 技术债清理 + graph.py 拆分（973→747 行）+ 测试 1,439→1,467（llm/service 97%，session 100%） |
| **v3.18.3** | 2026-07-17 | 全面测试补充：+216 用例（CLI 集成/纯函数/数据类），覆盖率 53%→60%；1,439 通过 |
| **v3.18.2** | 2026-07-16 | 文档版本同步 + 测试补充 + watcher 去抖修复；测试 1291 通过 54% |
| **v3.18.1** | 2026-07-16 | 全栈代码质量优化：P0 异常处理加固（15文件）+ P1 共享线程池/ConfigBundle Pydantic v2/deep_eval拆分 + P2 内存LRU缓存/BM25统计缓存 + P3 飞书退避优化/警告过滤；测试 1223→1291 | 
| **v3.18.0** | 2026-07-15 | 开源脱敏全面清理（方案B）：源码/测试/文档/模板敏感信息移除 + Git 历史全文重写（92 commits）+ CONTRIBUTING.md + SECURITY.md；测试 1223 通过 |
| **v3.17.1** | 2026-07-15 | SOURCE 新增「工作简报」栏目 + `--incremental` argparse 冲突修复 + 全维度 Wiki/索引/图谱评估与自动修复（断裂链接 -10、孤立节点 16→4、wikilink 边 +15、草稿发布）；测试 1223 通过 |
| **v3.17.0** | 2026-07-15 | 代码质量全面优化（P0-P3）：全局变量安全加固 + 异常精细化 + 模板加载统一 + ThreadPool 超时 + LRU 缓存驱逐 + 大文件拆分（service 1232→805行）+ graph LLM 缓存接入；测试 912→1223（+311），覆盖率阈值 50%→53% |
| **v3.16.0** | 2026-07-15 | 全栈优化 P0-P3：结构化日志 + async/await + 多工作空间 + 文件监听 + Prompt 外部化 + Wiki 引用校验 + LLM 缓存 + 增量 Chunk + NetworkX 图谱 + Config 迁移；测试 754→912（+158），覆盖率 49%→50.37%，CLI 48→51 |
| **v3.14.1** | 2026-07-15 | 代码质量全面优化：CLI handlers 拆分（1480→80+4 子模块）+ analysis 重构 + 测试 687→754 + pytest-cov 49% + except Exception 审查 + 文档完善 |
| **v3.14.0** | 2026-07-15 | 全面优化：测试 554→687 + 用量成本估算（usage-stats --cost + 价格表 + daily-start 概要/预算预警）+ 图谱查询命令 graph-query + VIDEO 多模态（ffmpeg 抽帧 + Whisper 转写）|
| **v3.13.0** | 2026-07-14 | LLM 用量统计（SQLite 记录调用/token，分模型 + 汇总，日/周/月/年聚合）+ usage-stats 命令；554 测试 |
| **v3.12.1** | 2026-07-14 | 图谱刷新单次 Wiki 扫描 + 持久 _out_edges 索引 + QA 图谱惰性缓存 + _parse_triples JSON 数组兼容 + pytest 工具配置；538 测试 |
| **v3.12.0** | 2026-07-14 | 知识图谱（节点+wikilink边+LLM关系提取）+ PDF/DOCX多模态 + 反向引用索引 + 代码审查14项修复；持续集成 532 测试 |
| **v3.11.17** | 2026-07-13 | .env→Keychain 密钥迁移 + secrets-list 修复 + 去重安全提醒修复；持续集成 461 测试 |
| **v3.11.16** | 2026-07-13 | P0 安全加固（Memory 原子写入 / LLMQueryPlanner 实现 / API Key 提醒）+ HTTP/Chunk 去重 + 测试 460 |
| **v3.11.15** | 2026-07-10 | Trello Python 3.13 SSL 兼容修复 + env 变量解析；持续集成 397 测试 |
| **v3.11.14** | 2026-07-10 | 新增 iris-process Skill（富媒体路由+三阶段流水线）；Stage 3 模板 bug 修复；iris-ask 职责边界清晰化，397 测试 |
| **v3.11.13** | 2026-07-10 | 开源脱敏清理：源码/测试/模板内部信息移除，`_SUB_AREA_KEYWORDS` 用户自定义，`.gitignore` 补全，397 测试 |
| **v3.11.12** | 2026-07-09 | extract-travel-invoice PDF 文字直接提取 + 转置表格输出；wiki-lint --fix 噪音链接正则修复（避免误删 frontmatter），397 测试 |
| **v3.11.11** | 2026-07-09 | extract-travel-invoice 代码审查 8 项修复，397 测试 |
| **v3.11.10** | 2026-07-09 | extract-weekly-reports 扫描漏人修复：folder list → 跨全文件夹 search + 白名单预筛 + 撤回/重复去重，命中人数大幅提升，384→397 测试 |
| **v3.11.9** | 2026-07-08 | 安全加固（开源准备）+ 工程质量 6 项 + 测试补全，315→384 测试 |
| **v3.11.8** | 2026-07-08 | build-asr-prompt 性能与质量优化：Phase 1/2 并发化 + Phase 3 校正策略强化，315 测试 |
| **v3.11.7** | 2026-07-07 | analysis/service.py 职责拆分：数据层/缓存层独立模块 + 38 测试用例，315 测试 |
| **v3.11.6** | 2026-07-07 | 全项目深度优化：19 项安全/bug/性能修复 + 8 测试文件 +58 用例，277 测试 |
| **v3.11.5** | 2026-07-07 | 代码审查优化：12 项修复（UTC 时区/preview 注入/OP 缓存/ModelManagerError），219 测试 |
| **v3.11.4** | 2026-07-07 | build-biweekly-report 流水线修复：多期去重 + 跨方向路由 + Stage 1 缓存 |
| **v3.11.3** | 2026-07-05 | build-biweekly-report 全面重构：文件级时间窗口 + 引用简化 |
| **v3.11.2** | 2026-07-03 | force_model 参数 + 纪要翻新 + 人物歧义处理，169 测试 |
| **v3.11.1** | 2026-07-03 | transcribe-meeting 修复：会议日期/时长/尾注，169 测试 |
| **v3.11.0** | 2026-07-03 | Claude Code Skill 体系：6 个项目级 Skill，169 测试 |
| **v3.10.2** | 2026-07-02 | feishu-doc-convert 改进：文件名使用飞书创建时间/作者，169 测试 |
| **v3.10.0** | 2026-07-01 | 全面代码优化 + 新模块引入（记忆 5 子模块 / 输出格式化 / 全局常量），169 测试 |
| **v3.9.0** | 2026-06-30 | 人物 Wiki 飞书通讯录丰富 + 人物发现规则增强，169 测试 |
| **v3.8.0** | 2026-06-29 | 复杂输入三阶段重构 + LLMService 统一入口 + 8 路由规则，161 测试 |
| **v3.7.0** | 2026-06-29 | iris2 迁移：Pydantic v2 配置校验 + Wiki 深度评估，161 测试 |
| **v3.6.0** | 2026-06-29 | 全面审查：6 Critical + 14 High 修复，5 项架构重构，138 测试 |
| v3.5.0 | 2026-06-29 | build-asr-prompt 三段 LLM Pipeline（热词 + 误识别 + Prompt） |
| v3.4.0 | 2026-06-27 | 代码审查修复（6 Critical Bug）+ BM25 重写 + 性能优化 |
| v3.3.0 | 2026-06 | 飞书 → 本地知识库提炼，步骤 3 完成 |
| v3.2.0 | 2026-06 | 步骤 2 完成，Wiki 体系上线 |
| v3.0.0 | 2026-05 | 项目初始化（从 Iris v2.7.1 重构） |
