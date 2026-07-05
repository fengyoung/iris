# Iris 变更历史

> 产品版本（pyproject.toml）完整变更记录。协议版本和数据版本独立演进，仅当真正变化时才在此记录。

---

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
