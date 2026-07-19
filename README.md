# Iris 3.19.1

工作知识助手 — 个人知识库（Obsidian Wiki）与飞书知识库集成。

## 版本

**v3.19.1** — ASR 子系统代码质量加固：JSONL 反馈格式统一、热词去重逻辑修正、死代码清理、等待策略改进、常量复用。测试 1,565 通过，覆盖率 60.42%。

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
| 数据管道 | `scan-source`, `build-chunks`, `build-vector-index` | 文档扫描 / 切块 / 向量索引 |
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
| 系统 | `daily-start`, `check-config`, `status`, `diagnose` | 日常维护（含图谱增量刷新）/ 配置检查 |
| ASR 校正 | `asr-corrector`, `asr-audit`, `asr-report` | vocotype 实时语音转写纠错润色（[使用指南](docs/asr-corrector-usage.md)） |

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
| `adv_model` | qwen3.7-plus | 百炼 | 文本 + 图片 | → qwen3.7-plus-0526 → qwen3.6-plus → qwen3.6-flash → qwen3.6-27b → qwen3.5-plus（6 级） |

路由规则（8 条）：用户显式指定 → 多模态输入 → Prompt 生成 → 复杂分析 → Wiki 重建 → 问答 → 文本兜底。

## 技术栈

- Python 3.9+
- OpenAI 兼容 LLM API（DeepSeek / 百炼 Qwen 3.5/3.6/3.7 Plus 多模态）
- Pydantic v2（配置类型安全校验）
- lark-cli（飞书接口，步骤 3）
- macOS Keychain（可选密钥存储）
- PyMuPDF / python-docx（PDF/DOCX 处理）
- ffmpeg（视频抽帧/抽音轨，视频处理必需）+ openai-whisper（音轨转写，可选）
- 1,565 个单元测试（96 个测试文件），覆盖率 60.42%（仅统计 Iris 自身 LLM 调用）

## 开发环境

```bash
# 克隆并安装（含开发依赖）
git clone <repo-url> && cd iris3
pip install -e ".[dev]"

# 快速命令（Makefile）
make test            # 运行全部测试
make test-unit       # 纯逻辑单元测试（199 用例，0.5s）
make test-integration # 集成测试（1,314 用例）
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
├── src/iris/           # 20 模块（见下）
│   ├── app/cli/        # CLI 入口 + 46 命令处理器（4 子模块）
│   ├── analysis/       # 分析服务（报告/思维导图/双周报）
│   ├── complex_input/  # 多模态三阶段（图片/PDF/DOCX/VIDEO）
│   ├── config/         # 配置加载 + Pydantic 校验
│   ├── core/           # 类型/锁/存储/Agent 适配
│   ├── evaluation/     # Wiki 深度评估
│   ├── feishu/         # 飞书文档转换 + 聊天提炼
│   ├── ingest/         # 文档扫描 + 切块
│   ├── llm/            # Provider/路由/LLMService/用量统计
│   ├── memory/         # 记忆系统（5 子模块）
│   ├── output/         # 格式化 + DOCX 输出
│   ├── qa/             # 检索增强问答
│   ├── retrieval/      # BM25 + 向量 + RRF 混合检索
│   ├── trello/         # Trello 看板
│   ├── utils/          # 工具函数
│   └── wiki/           # Wiki 体系（最大模块，含图谱/ASR/反向引用）
│       └── asr/         #   ASR 提示词子系统（术语提取/热词/Prompt优化/版本管理）
├── scripts/            # CLI 入口 + 委托脚本
├── templates/          # Prompt / Wiki 模板
├── tests/              # 1,513 用例（93 文件）
│   ├── unit/           #   纯逻辑单元测试（199 用例）
│   └── integration/    #   集成测试（1,314 用例）
├── config/             # *.json gitignored，*.example 版本控制
├── .github/workflows/  # CI 流水线（Python 3.9-3.12 矩阵）
├── Makefile            # 常用开发命令
├── Dockerfile          # 开发容器
└── pyproject.toml      # 项目配置 + pytest/coverage/ruff 设置
```

## 版本历史

| 版本 | 日期 | 要点 |
|------|------|------|
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
| **v3.11.10** | 2026-07-09 | extract-weekly-reports 扫描漏人修复：folder list → 跨全文件夹 search + 白名单预筛 + 撤回/重复去重，命中 3→10 人，384→397 测试 |
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
