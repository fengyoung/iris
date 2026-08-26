# Iris ASR 实时校正引擎 — 完整设计方案

> 生成日期：2026-07-18 · 最后更新：2026-08-26
> 关联项目：Iris 3.28.0 / VocoType (AltRight 热键 + vocotype ASR)
> 状态：v3.24.3 全面优化（公共 push_context API + 结构化日志）。v3.24.2 真机修正 — full 模式一次写回 + 全场景逐字符 Delete（取消 Cmd+A）。v3.24.0 写回机制重构+LLM 相似度门槛+长度上限配置化。v3.23.3 双段流水线/短段门控。v3.22.5 热键门控修复。v3.19.12 上下文 A/B 对比。v3.19.10 ASR 引擎全面质量加固已完成

---

## 一、背景与问题诊断

### 1.1 现状

当前 Iris 通过 `build-asr-prompt` 为 vocotype 离线生成三件套：

| 制品 | 格式 | 配额 | 用途 |
|------|------|------|------|
| 热词列表 | `hotwords.txt`（纯文本每行一词） | ≤500 | ASR 引擎 contextual bias |
| 替换词典 | `postprocess.json`（`{误识别: 正确词}`） | ≤1000 | 后处理文本替换 |
| 策略 Prompt | `ai_settings.json` 中的模板 | 无限制 | LLM 二次校正 |

生成后需手动拷贝到 `~/Library/Application Support/VocoType/` 目录，重启 vocotype 生效。

### 1.2 核心问题

| 问题 | 数据 | 根因 |
|------|------|------|
| 替换词典命中率极低 | 1000 行日志仅命中 1 次（0.1%） | LLM 生成的是"通用 ASR 错误"而非 paraformer 实际会犯的错 |
| LLM 校正 Prompt 过长 | ~2300 字，超时率 ~45% | 6类错误模式 + 硬编码人名名单 + 虚构示例 |
| Prompt 策略失效 | "字典优先"未触及时 LLM 不知道词典内容 | LLM 无法访问替换词典 |
| 热词质量不可知 | 401/500 已用，混杂噪音词 | 无检查环节 |
| 部署流程繁琐 | 生成 → 手动拷贝 → 手动改名 → 重启 | 无自动化 |
| 无反馈闭环 | 无法知道实际 ASR 错误模式 | 无数据采集渠道 |

### 1.3 已验证的假设

2026-07-18 实测验证：

```
用户说话 "我写到剪切板里头"
  → vocotype ASR 输出 "我写到检测板里头"（剪切→检测，q→c 声母混淆）
  → 写入剪贴板
  → Python 脚本读取成功
  → 替换词典命中 "检测板→剪切板" 校正成功
```

**确认：剪贴板链路可行，ASR 确实会产生可被词典修正的错误。**

---

## 二、架构变更

### 2.1 从离线编译器到实时校正服务

```
旧架构：
  Iris (build-asr-prompt) → 配置文件 → 手动部署 → vocotype (ASR + 热词 + 词典 + LLM)

新架构：
  vocotype (ASR + 热词 only) → 剪贴板 → Iris (词典 + LLM) → 剪贴板 → 应用
```

### 2.2 职责分配

```
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│         VocoType（前端）           │  │     iris-asr-corrector（后端）    │
│                                  │  │                                  │
│ · 音频采集                       │  │ · 剪贴板监听 + 热键检测           │
│ · ASR 引擎                       │  │ · 替换词典校正（Aho-Corasick）     │
│ · 热词 bias（hotwords.txt）      │  │ · LLM 语境消歧（异步）            │
│                                  │  │ · 反馈数据自动采集               │
│ · 桌面 GUI                       │  │ · CLI 守护进程                   │
│ · macOS/Windows                  │  │ · Python 3.11+（当前实现 macOS）  │
│ · AI 优化: 关闭                  │  │ · 不受 Claude Code 会话影响       │
│ · 替换词典: 清空                 │  │ · 通过 launchd 注册开机自启       │
└──────────────────────────────────┘  └──────────────────────────────────┘
         │                                              │
         │               剪贴板 (NSPasteboard)            │
         └──────────────────┬───────────────────────────┘
                            │
                    ┌───────▼────────┐
                    │  光标处应用     │
                    │  (IDE/文档/聊天)│
                    └────────────────┘
```

### 2.3 新旧对比

| 维度 | 旧（配置编译器） | 新（实时校正服务） |
|------|:---:|:---:|
| 词典/Prompt 更新 | 需重启 vocotype | 即时生效 |
| 场景自适应 | 全局一份配置 | 多 profile，按需切换 |
| 反馈闭环 | 无数据来源 | 每次校正自动记录 |
| LLM 超时影响 | 阻塞 vocotype 输出 | 异步，不影响基础输出 |
| 可控性 | vocotype 黑盒 | Iris 侧完全可观测 |
| 部署 | 3 步手动 | 一条命令 |

---

## 三、数据流

### 3.1 build-asr-prompt 产出的三个制品

