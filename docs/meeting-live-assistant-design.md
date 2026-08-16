# iris meeting-live-assistant — 实时会议助理 方案设计 v2.0

**日期**：2026-08-12 · **状态**：已实现（v3.23.0 落地 → v3.25.0 本地音频 ASR → v3.26.0 四层能力+说话人区分 → v3.26.1 全量优化+评估修复 → v3.26.2 面板双主题视觉方案 → v3.26.3 面板稳定化+并发加固）· **当前版本**：产品 3.26.3 / 协议 3.19

---

## 1. 背景与目标

会议中语音信息密度高、转瞬即逝。本功能在会议进行中**实时**将你的语音转写为文本，逐段提炼**关键要点、风险、问题、决策点**，并提示你「此刻值得追问什么」，让与会者在会议当下就抓住关键，而不是会后补课。

### 定位
- **独立命令**：`iris meeting-live-assistant`，常驻进程，本地麦克风采集（不依赖第三方 App）
- **差异化**：与 `transcribe-meeting`（事后批量转写纪要）互补——本功能服务**会议当下**
- **复用优先**：ASR 校正、知识库检索、LLM 全部复用现有资产，新代码只写编排与分析

### 输入输出
- 输入：sounddevice 麦克风 16kHz 采集 → FunASR Paraformer 实时转写（VAD+ASR+标点+热词）
- 输出：终端实时面板（含洞察推送区）+ Markdown 过程文档（按话题结构化）

---

## 2. 需求清单（冻结）

| # | 需求 | 说明 |
|---|------|------|
| 1 | 独立 CLI 命令 | `iris meeting-live-assistant`，新模块 `src/iris/assistant/` |
| 2 | 语音输入 | sounddevice 麦克风采集（16kHz mono float32，blocksize 40ms） |
| 3 | ASR 层 | FunASR Paraformer（PyTorch）：RMS VAD + 转写 + CT-Transformer 标点 + 热词注入 |
| 4 | 校正层 | CorrectorAdapter：AC 词典快速校正（毫秒级）+ LLM 深度校正（per-speaker 上下文） |
| 5 | 检索层 | 每段校正文本 → EnhancedRetriever（top_k=5，可配） |
| 6 | 理解层 | LLM 批量分析：要点/风险/问题/决策点/建议提问/话题/说话人/待办（结构化 JSON） |
| 7 | 呈现层 | 终端面板：话题标签 + 本段文本 + 分析 + 洞察推送区 + 会议累计；热键交互 |
| 8 | 会议上下文 | 全场段落/要点/决策/风险/话题/说话人累积为「会议状态」，供后续段分析引用 |
| 9 | 进程管理 | ProcessRegistry 防重复实例；Ctrl+C 优雅退出（文档最终写 + 统计帧） |
| 10 | 自动化测试 | 单元 185 用例（assistant 模块）+ 集成端到端 |
| 11 | 配置 | app.json 新增 `assistant` 段（output_dir/top_k/agenda/…）+ `assistant.asr` 段 |
| 12 | 过程文档输出 | 按话题结构化：概览 → 话题卡 → 决策/待办/风险汇总 → 附录折叠 |
| 13 | 文档同步 | CHANGELOG/CLAUDE/README/design/usage + 测试数更新 |

**v2 不做**：会后纪要自动生成 · FunASR 声纹说话人识别（当前为 LLM 语义推断）· macOS 通知 · 知识库自动回写（M 项，暂缓）

**积压策略**：丢弃积压——处理中到达的新段暂存为「最新待处理」，当前批完成后只处理最新批，中间段直接丢弃（LLM 分析必然慢于说话，丢弃保证实时性）

---

## 3. 总体架构（4 层流水线）

