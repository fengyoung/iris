# Iris 3.30.1

工作知识助手 — 个人知识库（Obsidian Wiki）与飞书知识库集成。

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

> 逐版完整变更记录见 [CHANGELOG.md](CHANGELOG.md)；下表为按版本倒序的要点摘要。

| 版本 | 日期 | 要点 |
|------|------|------|
| **v3.30.1** | 2026-09-03 | 主线合并 0902-alpha → main：并入 v3.30.0 三阶段质量优化（F401/C901 门禁、`IrisError` 统一异常体系、mypy 基线、corrector/live 拆分），补齐 v3.29.2/v3.29.3 修复；源码零冲突，全量 3,274 通过。协议版本 3.21（不变） |
| **v3.30.0** | 2026-09-03 | 三阶段质量优化：开源冲刺（F401 门禁 + 395 导入清理 + 测试数据脱敏）→ 代码质量（C901≤20 重构 11 函数、corrector/live 拆分、print→logger）→ 长期改进（`IrisError` 异常体系 19 挂接、mypy 基线 193、专项单测 +257）；覆盖率 68%。协议版本 3.21（不变） |
| **v3.29.3** | 2026-09-03 | deep-eval 准确性校验修复：引用解析（列表符号/行号范围/反引号）+ `lookup_relevant` 证据召回兜底 + 评估模板双花括号填充根因修复。协议版本 3.21（不变） |
| **v3.29.2** | 2026-09-02 | 提醒引擎停滞误判修复：数字点号变体还原（30→3.0）+ frontmatter title 词段补充 + 调薪完结项目入 ignore；停滞信号 6→1。协议版本 3.21（不变） |
| **v3.29.1** | 2026-09-02 | Wiki 增量更新指纹预检补全：`update_all_pages` 复用 `is_wiki_stale`，源文档未变零 LLM 跳过（241 页全跳过 ~5s），根治 daily-start 卡死。协议版本 3.21（不变） |
| **v3.29.0** | 2026-09-01 | 飞书消息图片理解沉淀：`MessageImageAnalyzer` 下载→多模态→描述 + 按 image_key 跨管道缓存；feed/chat-digest 双管道接入；`image_understanding` 配置。协议版本 3.21（不变） |
| **v3.28.5** | 2026-09-01 | LLM 调用统一到 LLMService：消除 7 处 `get_provider()` 绕过响应缓存的残留路径。协议版本 3.21（不变） |
| **v3.28.4** | 2026-09-01 | 提醒引擎停滞判定三重兜底（指纹→SOURCE 同名→内容级）+ `project_stall_ignore` + Wiki 链接示例占位符根因清零。协议版本 3.21（不变） |
| **v3.28.3** | 2026-09-01 | 开源前信息安全复审：全库二次脱敏（姓名/指标/内部项目名泛化）+ git 邮箱 noreply + CI 最小权限。协议版本 3.21（不变） |
| **v3.28.2** | 2026-09-01 | batch-transcribe 批量会议纪要修复：补全 `run_batch`（音视频分流/单文件容错/埋点）+ handler 补传 `--to-source`。协议版本 3.21（不变） |
| **v3.28.1** | 2026-08-30 | 双线修复：LLM 思考文本污染（content 空抛错，Stage4b 回退组装稿）+ 深度审查批次 1 数据止损（6 P0 + 4 P1，回归 +26）。协议版本 3.21（不变） |
| **v3.28.0** | 2026-08-26 | 工程可靠性治理：SQLite 生命周期 / 稳定 inode 锁 / 统一原子写 / 向量索引 generation 发布 / 跨进程 LLM 缓存治理。协议版本 3.21，app 3.6 |
| **v3.27.2** | 2026-08-24 | LLM 配置修复（`find_model_by_name` Pydantic 兼容 + adv 视觉模型新默认）+ iris-feishu-import 批量用法修正。协议版本 3.20（不变） |
| **v3.27.1** | 2026-08-17 | 双周报写作风格固化：Stage 3 总结段逐项目「目标→思考→决策→下一步」+ 关键进展项目级聚合。协议版本 3.20（不变） |
| **v3.27.0** | 2026-08-16 | 任务面板 `iris task-panel`：Web 只读 + 常驻守护（launchd）+ TaskReporter 埋点 + 探测兜底 + 首批 5 命令接入。协议版本 3.20 |
| **v3.26.2** | 2026-08-12 | meeting-live-assistant 面板双主题视觉方案：`_theme.py` 双套 ANSI 配色 + 语义色贯穿 + `assistant.panel_theme` 配置。协议版本 3.19（不变） |
| **v3.26.1** | 2026-08-12 | meeting-live-assistant 深度审查后全量优化：29 项四阶段（P0 可用性 / P1 体验 / P2 能力 / P3 工程）+ 3 项评估修复。协议版本 3.19（不变） |
| **v3.26.0** | 2026-08-12 | meeting-live-assistant 升级「实时 AI 会议参谋」：四层 12 项能力 + 说话人区分 + VAD 修复 + 降级链 deadline 治理。协议版本 3.19（不变） |
| **v3.24.2** | 2026-08-11 | asr-corrector 写回修正：full 模式单次输出（消除两次写回闪烁）+ 恢复逐字符 Delete 删除。协议版本 3.18（不变） |
| **v3.24.0** | 2026-08-11 | assistant × asr-corrector 全面优化：写回快照校验 / 反馈反向优化管线时序 / 替换词典交叉冲突防护 / 预取原子化 / 热键 Event 化 / LLM deadline。协议版本 3.18 |
| **v3.23.3** | 2026-08-10 | meeting-live-assistant 全量优化：双段流水线并行（关键路径 25s→15s）/ 短段门控 / 退出路径加固 / AI 会议总结 / 检索 deadline 根治。协议版本 3.17 |
| **v3.23.2** | 2026-08-10 | wiki-update 备份文件全链路过滤：`*.bak.1.md` 被 5 处计入统一按 `.bak.` 过滤。协议版本 3.16（不变） |
| **v3.23.1** | 2026-08-10 | 遗留修复 + 使用指南：asr-corrector Ctrl+C（Python 3.13 SIGINT）+ verify_hotkey_inject 入版本控制 + 会议助理使用指南。协议版本 3.16（不变） |
| **v3.23.0** | 2026-08-10 | 实时会议助理 `meeting-live-assistant`：按住热键逐段转写→ASR 校正→检索→LLM 分析→面板提示 + 过程文档；积压丢弃 + 运行时互斥。协议版本 3.16 |
| **v3.22.5** | 2026-08-10 | ASR 校正引擎热键门控修复：CGEventTap 失效降级（内容特征判定兜底）+ 监听窗口挂钩热键按住时长（1 分钟长语音不再超时跳过）。协议版本 3.15（不变） |
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