```
build-asr-prompt 生成
          │
          ├── hotwords.txt ────────────→ vocotype 配置目录（ASR 引擎用）
          │                              ~/Library/Application Support/VocoType/hotwords.txt
          │
          ├── replace_dict.json ────────→ Iris data/（校正引擎用）
          │                              data/asr_replace_dict.json
          │
          └── prompt_v2.md ─────────────→ Iris data/（校正引擎用）
                                         data/asr_prompt_v2.md
```

### 3.2 三个路径，三种角色

```
路径                                   消费者              更新方式
──────────────────────────────────────────────────────────────────────
~/Library/.../VocoType/hotwords.txt    vocotype (ASR)       build-asr-prompt --deploy
                                                               
data/asr_replace_dict.json             iris-asr-corrector   build-asr-prompt --deploy
data/asr_prompt_v2.md                  iris-asr-corrector   build-asr-prompt --deploy
data/asr_feedback.jsonl                build-asr-prompt     iris-asr-corrector 实时追加
                                       （Phase 1 反向优化）
                                                               
output/asr-modify/                     人工审查 + 归档       build-asr-prompt
  asr-hotwords-{date}.txt
  asr-replace-dict-{date}.json
  asr-prompt-v{ver}-{date}.md
```

- `output/asr-modify/` — 带日期版本的归档副本，便于对比和回滚
- `data/` — 固定路径，`iris-asr-corrector` 启动时直接读取，无需指定参数
- vocotype 配置目录 — 仅 `hotwords.txt` 部署到这里

### 3.3 build-asr-prompt --deploy 完整流程

```
1. 生成三件套（同现有逻辑）
2. 备份旧配置到 output/vocotype-backup-{date}/
3. 写入 vocotype 配置目录：
   hotwords.txt     → 覆盖（热词在 ASR 层面生效）
4. 写入 Iris data/：
   replace_dict     → data/asr_replace_dict.json
   prompt_v2        → data/asr_prompt_v2.md
5. 写入 vocotype ai_settings.json：
   global.enabled   → false（关闭 vocotype LLM 优化）
   replace_map      → {}（清空 vocotype 替换词典）
6. 输出部署摘要
```

---

## 四、模块设计

### Phase 0 总览

```
Phase 0
├── 模块 1：iris-asr-corrector    实时校正引擎（常驻守护进程）
├── 模块 2：策略 Prompt V2         替换词典增强 + LLM Prompt 精简
├── 模块 3：asr-audit             覆盖分析 + 格式检查（纯本地）
├── 模块 4：asr-feedback          反馈数据模型 + JSONL 自动采集
└── 模块 5：build-asr-prompt --deploy  一键部署
```

---

### 模块 1：iris-asr-corrector（实时校正引擎）

#### 1.1 CLI 命令

```
iris3 asr-corrector [--mode fast|full] [--profile <name>]
```

| 参数 | 说明 |
|------|------|
| `--mode fast` | 仅替换词典，不调 LLM，<1ms |
| `--mode full`（默认） | 替换词典 + LLM 异步校正 |
| `--profile <name>` | 校正策略配置（work/tech/meeting） |

启动后常驻后台运行。前台模式调试用，生产通过 `launchd` 注册。

#### 1.2 完整运行时链路

```
用户按下 vocotype 热键（按住说话）→ 说话 → 松手（触发转写）
  │
  ├─ Iris 监听到热键按下（上升沿），记录 _hotkey_held = True
  │    （从 ~/Library/Application Support/VocoType/ui_settings.json
  │     读取 recording_hotkey，支持左右修饰键 + F1-F12 功能键）
  │
  ├─ Iris 监听到热键释放（下降沿），记录 _hotkey_released_at
  │   → 开启 3 秒"释放后监听窗口"（覆盖 vocotype 转写延迟）
  │
  ├─ vocotype: ASR → 写入剪贴板 → Cmd+V（原始文本闪现）
  │
  ├─ Iris: 监听窗口内检测到剪贴板变化
  │    → 内容通过 ASR 文本特征检测 ✓
  │    → 剪贴板格式检查（纯文本才放行，拒绝 HTML/RTF）✓
  │    → Step 1: 替换词典（Aho-Corasick，<1ms）
  │    → 写入剪贴板 → Cmd+V（覆盖原始文本）
  │
  ├─ Iris: Step 2: LLM 校正（异步，1-3s）
  │    → 成功 → 写入剪贴板 → Cmd+V（精修）
  │    → 失败/超时 → 保持 Step 1 结果
  │
  └─ Iris: 记录校正日志 → feedback.jsonl
```

#### 1.3 vocotype 文本来源判定：热键（push-to-talk）+ 内容特征 + 剪贴板格式三重检测

```
vocotype 录音热键按下？（从 ui_settings.json 动态读取，支持左右修饰键）
  │
  ├─ 否 → 忽略（普通剪贴板操作）
  │
  └─ 是 → 追踪 push-to-talk 状态：
           按住期间保持窗口开放，释放后 3s 内等待转写结果
           │
           └─ 剪贴板变化且内容匹配？
                 │
                 ├─ 是 → 三重判定全部满足才触发：
                 │         ☐ 中文为主（>30% 汉字）
                 │         ☐ 5-500 字符
                 │         ☐ 无代码特征（无 { ; def import class http:// 等）
                 │         ☐ 无 URL 模式
                 │         ☐ 无 markdown 格式标记
                 │         └─ 全部满足 → 触发校正
                 │
                 └─ 否 → 窗口超时关闭（释放后 3s 内无剪贴板写入）
```

