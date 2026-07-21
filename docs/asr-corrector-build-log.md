# Iris ASR 实时校正引擎 — 构建与优化日志

> 日期：2026-07-18 ~ 2026-07-21
> 版本：Iris 3.18.9 → ... → 3.19.9 → 3.19.10 → 3.19.11

---

## 一、动机

`build-asr-prompt` 为 vocotype 离线生成 ASR 校正三件套（热词、替换词典、LLM Prompt），但存在三个核心问题：

1. **替换词典命中率 <1%**：987 条规则，实测 1000 行日志仅命中 1 次
2. **LLM 校正超时率 ~45%**：Prompt 过长（~2300 字），v4-flash 不稳定
3. **无反馈闭环**：不知道哪些规则有用、哪些是错的

## 二、架构决策

### 从离线编译器到实时校正服务

```
旧：Iris → 配置文件 → 手动部署 → vocotype(ASR+校正)
新：vocotype(ASR only) → 剪贴板 → Iris(实时校正) → 剪贴板 → 应用
```

关键决策：
- **热键 + 内容特征双重检测**：热键从 vocotype 配置动态读取，不硬编码。热键触发 + ASR 文本特征（中文为主、5-500 字、无代码特征）组合判定，误判率接近零
- **双次粘贴**：vocotype 先贴原始文本，Iris 用 Backspace 删掉再贴校正版
- **独立守护进程**：与 Claude Code 无关，通过 launchd 开机自启
- **伴生软件定位**：vocotype = 前端（音频+ASR），Iris = 后端（校正+反馈）

## 三、实现路径

### Phase 1：核心引擎（6 模块）

| 模块 | 文件 | 说明 |
|------|------|------|
| 数据类型 | `_types.py` | CoverageReport、DictQualityReport、AsrCorrection |
| 覆盖分析 | `coverage.py` | 热词覆盖率、噪音检测、高危映射检查，纯本地 |
| 反馈模型 | `feedback.py` | JSONL 读写、命中频率统计、应用反馈到词典 |
| 校正引擎 | `corrector.py` | Aho-Corasick 多模式匹配、剪贴板监听、热键检测、异步 LLM |
| Prompt 优化 | `prompt_optimizer.py` | Python 模板直接渲染，确定性输出 |
| CLI | `_cli_main.py` + handlers | `asr-corrector`、`asr-audit`、`asr-report` |

### Phase 2：质量加固

#### 1. Prompt V1 → V2 → V3

```
V1 (~2300字)：LLM 生成，输出不稳定，常产生元评论
V2 (~800字)：Python 模板直渲染，规则式 + top 30 映射内嵌
V3 (~930字)：编辑助手角色，纠错 + 润色 + 领域保护名单
```

#### 2. 替换词典质量

- **禁止项**：不生成含括号注释的误识别（如"张啸(误为张笑)"）
- **paraformer 特化**：告知 LLM 目标模型特征（Conformer + CTC），每术语最多 3 个映射
- **高危映射过滤**：单字高频中文（在、是、的、了、我…）不得作为误识别目标
  - 修复前：`在→ZAI` 导致"一起在深圳"变成"一起ZAI深圳"
  - 修复后：990 条映射，仅 1 条单字映射（`再→ZAI`，合法）

#### 3. LLM 推理问题

- **问题**：deepseek-v4-flash 默认开启 Chain-of-Thought，输出数千字推理过程
- **修复**：通过 `LLMRequest.extra_body` 传递 `thinking: { type: "disabled" }` 到 API
- **安全网**：输出 > 输入 ×3 长度 → 判定为 CoT 泄漏 → 丢弃，保留词典结果

#### 4. 反馈追踪

- 词典命中追踪（Aho-Corasick）
- LLM 修改追踪（词级 SequenceMatcher diff）
- 处理耗时追踪（`llm_time_ms` 字段）

## 四、使用方式

```bash
# 生成配置 + 一键部署
iris3 build-asr-prompt --deploy

# 质量检查
iris3 asr-audit --pretty

# 启动校正引擎（常驻）
iris3 asr-corrector --correct-mode full

# 手动纠错
iris3 asr-report --notes "实际想说的文本"
```

## 五、数据流