```
┌─────────────────────────────────────────────────────────────┐
│  采集层  _audio.py + _asr.py                                  │
│  sounddevice 麦克风 16kHz → VAD（RMS 40ms 帧级判定）          │
│  → FunASR Paraformer 转写（热词+标点）→ 噪音门控 _is_noise    │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  合并层  live.py _audio_loop（merge buffer）                  │
│  内容感知合并（短段 6s 窗口）+ 说话人间隙门控（0.8s/2.0s）     │
│  → 提交 VoiceSegment（speaker_change_signal 标记）            │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  校正层  _corrector.py                                       │
│  AC 替换词典（毫秒级）→ LLM 深度校正（per-speaker 上下文）     │
│  预取：音频线程提交 deep/检索 futures（与上一批分析并行）       │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  理解层  _analyzer.py + live.py _process_batch               │
│  批处理（最多 5 段 / 2s）：合并文本一次 LLM 分析              │
│  输出：要点/风险/问题/决策(置信度)/建议提问/话题/说话人/待办    │
│  + 冲突检测 + 跑偏检测 + 洞察推送                              │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  输出层  _panel.py + _doc_writer.py + _insight.py            │
│  ① 终端面板：话题标签+本段+分析+洞察推送区+累计+热键          │
│  ② 过程文档：按话题结构化（概览→话题卡→汇总→附录折叠）        │
└─────────────────────────────────────────────────────────────┘
```

**线程模型**：
- **音频线程**（主）：sounddevice 回调采集 → VAD feed（40ms 帧切片）→ merge buffer → submit
- **工作线程**：批处理消费段（等 futures → 合并分析 → 落账），与音频线程通过 `_futures_lock` 同步
- **键盘线程**：`select` 非阻塞单键监听（?dtaq）

---

## 4. 核心时序（每批处理）

```
t0  VAD 输出文本 → 噪音门控 → merge buffer（内容感知 + 说话人边界）
t1  merge 刷新 → AC 校正（毫秒级）→ submit（on_publish 预取：deep/检索 futures 入池）
t2  工作线程取批（最多 5 段 / 2s）→ 批量收集 deep/检索（一次性 wait ≤10s）
t3  合并文本 + 检索上下文 + 会议状态 + 说话人历史 + 议程 → LLM 分析（15s deadline）
t4  解析 → 话题追踪（2-gram 去重）→ 冲突检测 → 说话人登记 → 洞察推送
t5  落账（按 seq 升序，v3.26.3 修复首段最后落账的乱序）→ 面板渲染 → 文档重写
t6  回到取批；若期间新段到达 → 覆盖 pending，丢弃中间段
```

**失败降级链**：LLM 不可用 → 仅词典校正 + 无分析（面板显示原文+「分析不可用」）→ 会议继续不中断。

---

## 5. 模块结构

```
src/iris/assistant/
├── __init__.py        # 导出 MeetingLiveAssistant 主类
├── models.py          # SpeakerLabel/DecisionItem/TodoItem/TopicInfo/VoiceSegment/
│                      #   SegmentAnalysis/MeetingState/AssistantConfig/AsrConfig（Pydantic）
├── _audio.py          # AudioCapture：sounddevice 采集（设备热插拔容错 + 自动重连）
├── _asr.py            # ASREngine：VAD（40ms 帧切片）+ Paraformer 转写 + 标点 + 热词
│                      #   （崩溃自动重初始化 + 噪声地板冻结）
├── _corrector.py      # CorrectorAdapter：AC 词典 + LLM 深度校正（per-speaker 上下文）
├── _retriever.py      # RetrieverAdapter：包装 EnhancedRetriever，top_k 可配
├── _analyzer.py       # SegmentAnalyzer：LLM 批量分析 + JSON 解析 + 话题/说话人/待办
├── _session.py        # MeetingSession：会议状态累积 + 话题追踪 + 说话人历史
├── _doc_writer.py     # DocWriter：按话题结构化渲染 + 原子重写（含阶段性总结区）
├── _panel.py          # PanelRenderer：终端面板（话题标签+分析+洞察推送区+电平+告警+统计帧；
│                      #   v3.26.3 区域固高布局 + alt-screen 进出 + 折行 O(n²)→O(n)）
├── _insight.py        # InsightFeed：洞察推送引擎（决策/话题/风险/冲突/待办/说话人，支持暂停；
│                      #   v3.26.3 加锁防 worker↔键盘线程竞态）
├── _logging.py        # 会话文件日志（session_id 命名，双输出；teardown 防句柄泄漏）
├── _theme.py          # Theme + DARK/LIGHT 两套 ANSI 256 色配色（v3.26.2 双主题）
└── live.py            # MeetingLiveAssistant：主循环编排 + merge buffer + 批处理 + 热键

src/iris/app/cli/_handlers/_assistant.py   # CLI handler（注册命令+参数解析）
```