#### 1.4 粘贴策略：Backspace 删除 + 重新粘贴

vocotype 的粘贴不走标准 Cmd+V，Cmd+Z 无法可靠撤销。改为等 vocotype 粘贴落地后，按原始文本长度 N 次 Backspace 删除，再粘贴校正版。

```
T+0ms      vocotype: 写入原始 ASR 文本
T+200ms    Iris: 按 Backspace × N 删除 → Cmd+V 粘贴校正版
T+1-3s     Iris: LLM 校正完成后再次覆盖（如启用 full 模式）
```

短文本几乎无感，长文本可能有短暂闪烁。

#### 1.5 校正引擎核心类

```python
class AsrCorrector:
    """实时 ASR 校正引擎。"""

    def __init__(self, replace_dict: Dict[str, str],
                 llm_prompt: str, provider=None):
        self._automaton = self._build_automaton(replace_dict)
        self._prompt = llm_prompt
        self._provider = provider

    def correct_fast(self, text: str) -> Tuple[str, List[str]]:
        """
        Aho-Corasick 多模式匹配，一次扫描完成全部替换。
        最长匹配优先——避免"数据湖"和"数据湖工程"冲突时前者先覆盖后者。
        返回：(校正后文本, [应用的规则列表])
        """
        ...

    async def correct_full(self, text: str) -> Tuple[str, List[str]]:
        """
        替换词典 + LLM 异步校正。
        Step 1 结果立即返回，Step 2 异步执行。
        LLM 超时/失败时保持 Step 1 结果。
        """
        ...
```

#### 1.6 多场景配置

```json
// config/asr_profiles.json
{
  "default": {
    "replace_dict": "data/asr_replace_dict.json",
    "llm_prompt": "data/asr_prompt_v2.md",
    "mode": "full",
    "llm": {
      "provider": "deepseek",
      "model": "deepseek-v4-flash",
      "timeout_ms": 4000,
      "max_tokens": 2048,
      "temperature": 0.1
    }
  },
  "meeting": {
    "replace_dict": "data/asr_replace_dict.json",
    "llm_prompt": "data/asr_prompt_meeting.md",
    "mode": "full",
    "llm": {
      "provider": "deepseek",
      "model": "deepseek-v4-flash",
      "timeout_ms": 5000,
      "max_tokens": 2048,
      "temperature": 0.1
    }
  },
  "quick": {
    "replace_dict": "data/asr_replace_dict.json",
    "llm_prompt": null,
    "mode": "fast",
    "llm": null
  }
}
```

#### 1.7 配置分层

```
┌─ .env（Iris 全局，复用）─────────────────┐
│ DEEPSEEK_API_KEY=sk-xxx                  │
│ DEEPSEEK_BASE_URL=https://api.xxx.com    │
│  → iris-asr-corrector 启动时自动加载     │
└──────────────────────────────────────────┘
              │
              ▼
┌─ asr_profiles.json（校正引擎独有）────────┐
│ model / timeout_ms / max_tokens / temp   │
│  → 覆盖使用参数，不存密钥                │
│  → 校正场景要求：低 timeout（3-5s）       │
│    低 temperature（0.1，确定性强）       │
│    小 max_tokens（1024-2048，输入短）     │
│    不重试（超时直接降级）                │
└──────────────────────────────────────────┘
```

**密钥仍走 `.env`，`asr_profiles.json` 只规定 LLM 使用参数。** 这样 Iris 其他模块不受影响，校正引擎可以独立调校各参数。

---

### 模块 2：策略 Prompt V2

#### 2.1 方向 A：替换词典质量提升（生成侧）

改动 `src/iris/wiki/asr/extractor.py` 的 `_build_misreadings_prompt()`：

| 维度 | 当前 | 改进后 |
|------|------|--------|
| 目标模型 | 未指定 | 明确告知 paraformer-large-zh-cn-contextual（Conformer + CTC 解码） |
| 误导识别数量 | 每术语 3-5 个 | 每术语最多 3 个，聚焦音近+常见字混淆，不编造冷门变体 |
| 禁止项 | 无 | 不生成含括号注释的误识别（如"王军(误为王君)"）；不生成原词大小写变体（qwen→QWEN 是格式归一，不是 ASR 误识） |
| 冲突检测 | 无 | 两个不同正确词不能有相同误识别，冲突时标记并跳过 |

#### 2.2 方向 B：LLM Prompt 精简（生成侧）

重写 `src/iris/wiki/asr/prompt_optimizer.py` 的 `build_optimize_prompt()`。

**核心转变**：从"指南式"（告诉 LLM 怎么纠错）改为"规则式"（直接给规则 + 高频映射 + 实例）。

