# iris meeting-live-assistant — 实时会议助理 方案设计 v1.0

**日期**：2026-08-10 · **状态**：已实现（v3.23.0 落地，v3.23.1 补充修复，v3.23.3 全量优化：双段流水线/短段门控/退出加固/结束总结/检索 deadline/长段支持，v3.24.0 全面加固：写回机制重构/预取原子化/LLM 治理/交叉冲突防护）· **最终版本**：产品 3.24.0 / 协议 3.18

---

## 1. 背景与目标

会议中语音信息密度高、转瞬即逝。本功能在会议进行中**实时**将你的语音转写为文本，逐段提炼**关键要点、风险、问题、决策点**，并提示你「此刻值得追问什么」，让与会者在会议当下就抓住关键，而不是会后补课。

### 定位
- **独立命令**：`iris meeting-live-assistant`，常驻进程，与 asr-corrector 运行时互斥
- **差异化**：与 `transcribe-meeting`（事后批量转写纪要）互补——本功能服务**会议当下**
- **复用优先**：ASR 校正、知识库检索、LLM 全部复用现有资产，新代码只写编排与分析

### 输入输出
- 输入：vocotype 按住右 Option 说话（原生行为不变），松开转写写入剪贴板
- 输出：终端实时面板 + Markdown 过程文档（实时增量）

---

## 2. 需求清单（冻结）

| # | 需求 | 说明 |
|---|------|------|
| 1 | 独立 CLI 命令 | `iris meeting-live-assistant`（59→60 命令），新模块 `src/iris/assistant/` |
| 2 | 语音输入 | vocotype 右 Option 按住说话（原生），松开转写写剪贴板 = 一个语音段 |
| 3 | 采集层 | 剪贴板轮询监听（0.5s），内容特征判定（`_is_asr_text` + 富文本检查）过滤非语音变化 |
| 4 | 校正层 | 复用 AsrCorrector：替换词典快速校正（Aho-Corasick 毫秒级）+ LLM 深度校正 |
| 5 | 检索层 | 每段校正文本 → EnhancedRetriever（top_k=5，可配） |
| 6 | 理解层 | LLM 逐段分析：要点/风险/问题/决策点/建议提问（结构化 JSON） |
| 7 | 呈现层 | 终端面板：本段校正文本 + 分析 + 建议提问 + 会议累计清单 |
| 8 | 会议上下文 | 全场段落/要点/决策/风险累积为「会议状态」，供后续段分析引用 |
| 9 | 互斥 | 启动检测 asr-corrector 实例（ProcessRegistry），存在则提示让位退出；自身防重复实例 |
| 10 | 自动化测试 | 热键注入（CGEventPost 右 Option 61，已验证）驱动端到端测试 |
| 11 | 配置 | app.json 新增 `assistant` 段（output_dir/top_k/llm_model/poll_interval），带 example |
| 12 | 过程文档输出 | 实时增量写 Markdown：`--output <path>` > `assistant.output_dir` > `data/meeting-live/YYYYMMDD-HHMMSS-会议记录.md`；frontmatter + 逐段记录 + 会议累计清单（原子重写保证累计区实时准确） |
| 13 | 文档同步 | CHANGELOG/CLAUDE/README + 测试数更新 |

**v1 不做**：会后纪要生成 · 连续自动录音 · 自定义触发键 · macOS 通知 · SOURCE 自动归档（`--output` 可指到 SOURCE 目录，归不归用户决定）

**积压策略**：丢弃积压——处理中到达的新段暂存为「最新待处理」，当前段完成后只处理最新段，中间段直接丢弃（会议节奏下 LLM 分析 3-8s 必然慢于说话，排队会失控，丢弃保证实时性）

---

## 3. 总体架构（5 层流水线）

```
┌─────────────────────────────────────────────────────────────┐
│  采集层  _clipboard.py                                       │
│  剪贴板轮询 0.5s · vocotype 转写检测（文本特征+富文本）       │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  校正层  _corrector.py（复用 wiki/asr/corrector.py）          │
│  Aho-Corasick 替换词典（毫秒级）→ LLM 深度校正（近期上下文）  │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  检索层  _retriever.py（复用 qa/retrieval）                   │
│  EnhancedRetriever top_k=5：Wiki 页面/文档/记忆/图谱上下文    │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  理解层  _analyzer.py                                        │
│  LLM 分析：关键要点 / 风险 / 问题 / 决策点 / 建议提问          │
│  输入 = 校正文本 + 检索上下文 + 会议状态（JSON 结构化输出）    │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  输出层  _panel.py + _doc_writer.py                          │
│  ① 终端面板：本段校正文本+分析+建议提问+累计清单              │
│  ② 过程文档：Markdown 原子重写（frontmatter+逐段+累计区）     │
└─────────────────────────────────────────────────────────────┘
```

**积压丢弃实现在调度器（live.py）**：单工作线程 + 「最新段指针」——处理中若新段到达，仅覆盖 pending 指针，完成后只消费最新指针。

---

## 4. 核心时序（每段处理）

```
t0  剪贴板变化 → 内容特征判定通过 → 生成 VoiceSegment(seq+1)
t1  词典快速校正（毫秒级）→ 立即面板显示校正文本（实时反馈）
t2  LLM 深度校正（线程池，≤8s 降级链）
t3  校正文本 → EnhancedRetriever 检索（与 LLM 校正并行）
t4  校正文本 + 检索上下文 + 会议状态 → LLM 分析（结构化 JSON）
t5  解析校验 → 更新会议状态 → 面板渲染 → 文档原子重写
t6  回到轮询；若 t0 后又有新段 → 覆盖 pending，丢弃中间段
```

