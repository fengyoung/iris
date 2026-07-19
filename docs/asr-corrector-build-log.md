# Iris ASR 实时校正引擎 — 构建与优化日志

> 日期：2026-07-18 ~ 2026-07-19
> 版本：Iris 3.18.9 → 3.19.0 → 3.19.1

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

## 八、待 Phase 1 完成

- 从 feedback.jsonl 提取高频误识别，反向优化替换词典
- 淘汰命中 0 次的僵尸规则
- 场景自适应 profile 切换
- daily-start 集成自动审计