```
build-asr-prompt 生成         消费者              部署路径
──────────────────────────────────────────────────────────
hotwords.txt                   vocotype (ASR)       ~/Library/.../VocoType/
asr_replace_dict.json          iris-asr-corrector   data/
asr_prompt.md                  iris-asr-corrector   data/
asr_feedback.jsonl             build-asr-prompt     data/ (自动积累)
```

## 六、当前状态

| 指标 | 值 |
|------|-----|
| 替换词典 | 990 条（已过滤高危映射） |
| LLM Prompt | ~930 字（编辑助手 V3） |
| 覆盖率分析 | asr-audit 纯本地秒级运行 |
| 反馈数据 | 自动积累到 data/asr_feedback.jsonl |
| 单元测试 | 52 tests, 0.28s |
| LLM 推理 | 已关闭（thinking: disabled） |
| Prompt 热加载 | 支持（5 秒检查间隔） |

## 七、v3.19.1 代码质量加固（2026-07-19）

基于深度代码审查的 6 项修复/优化：

### 修复

| # | 问题 | 文件 | 改动 |
|---|------|------|------|
| 1 | JSONL 反馈格式不一致 | `feedback.py` | `save_correction` 补写 `llm_time_ms`；`load_corrections` 补读该字段 |
| 2 | 热词去重前截断 | `hotwords.py` | 移除 `[:max_hotwords*2]` 前置截断，改为遍历全量后截断 |
| 3 | V2 死代码残留 | `prompt_optimizer.py` | 移除 `build_optimize_prompt()` 和 `_clean_text()`，清理 8 个未使用的 import |

### 优化

| # | 问题 | 文件 | 改动 |
|---|------|------|------|
| 4 | 固定 delay 不可靠 | `corrector.py` | `_replace_text_in_place` 改为基线等待 0.15s + 剪贴板稳定性轮询（最长 1.0s） |
| 5 | 硬编码 prefix_map | `coverage.py` | 复用 `_constants.py` 的 `get_wiki_prefix()` |

## 八、v3.19.2 Phase 1 基础设施（2026-07-19）

为反馈驱动的反向优化闭环做准备，三项基础设施：

### 新增

| # | 内容 | 文件 | 说明 |
|---|------|------|------|
| 1 | `list_patterns()` | `corrector.py` | `_AhoCorasick` 新增公开方法，返回全部替换规则，供僵尸规则检测使用 |
| 2 | `extract_llm_discoveries()` | `feedback.py` | 仅提取 `[LLM]` 标记的修正条目，区分词典命中 vs LLM 发现 |
| 3 | `_daily_asr_audit()` | `_system.py` | daily-start 第 6 步，零 LLM 成本覆盖审计，无产物时静默跳过 |

### 修复

| # | 问题 | 文件 | 改动 |
|---|------|------|------|
| 4 | `[LLM]` 前缀污染 | `feedback.py` | `extract_mappings_from_corrections` 中剥离 `[LLM] ` 前缀 |
| 5 | pattern_count 未插值 | `corrector.py` | `run_forever` 启动日志修复，改用 `list_patterns()` |

## 九、Phase 1 迭代计划

> 目标：反馈驱动的反向优化闭环 — 校正日志自动优化替换词典和热词表。

### 当前状态

```
已完成 (v3.19.2):              待实现:
├── B. list_patterns() API     ├── D. 僵尸规则淘汰
├── C. [LLM] 前缀修复          ├── E. 热词补充逻辑
├── G. daily-start ASR 审计    ├── F. build-asr-prompt 反馈回注
└── 反馈数据采集链路              └── 场景自适应 profile
```

### 第 1 步：数据积累（前置条件，不可跳过）

```
依赖: iris-asr-corrector 常驻运行 1-2 周
产出: data/asr_feedback.jsonl 积累数百条校正记录
目的: 统计词典命中率、发现 LLM 高频修正模式
```

| 数据维度 | 用途 |
|----------|------|
| `corrections_applied` 命中频率 | 识别僵尸规则（0 命中 → 淘汰） |
| `[LLM] X→Y` 修正条目 | 发现词典未覆盖的错误模式 → 提升为词典规则 |
| `raw_text` 高频被误识词 | 补充进热词列表 |
| 手动 `asr-report` 记录 | 高优先级加入替换词典 |