```
┌─ 旧 Prompt (~2300字) ─────────────────────
│ 角色设定
│ 6类错误模式（每类：判断依据 + 领域实例）
│ 硬编码人名名单名单
│ 润色规则 5条
│ 输出格式
│ → LLM 认知过载，超时率 45%
└──────────────────────────────────────────

┌─ 新 Prompt (~800字) ─────────────────────
│ 你是 ASR 校正助手。仅输出校正后文本，不解释。
│
│ ## 内置映射（直接替换，无需判断）
│ "李雷"→"李蕾"、"数据仓库"→"数据湖"、
│ "智能画检测"→"智能化检测"、"检测板"→"剪切板"...
│ （精选 top 30 高频易错映射，从替换词典中按置信度选取）
│
│ ## 通用规则
│ 1. 技术缩写全大写（dnn→DNN, ocr→OCR）
│ 2. 中英文间加空格（Qwen大模型→Qwen 大模型）
│ 3. 合并 ASR 误拆的短句碎片
│ 4. 去除口语填充词（嗯、啊、那个）
│ 5. 中文全角标点
│
│ ## 未覆盖的词
│ 如遇词典未覆盖但明显错误的人名/术语，
│ 根据上下文推断，保持音近原则。
│
│ 仅输出校正后文本。
└──────────────────────────────────────────
```

**关键差异**：
- 旧：LLM 不知道词典内容 →"词典优先"空转
- 新：top 30 映射直接内嵌 → LLM 真的能执行
- 旧：63 人硬编码 → ~200 tokens
- 新：引用式 → ~20 tokens，人名走词典
- 长度：2300 → ~800 字，预计超时率从 45% 降至 <15%

---

### 模块 3：asr-audit（覆盖分析 + 格式检查）

#### 3.1 数据来源

分析对象是 `build-asr-prompt` 已产出的文件（`output/asr-modify/`）。纯本地计算，无 LLM 调用。

#### 3.2 检查项

```python
@dataclass
class CoverageReport:
    """热词覆盖分析"""
    hotword_count: int          # 当前热词数
    max_slots: int              # 配额（500）
    persons_covered: int        # 已覆盖人物
    persons_total: int          # Wiki 人物总数
    persons_missing: List[str]  # 未覆盖人物名
    projects_covered: int
    projects_total: int
    projects_missing: List[str]
    concepts_covered: int
    concepts_total: int
    concepts_missing: List[str]
    noise_words: List[str]      # 噪音词
    long_words: List[str]       # 超长词（>12字，ASR 热词 bias 无效）
    slot_efficiency: float      # 槽位利用率

@dataclass
class DictQualityReport:
    """替换词典质量检查"""
    total_rules: int
    format_errors: List[str]           # 含括号注释等格式异常
    conflicting_pairs: List[Tuple]     # 两条映射指向同一正确词
    category_distribution: Dict[str, int]
```

#### 3.3 CLI

```
iris3 asr-audit [--coverage] [--output-file PATH] [--pretty]
```

#### 3.4 build-asr-prompt 集成

生成完成后自动输出 2 行摘要。需要详细报告时手动运行 `asr-audit`。

---

### 模块 4：asr-feedback（反馈数据模型 + Phase 1 反向优化）

#### 4.1 数据结构

```python
@dataclass
class AsrCorrection:
    """单次校正记录"""
    timestamp: str              # ISO 8601
    raw_text: str               # vocotype 原始 ASR 输出
    fast_corrected: str         # Step 1 词典校正结果
    full_corrected: str         # Step 2 LLM 校正结果
    mode: str                   # "fast" | "full"
    corrections_applied: List[str]  # 命中了哪些替换规则
```

#### 4.2 存储格式

```jsonl
{"timestamp":"2026-07-18T17:30:00","raw_text":"我写到检测板里头","fast_corrected":"我写到剪切板里头","full_corrected":"我写到剪切板里头","mode":"full","corrections_applied":["检测板→剪切板"]}
```

#### 4.3 自动采集

- `iris-asr-corrector` 每次校正自动追加到 `data/asr_feedback.jsonl`
- 数据完全本地存储，不上传

#### 4.4 手动纠错命令

```bash
# 用户发现校正结果有误时，终端输入：
iris3 asr-report "实际想说的文本"
# → 读取当前剪贴板内容作为 ASR 原文
# → 追加一条手动纠错到 feedback.jsonl
```

#### 4.5 Phase 1 反向优化闭环

反馈数据积累到一定量后，`build-asr-prompt` 下次运行时自动读取 `feedback.jsonl` 进行分析：

```
                    ┌─────────────────────────────────────┐
                    │         iris-asr-corrector           │
                    │    每次校正写入 feedback.jsonl        │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │         feedback.jsonl               │
                    │  · 替换词典命中记录（哪些规则有用）    │
                    │  · LLM 发现的校正（词典漏掉的错误）   │
                    │  · 校正前后 diff                     │
                    │  · 手动纠错记录                      │
                    └──────────────┬──────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
    ┌─────────────────┐  ┌───────────────┐  ┌─────────────────┐
    │ 淘汰无效规则      │  │ LLM发现→词典   │  │ 新误识别→热词    │
    │ 命中0次的移出配额 │  │ 高频LLM校正    │  │ 高频被误识的词   │
    │                  │  │ 提升为词典规则 │  │ 补充进热词列表   │
    └─────────────────┘  └───────────────┘  └─────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │      build-asr-prompt（下次运行）      │
                    │  · 读取 feedback.jsonl               │
                    │  · 淘汰僵尸规则 + 提升高频用例         │
                    │  · 补充新热词                        │
                    │  · 生成更精准的词典和 Prompt          │
                    └─────────────────────────────────────┘
```