---

## 6. 数据模型

```python
# models.py（Pydantic v2）
class SpeakerLabel:
    speaker_id: str         # "speaker_A" / "speaker_B"（LLM 语义推断）
    role_hint: str          # 主持人 / 汇报人 / 提问者
    is_turn_change: bool    # 本段是否切换说话人

class DecisionItem:
    text: str               # 决策内容
    confidence: str         # confirmed(✅已拍板) / proposed(💬提议) / tentative(❓待定)
    speaker: str            # 谁拍的板

class TodoItem:
    text: str               # 待办内容
    assignee: str           # 责任人
    deadline: str           # 时间节点

class VoiceSegment:
    seq: int                # 段序号（1-based）
    started_at: datetime
    raw_text: str           # ASR 原文
    corrected_text: str     # 校正后文本（词典/LLM）
    analysis: SegmentAnalysis | None
    analysis_status: str    # pending/done/failed/skipped/merged
    speaker: SpeakerLabel   # 说话人（LLM 后验填充）
    speaker_change_signal: bool  # VAD 检测到可能切换

class SegmentAnalysis:
    key_points / risks / questions / suggested_questions / resolved_questions
    decisions: list[DecisionItem]
    topic: str              # 话题标签
    topic_change: bool      # 是否切换话题
    topic_summary: str      # 话题一句话摘要
    todos: list[TodoItem]
    speaker: SpeakerLabel

class MeetingState:
    segments: list[VoiceSegment]
    key_points / risks / decisions / open_questions   # 累计（去重+25 条上限）
    current_topic: str      # 当前话题
    topics: list[dict]      # 已关闭话题（label/start_seq/end_seq/summary）
    speakers: list[dict]    # 说话人统计（id/role/segments）
    todos: list[str]        # 待办去重累计
    summary: str            # 退出时 AI 会议总结
```

---

## 7. 配置设计

```jsonc
// config/app.json（example 同步）
{
  "assistant": {
    "output_dir": "",          // 默认 data/meeting-live/
    "top_k": 5,                // 知识库检索条数
    "llm_model": "",           // 空 = 走全局 LLMService 路由
    "poll_interval": 0.5,      // 段轮询间隔（秒）
    "doc_rewrite_every": 3,    // 每 N 段重写文档（3 = 每 3 段，降低 I/O）
    "short_segment_chars": 15, // 短段门控阈值
    "suggest_every": 3,        // 建议提问生成间隔
    "summary_enabled": true,   // 退出时生成 AI 会议总结
    "panel_theme": "dark",     // 面板主题（v3.26.2，dark/light，非法回退 dark）
    "agenda": "",              // 预设议题（分号分隔），注入分析 prompt + 跑偏检测
    "save_knowledge": false    // 退出时回写知识库（预留，M 项暂缓）
  },
  "asr": {
    "mode": "local",
    "local": {
      "model_dir": "",         // 空 = 自动检测 ModelScope 缓存
      "device": "cpu",
      "sample_rate": 16000,
      "energy_threshold": 0,   // 0 = 自动适应噪声地板
      "batch_size_s": 60       // 单次 VAD+ASR 最大音频长度（秒）
    },
    "hotwords_file": "data/assistant/asr_hotwords.txt",
    "replace_dict_file": "data/assistant/asr_replace_dict.json",
    "llm_correct_enabled": true,
    "llm_correct_timeout_ms": 8000
  }
}
```

**路径优先级**：`--output <path>` > `assistant.output_dir` > `data/meeting-live/YYYYMMDD-HHMMSS-会议记录.md`

---

## 8. 过程文档格式（按话题结构化）