**失败降级链**：LLM 不可用 → 仅词典校正 + 无分析（面板显示原文+「分析不可用」）→ 会议继续不中断。

---

## 5. 模块结构

```
src/iris/assistant/
├── __init__.py        # 导出 MeetingLiveAssistant 主类
├── models.py          # VoiceSegment / SegmentAnalysis / MeetingState / AssistantConfig（Pydantic）
├── _clipboard.py      # ClipboardWatcher：轮询 + vocotype 转写检测（复用 corrector 特征判定）
├── _corrector.py      # CorrectorAdapter：包装 AsrCorrector 双通道，复用热词/词典加载
├── _retriever.py      # RetrieverAdapter：包装 EnhancedRetriever，top_k 可配
├── _analyzer.py       # SegmentAnalyzer：LLM 结构化分析 + JSON 解析校验 + 失败降级
├── _session.py        # MeetingSession：会议状态累积 + 段落序号 + 积压指针
├── _doc_writer.py     # DocWriter：Markdown 原子重写（frontmatter/逐段/累计区）
├── _panel.py          # PanelRenderer：终端面板渲染（ANSI 清屏 + 分区布局）
└── live.py            # MeetingLiveAssistant：主循环编排 + 互斥 + 信号处理

src/iris/app/cli/_handlers/_assistant.py   # CLI handler（注册命令+参数解析）
```

---

## 6. 数据模型

```python
# models.py（Pydantic v2）
class VoiceSegment:
    seq: int                    # 段序号（1-based）
    started_at: datetime        # 检测时刻
    raw_text: str               # 剪贴板原文
    corrected_text: str | None  # 校正后文本（词典/LLM）

class SegmentAnalysis:
    key_points: list[str]       # 关键要点
    risks: list[str]            # 风险
    questions: list[str]        # 讨论中的问题
    decisions: list[str]        # 决策点
    suggested_questions: list[str]  # 建议你追问的提问

class MeetingState:
    segments: list[VoiceSegment]
    analyses: list[SegmentAnalysis]
    key_points: list[str]       # 累计（去重）
    risks: list[str]
    decisions: list[str]
    open_questions: list[str]   # 待解决问题（会被后续段回答）
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
    "poll_interval": 0.5,      // 剪贴板轮询间隔（秒）
    "doc_rewrite_every": 1     // 每 N 段重写文档（1 = 每段）
  }
}
```

**路径优先级**：`--output <path>` > `assistant.output_dir` > `data/meeting-live/YYYYMMDD-HHMMSS-会议记录.md`

---

## 8. 过程文档格式

```markdown
---
title: 实时会议记录 2026-08-10 11:50
date: 2026-08-10 11:50
type: 实时会议记录
source: meeting-live-assistant
---

# 实时会议记录 2026-08-10 11:50

## 📋 会议累计（实时更新）
### 关键要点
- …
### 决策点
- …
### 风险
- …
### 待解决问题
- …

## 🎙 段 1（11:50:23）
**校正文本**：……
**要点**：- …
**风险**：- …
**问题**：- …
**决策点**：- …
**💡 建议提问**：- …

## 🎙 段 2（11:52:01）
…
```

写入策略：**原子整体重写**（写临时文件 + rename）——每段处理完重写一次（≤100KB 毫秒级，段间隔 ≥10s，无性能风险），保证「会议累计」区实时准确；进程中断时临时文件安全。

---

## 9. 互斥与进程管理

| 场景 | 行为 |
|------|------|
| asr-corrector 在运行 | 检测到实例 → 提示「请先退出 asr-corrector（独占剪贴板）」→ 退出 |
| 本命令重复启动 | ProcessRegistry 拒绝，提示已有实例 |
| Ctrl+C | 优雅退出：最后重写一次文档 → 面板显示会议统计（段数/决策数/风险数） |
| vocotype 未安装/无热键 | 启动检查 ui_settings.json，缺失则警告「仍可手动粘贴文本测试」不阻塞启动 |

---

## 10. 测试计划

| 层级 | 内容 | 依赖 |
|------|------|------|
| 单元 | models 校验 / analyzer JSON 解析与容错 / session 累积去重 / doc_writer 重写与中断安全 / 积压丢弃逻辑 | mock LLM |
| 集成 | 剪贴板写入 → 校正 → 检索 → 分析 → 文档全链路（mock LLM + 真实剪贴板/检索） | 无 vocotype |
| 端到端 | **热键注入**（CGEventPost 右 Option 61，verify_hotkey_inject.py 已验证）：注入按住-松开 → vocotype 真实转写 → 剪贴板 → 全链路；真机手动验证 | vocotype 运行中 |

---

## 11. 风险与对策

| 风险 | 对策 |
|------|------|
| LLM 分析延迟拖垮节奏 | 丢弃积压 + 并行化（校正/检索并行）+ 分析超时降级 |
| 与 asr-corrector 剪贴板冲突 | 启动互斥 + 文档中明确使用说明 |
| 面板渲染混乱 | ANSI 清屏 + 分区固定布局；stderr 日志与面板分离 |
| 检索噪音干扰分析 | top_k 可配 + Prompt 中「无关上下文忽略」指令 |
| 剪贴板被非语音内容污染 | 内容特征判定（复用 corrector 成熟逻辑） |

---

## 12. 版本与交付

- **产品版本**：3.22.5 → **3.23.0**（新功能）
- **协议版本**：3.15 → **3.16**（新增 1 命令）
- **交付物**：新模块 + CLI 命令 + 测试（预计 unit +30~40 / integration +5~10）+ CHANGELOG/CLAUDE/README 同步