**feedback.jsonl 分析维度：**

| 信息 | 分析结果 | 行动 |
|------|---------|------|
| `corrections_applied` 命中频率 | 哪些替换规则实际生效 | 淘汰命中 0 次的规则，释放配额 |
| `full_corrected ≠ fast_corrected` | LLM 修正了词典未覆盖的错误 | 提取 LLM 修正对 → 补充进替换词典 |
| `raw_text` 中高频被 LLM 纠正的词 | 这些词 ASR 经常误识 | 补充进热词列表（ASR 层面预防） |
| 手动 `asr-report` 记录 | 用户发现的漏网之鱼 | 高优先级加入替换词典 |

**闭环完整性：自动积累 ~80% 数据 + 手动补充 ~20%。**

---

### 模块 5：build-asr-prompt --deploy（一键部署）

#### 5.1 CLI

```
iris3 build-asr-prompt --deploy
```

#### 5.2 行为

```
1. 生成三件套（同现有流程）
2. 备份旧配置到 output/vocotype-backup-{date}/
3. 写入 vocotype 配置目录：
   hotwords.txt     → ~/Library/.../VocoType/hotwords.txt（覆盖）
   ai_settings.json → global.enabled = false（关闭 vocotype LLM 优化）
                     replace_map = {}（清空 vocotype 替换词典，Iris 接管）
4. 写入 Iris data/：
   replace_dict     → data/asr_replace_dict.json（校正引擎用）
   prompt_v2        → data/asr_prompt_v2.md（校正引擎用）
5. 输出部署摘要
```

不指定 `--deploy` 时行为保持不变（输出到 `output/asr-modify/`）。

---

## 五、关键设计决策

| 决策 | 结论 | 理由 |
|------|------|------|
| 文本来源判定 | 热键(push-to-talk) + 内容特征 + 剪贴板格式三重检测 | 左右修饰键均检测、非修饰键联动校验、富文本自动过滤、书面中文预检查 |
| 粘贴策略 | 接受双次粘贴 | vocotype 不支持"只写剪贴板不粘贴"；先出字后修正体感自然 |
| 进程模型 | 独立守护进程 | 与 Claude Code 无关，通过 launchd 注册开机自启 |
| 与 vocotype 关系 | 伴生软件 | vocotype=前端（音频+ASR），Iris=后端（校正+反馈） |
| vocotype MCP | 不使用 | MCP 仅提供音视频转录接口，无配置管理能力 |
| 热词列表 | 保留在 vocotype | ASR 层面的 contextual bias 是最有效的，不动 |
| 开源：vocotype 依赖 | 可选外部依赖 | 缺失仅影响 iris-asr-corrector，Iris 整体不受影响 |
| 开源：敏感信息 | .gitignore + .example + 零硬编码 | data/ 全量 gitignore；asr_profiles.json.example 脱敏进入版本控制；vocotype 路径从环境变量读取 |
| 开源：降级策略 | 其他模块零影响 | iris-asr-corrector 检测 vocotype 不存在时返回错误码，不影响系统 |

---

## 六、实施顺序

| # | 文件 / 模块 | 内容 |
|---|------------|------|
| 1 | `src/iris/wiki/asr/_types.py` | 新增 `CoverageReport`、`DictQualityReport`、`AsrCorrection` 数据类 |
| 2 | `src/iris/wiki/asr/coverage.py` | 覆盖分析：热词覆盖率、噪音检测、格式错误，纯本地 |
| 3 | `src/iris/wiki/asr/feedback.py` | 反馈模型 + JSONL 读写 |
| 4 | `src/iris/wiki/asr/extractor.py` | 改进 `_build_misreadings_prompt()`：paraformer 特化 + 禁止项 + 去冲突 |
| 5 | `src/iris/wiki/asr/prompt_optimizer.py` | 重写 `build_optimize_prompt()`：规则式 + top N 内嵌 + 800字目标 |
| 6 | `src/iris/wiki/asr/corrector.py` | **实时校正引擎**：剪贴板监听 + 热键检测 + Aho-Corasick + 异步 LLM |
| 7 | `src/iris/app/_cli_main.py` | 注册 `asr-corrector`、`asr-audit` 命令；`build-asr-prompt` 增加 `--deploy` |
| 8 | `src/iris/app/cli/_handlers/_wiki.py` | 实现 `handle_asr_corrector()`、`handle_asr_audit()`、`--deploy` 逻辑 |
| 9 | `src/iris/wiki/asr/__init__.py` | 更新 `__all__` 导出 |
| 10 | `src/iris/app/cli/handlers.py` | 重导出新 handler |
| 11 | `config/asr_profiles.json` | 默认校正策略配置 |
| 12 | `tests/unit/test_asr_coverage.py` | 覆盖分析单元测试 |
| 13 | `tests/unit/test_asr_corrector.py` | 校正引擎单元测试 |
| 14 | `tests/unit/test_asr_feedback.py` | 反馈模型单元测试 |