```markdown
---
title: 实时会议记录 2026-08-12 12:00
type: 实时会议记录
source: meeting-live-assistant
---

# 实时会议记录 2026-08-12 12:00

**概览**：8 个话题 · 30 分钟 · 5 决策 · 3 待办 · 12 风险

## 📌 话题 1：售后归拢到校（段 1-28）
**讨论**：提出中仓售后归拢到校需求，先介绍方案看能否满足
**决策**：✅ 先介绍方案，不满足再联动解决

## ✅ 决策汇总
- 重质量检率替代客观差异率（speaker_B）

## 📋 待办汇总
- 方案方介绍归拢方案（assignee: 方案方）

## 📎 附录：完整逐段转写
<details><summary>270 段 · 展开查看</summary>
## 🎙 段 1（12:01:18）· speaker_A
**校正文本**：……
</details>
```

**写入策略**：原子整体重写（临时文件 + rename）。会议进行中为线性增量（实时可读）；退出 force 时全量渲染为话题结构化（最终形态）。

---

## 9. 说话人区分（v3.26.0）

**原理**：LLM 语义推断为主 + VAD 间隙辅助，零额外 LLM 成本、不做声纹识别。

```
VAD 间隙 > 0.8s     → 可能切换（弱信号，merge 不跨人）
VAD 间隙 > 2.0s     → 几乎一定切换（强信号，强制刷新 + 标记）
静音 > 3s           → 最强信号（merge 过期刷新 + 标记）
LLM 分析（后验）     → 确认是否真换人：speaker_id + is_turn_change
```

- **跨批一致性**：`summary_for_prompt` 注入「已识别说话人」历史，LLM 复用既有 ID
- **per-speaker 校正上下文**：`CorrectorAdapter._speaker_ctx` 按说话人隔离（首轮全局兜底）
- **决策归属**：`DecisionItem.speaker` 记录拍板人

---

## 10. 洞察推送与热键（v3.26.0）

**洞察事件**（面板下半屏滚动推送，最多 50 条历史 / 显示 8 条）：

| 事件 | 图标 | 触发 |
|------|:---:|------|
| 决策确认 | ✅ | decisions.confidence == confirmed |
| 话题切换 | 📌 | current_topic 变化 |
| 风险检出 | ⚠ | risks 非空（前 2 条） |
| 语义冲突 | 🔥 | check_conflict 返回非空 |
| 待办识别 | 📋 | todos 非空（前 2 条） |
| 说话人切换 | 🗣 | is_turn_change |

**热键**（select 非阻塞监听，daemon 线程）：

| 按键 | 功能 |
|:---:|------|
| `?` | 显示帮助 |
| `d` | 显示已确认决策（confirmed） |
| `t` | 显示当前话题 / 已讨论话题 |
| `a` | 显示待解决问题 |
| `m` | 手动标记话题边界（v3.26.1，下批分析注入 topic_change 提示） |
| `s` | 暂停/恢复洞察推送（v3.26.1，暂停期事件入 pending 队列恢复刷入） |
| `q` | 优雅退出 |

---

## 11. 核心修复记录（v3.26.0）

| 问题 | 根因 | 修复 |
|------|------|------|
| VAD 尾部内容丢失 | feed() 整块平均 RMS 判定，转写阻塞期间语音被静音稀释 | 40ms 帧切片逐帧判定 |
| LLM 降级链失效 | deadline 约束 HTTP timeout 冲突 + `_dispatch` 否决放行 | deadline 压入 timeout（2s 下限）+ 超时后继续尝试剩余模型 |
| 话题碎片化 | LLM 对同一讨论不同措辞（76 话题/15 分钟） | 2-gram 语义去重（≥2 共享或 ≥33% 重叠） |
| 冲突误报 | 单向否定 + 关键词重叠 | 双向否定 + 仅明确推翻词触发（宁漏报不误报） |
| 话题状态机 | 连续切换提前 return 丢话题；摘要拼接错误 | 关闭后必创建新话题；保留自身摘要 |
| 批处理超时放大 | 逐段 wait futures（5 段最坏 50s） | 一次性 wait 全部（≤10s） |

---

## 12. 互斥与进程管理

| 场景 | 行为 |
|------|------|
| asr-corrector 在运行 | **不冲突**（v3.25.0 起本地音频，无剪贴板依赖，可同时运行） |
| 本命令重复启动 | ProcessRegistry 拒绝，提示已有实例 |
| Ctrl+C | 优雅退出：merge 残留刷新 → worker 有界 join → 会议总结 → 文档最终写 → 统计帧 → 池关闭 → pid 清理 |