### 第 2 步：分析函数（~60 行，数据就绪后实现）

纯函数，零 LLM 成本：

```python
# D. 僵尸规则淘汰 — feedback.py
def prune_zombie_rules(terms, hit_frequency, min_hits=0):
    """移除从未命中的 mis_asr 条目，释放词典配额"""

# E. 热词补充 — feedback.py
def extract_new_hotwords(corrections, current_hotwords):
    """从 LLM diff 中提取高频被纠词，补充为热词候选"""
```

### 第 3 步：build-asr-prompt 反馈回注（~30 行）

在 Phase 2 之前插入 Phase 1.5，读 feedback → 分析 → 注入：

```
handle_build_asr_prompt 增加 --from-feedback 参数:
  Phase 2 术语提取
    ↓
  Phase 1.5 反馈注入 ← 新增
    ├── load_corrections(feedback.jsonl)
    ├── compute_hit_frequency → 标记僵尸规则
    ├── prune_zombie_rules → 淘汰 0 命中条目
    ├── extract_llm_discoveries → LLM 发现 → 提升为词典规则
    └── extract_new_hotwords → 补充热词候选
    ↓
  Phase 2 误识别生成（使用优化后的 terms）
    ↓
  Phase 3 Prompt 渲染
```

### 第 4 步：场景自适应（~50 行）

根据 feedback 数据的时间/内容特征自动切换 profile：

```
分析 feedback.jsonl:
  ├── 工作时间 + 技术术语密集 → 自动切 tech profile
  ├── 高频人名出现 → 增强人物热词权重
  └── 非工作语言/闲聊 → 切 quick 模式（仅格式归一）
```

### 工作量

| 步骤 | 状态 | 代码量 | 依赖 |
|------|:--:|:-----:|------|
| 1. 数据积累 | ⏳ 等待中 | 0 | iris-asr-corrector 日常使用 |
| 2. 分析函数 | ❌ 待实现 | ~60 行 | 第 1 步（数据） |
| 3. 反馈回注 | ❌ 待实现 | ~30 行 | 第 2 步 |
| 4. 场景自适应 | ❌ 待实现 | ~50 行 | 第 1 步（数据） |

**核心瓶颈是第 1 步** — 代码总量 ~140 行，无真实反馈数据无法验证效果。

## 十、v3.19.3 交互体验改进（2026-07-19）

`build-asr-prompt` 三阶段执行慢（~1-2 分钟），但过程几乎无反馈。Phase 2（LLM 误识别生成）完全静默，Phase 3 标签有误导性。

### 改动

| # | 文件 | 操作 | 说明 |
|---|------|:---:|------|
| 1 | `_progress.py` | 新增 | 线程安全进度追踪器，零外部依赖 |
| 2 | `hotwords.py` | 修改 | Phase 1 集成 ProgressTracker，逐批显示 X/Y 完成 + 耗时 |
| 3 | `extractor.py` | 修改 | Phase 2 补齐逐批进度输出（此前完全静默） |
| 4 | `_wiki.py` | 修改 | 标签修正 + Phase 级耗时 + 总耗时汇总 |

### 效果

```
[asr] Phase 1/3: LLM 热词提取...
[asr] 热词提取：10 批并发，共 123 页
  [asr] 3/10 批完成 (12.8s): 第3批 72词 (候选85)
  ...
[asr]   ... Phase 1 完成 (35.2s): 487 热词

[asr] Phase 2/3: LLM 误识别生成（156 术语）...
[asr] 误识别生成：8 批并发，共 156 术语
  [asr] 2/8 批完成 (18.4s): 第2批 46/50 术语已映射
  ...
[asr]   ... Phase 2 完成 (59.0s): 317 映射

[asr] Phase 3/3: 校正提示词渲染...
[asr]   ... Phase 3 完成 (0.1s): 2431 字符

[asr] 总耗时 94.3s | 热词 487 | 术语 156
```

### 验证

- 111 个已有测试全部通过（52 unit + 59 integration）
- 8 批 / 10 批并发模拟验证线程安全，无行交错