---

## 七、验证方案

### 7.1 单元测试

```bash
python -m pytest tests/unit/test_asr_coverage.py -v
python -m pytest tests/unit/test_asr_corrector.py -v
python -m pytest tests/unit/test_asr_feedback.py -v
```

### 7.2 集成验证

```bash
# 1. 覆盖率检查
iris3 asr-audit --pretty

# 2. 一键部署（生成 + 写入 vocotype 配置 + Iris data/）
iris3 build-asr-prompt --deploy

# 3. 启动校正服务（前台运行，观察输出）
iris3 asr-corrector --mode full

# 4. 正常使用 vocotype 语音输入
#    - 观察原始 ASR → 词典修正 → LLM 精修的完整链路
#    - 确认双次粘贴体感可接受
# 5. 检查反馈数据
cat data/asr_feedback.jsonl
```

### 7.3 效果预期

| 指标 | 当前 | 目标 |
|------|------|------|
| 替换词典格式错误 | 有（含括号注释） | 零 |
| LLM 校正 Prompt 长度 | ~2300 字 | ~800 字 |
| LLM 超时率 | ~45% | <15% |
| 部署步骤 | 3 步手动 | 1 条命令 |
| 反馈数据 | 无 | 自动积累 JSONL |
| 配置更新生效 | 重启 vocotype | 即时 |

---

## 八、Phase 1 展望

Phase 0 建立基础设施后，Phase 1 的核心目标：

### 8.1 数据驱动优化

Phase 0 积累的 `feedback.jsonl` 达到一定量级（预计日常使用 1-2 周）后，`build-asr-prompt` 自动开启反向优化：

- **淘汰僵尸规则**：`corrections_applied` 命中 0 次的替换映射，从配额中移出
- **提升 LLM 发现**：`full_corrected ≠ fast_corrected` 时，提取 LLM 修正的映射对，补充进替换词典
- **补充热词**：ASR 原文中高频被误识且被 LLM 纠正的词，加入热词列表

### 8.2 场景自适应

根据 feedback 数据的时间分布和内容特征，自动区分场景：

- 工作时间 + 技术术语 → 自动切换 `tech` profile
- 高频人名出现 → 自动增强人物热词权重
- 非工作语言/闲聊 → 切换到 `quick` 模式（仅格式归一，不深度校正）

### 8.3 vocotype 集成增强

- 向 vocotype 提 Feature Request：剪贴板-only 模式（不自动粘贴）
- 探索 vocotype 是否支持替换词典的动态热加载
- 评估 paraformer 模型版本升级对热词需求的影响

---

## 九、开源兼容性设计

### 9.1 vocotype 作为可选外部依赖

iris-asr-corrector 依赖 vocotype 提供 ASR 能力。vocotype 是独立的第三方软件，Iris 不内置、不分发 vocotype。

README 中需要明确说明：

```markdown
## ASR 实时校正（可选功能）

iris-asr-corrector 是 Iris 的实时语音转写校正引擎。

### 前置依赖
- [VocoType](https://vocotype.com) — 第三方本地语音转写工具（免费）
- 仅支持 macOS

### 快速开始
1. 下载安装 VocoType
2. 运行 `iris3 build-asr-prompt --deploy` 生成并部署校正配置
3. 在 VocoType 设置中关闭 AI 优化、清空替换词典
4. 运行 `iris3 asr-corrector` 启动校正服务
5. 正常使用 VocoType 语音输入，Iris 后台自动校正

### 不使用 vocotype？
iris-asr-corrector 是可选模块，不安装 vocotype 不影响 Iris 其他功能。
```

### 9.2 不安装 vocotype 时的降级策略

```
iris3 asr-corrector      → 启动时检测 vocotype 是否安装
                            ├─ 已安装 → 正常工作
                            └─ 未安装 → 提示 "vocotype 未安装，校正引擎不可用"
                                       返回错误码，不影响系统

iris3 build-asr-prompt    → 无影响，正常生成三件套
iris3 asr-audit           → 无影响，纯本地分析
iris3 其他所有命令         → 无影响，完全不依赖 vocotype
```

**设计原则：vocotype 是 iris-asr-corrector 的前置条件，但 iris-asr-corrector 是 Iris 的可选模块。** 缺失时仅该命令不可用，Iris 整体功能不受任何影响。

### 9.3 开源信息泄露防护

#### 9.3.1 风险识别

| 风险点 | 泄露内容 | 风险等级 |
|--------|---------|:---:|
| `data/asr_replace_dict.json` | 团队成员姓名、项目名、内部术语 | 🔴 高 |
| `data/asr_feedback.jsonl` | 实际语音转写文本，可能含敏感讨论内容 | 🔴 高 |
| `output/asr-modify/*.json` | 同上 | 🔴 高 |
| `data/asr_prompt_v2.md` | 领域上下文、人名列表 | 🟡 中 |
| `output/asr-modify/asr-prompt-*.md` | 同上 | 🟡 中 |
| `config/asr_profiles.json` | LLM 配置（无密钥），路径信息 | 🟢 低 |
| 源代码 | vocotype 配置路径硬编码 | 🟢 低 |

#### 9.3.2 防护措施