---

## 13. 测试计划

| 层级 | 内容 | 依赖 |
|------|------|------|
| 单元（185 用例） | VAD 状态机/帧切片/chunk 不丢失/噪音门控/容量控制/话题状态机/冲突检测/corrector AC/面板渲染/批量 wait/说话人 | mock LLM + mock ASR |
| 集成 | 多段全链路 → 校正 → 检索 → 分析 → 文档（mock LLM） | 无麦克风 |
| 端到端 | 真机验证：本地麦克风 → 真实转写 → 面板/文档/推送 | 麦克风 + 模型缓存 |

---

## 14. 版本与交付

- **v3.23.0**：初版（剪贴板采集 + vocotype）
- **v3.24.x**：写回机制重构 / 并发安全 / 信息完整性 / 性能架构
- **v3.25.0**：本地音频 ASR（sounddevice + FunASR Paraformer），去除 vocotype 依赖
- **v3.26.0**：四层 12 项能力（防御/理解/交互/沉淀）+ 说话人区分 + LLM 降级链 + VAD 尾部丢失修复，协议 3.19（不变）
- **v3.26.1**：深度审查后全量优化（29 项四阶段 + 3 项评估修复）——P0 可用性（ASR 崩溃自动重初始化 / `s` 热键推送暂停恢复 / 面板阶段指示+系统告警区）· P1 体验（面板宽度自适应 / VU 电平条 / USB 热插拔重连 / 超长会议保护 / 剪贴板遗留清理）· P2 能力（建议提问事件驱动+节流 / 批内多说话人提示 / 热词校验 / 噪声地板冻结 / 空会议清理 / `max_segment_chars` 生效）· P3 工程（buffer O(n²)→O(1) / 增量阶段性总结可见化 / `m` 键手动话题边界 / forced_cut 连续发言标注 / 混合文本噪音判定）+ 评估修复（死代码删除 / 建议节流 / 总结渲染进文档）。测试 178 专项 / 1,428 全量，协议 3.19（不变）
- **v3.26.3**（当前）：面板稳定化 + 并发加固——① 区域固高布局（语音 3/分析 2/建议提问 2/洞察推送 4 行，不足补空行、超出截断「…」，高度不再跳动）+ alt-screen 进出（保留/恢复终端回滚历史）+ 折行 O(n²)→O(n) + 洞察推送多行渲染 + 长告警/长标题截断（防边框断裂）+ VU emoji 标签（色盲友好）+ 底部累计条 CJK 宽度修正；② 并发加锁——InsightFeed（push/toggle_pause + visible snapshot）、CorrectorAdapter per-speaker 上下文（LRU ≤10 speaker）、AudioCapture buffer（回调↔主线程）；③ 修复——多段批次按 seq 升序落账（原首段最后落账乱序）、analysis_elapsed 实际耗时（原恒 0）、`asr.local.batch_size_s` 真正生效（原硬编码 60）、`doc_rewrite_every` 默认 3；④ 共享常量 CONF_ICON/DECISION_FG 收敛到 models.py + teardown_session_logger（e2e 防句柄泄漏）。测试 187 专项，协议 3.19（不变）
- **v3.26.2**：面板双主题视觉方案——新模块 `_theme.py`（Theme + DARK/LIGHT 两套 ANSI 256 色配色）+ 整帧全区填充（底色 = 面板色，含空行/分割条/框线/退出统计帧）+ 语义色贯穿（要点/决策✅绿 · 提议💬黄 · 待定❓灰 · 风险⚠橙 · 冲突🔥红 · 话题📌青 · 待办📋蓝 · 说话人🗣紫 · 建议提问💡亮黄 · 告警红底亮黄字 · VU 低绿→中黄→高红渐变）+ 布局安全（纯文本算宽 + ANSI 后包裹，超宽降级纯文本折行）+ 配置 `assistant.panel_theme: dark|light`（非法回退 dark）。测试 186 专项（+8 主题测试），协议 3.19（不变）