```
防护层 1：.gitignore（已覆盖）
  data/                         ← 已在 .gitignore 中全量忽略
  output/asr-modify/            ← 需确认已在 .gitignore 中
  *.jsonl                       ← 需确认已在 .gitignore 中

防护层 2：.example 文件模式（遵循 Iris 现有约定）
  config/asr_profiles.json      → gitignore
  config/asr_profiles.json.example → 版本控制（脱敏版）
  内容差异：
    实际版：包含完整路径和团队特定 LLM 配置
    示例版：占位符路径 + 通用模型名 + 注释说明

防护层 3：代码中零硬编码敏感信息
  ✗ 不在源代码中写 vocotype 配置文件路径字符串
  ✓ 从环境变量 IRIS_VOCOTYPE_DIR 读取，默认值指向标准安装路径
  ✗ 不在 prompt 模板中内嵌真实人名
  ✓ 人名从 Wiki 动态加载，Wiki 本身在用户私有仓库中

防护层 4：文档脱敏
  设计文档 docs/asr-corrector-design.md 中：
  - 示例热词、替换词条均使用虚构/通用示例
  - 不包含真实团队成员姓名
  - 不包含真实项目名
```

#### 9.3.3 具体改动

**代码层：**

```python
# corrector.py — vocotype 路径从环境变量读取
VOCO_DIR = os.environ.get(
    "IRIS_VOCOTYPE_DIR",
    os.path.expanduser("~/Library/Application Support/VocoType")
)
```

**配置文件：**

```json
// config/asr_profiles.json.example（版本控制，脱敏）
{
  "default": {
    "replace_dict": "data/asr_replace_dict.json",
    "llm_prompt": "data/asr_prompt_v2.md",
    "mode": "full",
    "llm": {
      "provider": "deepseek",
      "model": "deepseek-v4-flash",
      "timeout_ms": 4000,
      "max_tokens": 2048,
      "temperature": 0.1
    }
  },
  "_comment": "复制此文件为 asr_profiles.json 并根据需要修改。replace_dict 和 llm_prompt 由 build-asr-prompt --deploy 自动生成"
}
```

**数据文件路径约定：**

```
路径                     gitignore    包含敏感信息    说明
──────────────────────────────────────────────────────────────────
data/                    ✓ 已忽略     ✓              运行时数据，不提交
  asr_replace_dict.json                         build-asr-prompt 生成
  asr_prompt_v2.md                              build-asr-prompt 生成
  asr_feedback.jsonl                            iris-asr-corrector 写入
output/asr-modify/       ✓ 需确认    ✓             归档副本，不提交
config/
  asr_profiles.json      ✓ 需新增    ✗（仅LLM参数） LLM使用参数
  asr_profiles.json.example ✓ 版本控制 ✗          脱敏示例
```

### 9.4 设计决策更新

| 决策 | 结论 |
|------|------|
| vocotype 依赖 | 可选外部依赖，缺失仅影响 iris-asr-corrector |
| 降级策略 | 其他模块零影响 |
| 敏感信息 | .gitignore + .example 模式 + 零硬编码 |
| vocotype 路径 | 环境变量 `IRIS_VOCOTYPE_DIR`，有默认值 |
| 配置文件 | `asr_profiles.json.example` 脱敏版进入版本控制 |

---

## 十、实施记录（2026-07-19）

以下为实施过程中对原设计的调整：

### 10.1 粘贴策略变更

设计为"双次粘贴覆盖"，实测发现 vocotype 的粘贴不走标准 Cmd+V（使用剪贴板回退链路），Cmd+Z 无法可靠撤销，导致原始文本和校正文本叠加。

**实际方案**：等 vocotype 粘贴完成后，按原始文本长度模拟 Backspace 删除，再粘贴校正版。

### 10.2 Prompt V2 → V3

设计为 V2"规则式"（~800 字），实施中升级为 V3"编辑助手"（~930 字）：
- 加入**领域保护名单**（项目名、概念名），防止 LLM 误改专有名词
- 加入**润色能力**（去重复、合并碎片、补标点），对标飞书妙记
- 加入**音近推断示例**，引导 LLM 做语境消歧
- 角色从"校正助手"升级为"编辑助手"

### 10.3 deepseek-v4-flash 推理问题

v4-flash 默认开启 Chain-of-Thought 推理，即使 Prompt 明确要求"不解释"，仍输出数千字推理过程。

**解决方案**：
1. 通过 `LLMRequest.extra_body` 传递 `{"thinking": {"type": "disabled"}}` 到 DeepSeek API
2. 安全网：LLM 输出 > 输入 ×3 时判定为 CoT 泄漏，自动降级为词典结果

### 10.4 高危映射过滤

实测发现词典中有 `在→ZAI` 等映射——"在"是最高频中文字之一，导致大面积误伤（"一起在深圳"→"一起ZAI深圳"）。

**解决方案**：在 `format_replace_dict` 和 `analyze_dict_quality` 中自动过滤单字高频中文映射（在、是、的、了、我…约 80 个最高频字）。

### 10.5 热键格式兼容

设计时假设 vocotype 热键格式为 `shift+control+KeyZ`，实际使用中遇到 `alt+ArrowRight`（方向键组合）和 `AltRight`（单个修饰键）。扩展了 `_parse_hotkey` 的解析能力。

### 10.6 LLM 耗时追踪

新增 `llm_time_ms` 字段到 `AsrCorrection`，终端输出和 JSONL 日志中均包含处理耗时。通过 `LLMRequest.extra_body` 机制透传到 API 层。

### 10.7 Prompt 热加载

`iris-asr-corrector` 启动后每 5 秒检查 `data/asr_prompt.md` 文件 mtime，变化时自动重载，无需重启。

### 10.8 文件命名

Prompt 文件名从 `asr_prompt_v2.md` 改为 `asr_prompt.md`（去掉版本后缀，固定路径）。

### 10.9 v3.19.3 交互体验改进（2026-07-19）

`build-asr-prompt` 执行慢（~1-2 分钟）但过程几乎无反馈的问题已修复：

- **`_progress.py`**：新增线程安全进度追踪器 `ProgressTracker`，零外部依赖
- **Phase 1 进度增强**：`hotwords.py` 集成 ProgressTracker，逐批显示 X/Y 完成 + 耗时
- **Phase 2 进度补齐**：`extractor.py` 补齐逐批进度输出（此前完全静默）
- **Phase 3 标签修正**：「LLM Prompt 优化压缩」→「校正提示词渲染」（实际无 LLM 调用）
- **Phase 级耗时**：每个 Phase 完成后输出耗时和产出摘要
- **总耗时汇总**：流程结束时打印全流程耗时和阶段产出
- 测试：111 个已有测试全部通过，并发模拟验证线程安全

### 10.10 v3.19.1 代码质量加固（2026-07-19）

上线后深度代码审查发现 6 项改进点，已全部实施：

- **JSONL 反馈格式统一**：`save_correction()` 与 `_append_feedback_jsonl()` 的写入字段集对齐，`llm_time_ms` 写入和加载路径一致
- **热词去重修正**：移除去重前的前置截断，改为遍历全量候选后截断
- **死代码清理**：移除 `build_optimize_prompt()` 和 `_clean_text()`（V2 残留，V3 起由 `_render_v2()` 替代）
- **等待策略改进**：`_replace_text_in_place` 从固定 `delay 0.2s` 改为基线 0.15s + 剪贴板稳定性轮询
- **常量复用**：`coverage.py` 用 `get_wiki_prefix()` 替换硬编码 prefix_map
- 测试：84 ASR + 251 单元测试全部通过，无回归

### 10.11 v3.19.12 LLM 推理模式管控 + 上下文效果评估（2026-07-21）

#### LLM 思考模式关闭

**问题**：`deepseek-v4-flash` 默认开启 thinking 模式，ASR 校正时输出冗长 CoT 推理过程（数百字），导致耗时 2-5 秒且频繁触发「输出超长降级为词典结果」。

**修复**：
- `AsrCorrector._correct_llm()` 两处 LLM 调用添加 `extra_body={"thinking": {"type": "disabled"}}`
- `EnvironmentConfiguredLLMProvider.generate()` 路由路径 `_try_call` 闭包补传 `extra_body=request_data.extra_body`（此前仅 `force_model` 路径正确传递，路由路径静默丢弃）

#### 上下文 A/B 对比

**动机**：无法量化评估上下文窗口对 LLM 校正效果的影响。

**实现**：
- `AsrCorrector` 新增 `context_ab` 参数，通过 CLI `--context-ab` 开关控制
- 开启后：上下文非空时每句跑两次 LLM（带/不带上下文），对比差异
- `_correct_llm()` 新增 `force_no_context` 参数，支持强制跳过上下文注入
- 日志输出差异摘要，A/B 数据写入 feedback JSONL 的 `context_ab` 字段
- 新 `AsrCorrection.context_ab` 字段，非 None 时序列化到 JSONL

**设计权衡**：
- 默认关闭（日常使用零额外开销）
- 开启时 LLM 调用翻倍（仅评估场景使用）
- 第一句无上下文时自动跳过，第二句起生效

### 10.12 v3.19.13 ASR shutdown SIGINT 保护（2026-07-21）

#### 问题

用户 `Ctrl+C` 停止引擎后，`finally` 块依次执行 `_hotkey_monitor.stop()`（含 `thread.join(3s)`）→ `_shutdown_executor()`。若在 `join()` 期间再次 `Ctrl+C`，`KeyboardInterrupt` 中断清理序列，executor 未关闭，Python 3.13 atexit 阶段 `ThreadPoolExecutor._python_exit` 再次抛异常。

#### 修复

将 SIGINT 屏蔽从 `_shutdown_executor()` 内部提升到 `run_forever()` 的 `finally` 块顶层，统一保护 `hotkey_monitor.stop()` + `_shutdown_executor()` 整个序列。`_shutdown_executor()` 简化为纯业务逻辑。

#### 设计考量

- 信号屏蔽在 `finally` 中恢复，保证不泄露 `SIG_IGN` 到外层
- 不修改 `_HotkeyMonitor.stop()` 内部逻辑（其职责单一：停止监听）
- 与 Python 3.13 atexit 行为兼容
