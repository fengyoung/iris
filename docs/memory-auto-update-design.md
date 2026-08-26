# Iris 记忆系统自动更新方案

> 当前验证版本：Iris 3.28.0 · 现行持久化遵循 `FileLock` 稳定锁文件与统一原子写约定，详见 [工程可靠性设计](engineering-reliability-design.md)。
> 目标：从手动/半自动更新演进为全自动化的记忆学习系统。
> 创建时间：2026-07-22 · **状态：✅ 已全面实施（v3.19.14）**

---

## 实施状态

| Phase | 内容 | 状态 |
|:---:|------|:---:|
| 1 | LLM 双通道记忆提取器 | ✅ 已实施 |
| 2 | 会话模式挖掘器 | ✅ 已实施 |
| 3 | 全自治生命周期 | ✅ 已实施 |

详细变更见 [CHANGELOG.md](../CHANGELOG.md#v31914-2026-07-22)。

---

## 一、现状分析

### 1.1 记忆系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     Iris 记忆系统（6 模块）                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  长期记忆（long_term/）                                          │
│  ┌─────────────────────┐  ┌─────────────────────┐              │
│  │ UserProfileMemory   │  │ CorrectionMemory    │              │
│  │ profile.json        │  │ corrections.json    │              │
│  │ ├─ iris_persona     │  │ ├─ items{}          │              │
│  │ └─ user_preferences │  │ │  concept→preferred │              │
│  │    ├─ likes[]       │  │ │  update_count      │              │
│  │    ├─ dislikes[]    │  │ │  last_source       │              │
│  │    ├─ style_prefs[] │  │ └─ updated_at        │              │
│  │    └─ notes[]       │  └─────────────────────┘              │
│  └─────────────────────┘                                        │
│                                                                 │
│  会话记忆（session/）         工作上下文（working/）              │
│  ┌─────────────────────┐  ┌─────────────────────┐              │
│  │ SessionMemoryStore  │  │ WorkingContextStore │              │
│  │ latest_session.json │  │ working_context.md  │              │
│  │ ├─ recent_questions │  │ ├─ current_task     │              │
│  │ ├─ recent_topics    │  │ ├─ pending_items    │              │
│  │ ├─ topic_threads    │  │ ├─ recent_changes   │              │
│  │ └─ recent_summary   │  │ └─ notes            │              │
│  └─────────────────────┘  └─────────────────────┘              │
│                                                                 │
│  自治引擎（lifecycle.py）       管理接口（manager.py）            │
│  ┌─────────────────────┐  ┌─────────────────────┐              │
│  │ MemoryLifecycle     │  │ LongTermMemoryMgr  │              │
│  │ ├─ age() 老化       │  │ ├─ list_memory()    │              │
│  │ ├─ summarize() 摘要 │  │ ├─ delete() 删除    │              │
│  │ ├─ detect_conflicts │  │ ├─ export() 导出    │              │
│  │ └─ merge() 合并     │  │ └─ import() 导入    │              │
│  └─────────────────────┘  └─────────────────────┘              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 当前更新触发机制

```
触发路径                      触发方式        频率        自动化程度
──────────────────────────────────────────────────────────────────
① Q&A 正则匹配                每次 iris ask   实时        ✅ 自动
  MemoryUpdater.apply_updates()
  └─ 正则检测 "记住"、"纠正"、"我喜欢" 等关键词
  └─ 从问题文本中提取偏好/纠正规则
  └─ 写入 profile.json / corrections.json

② Claude Code → Iris 同步      daily-start     每日        ❌ 半自动
  sync_memory.py
  └─ 读取 .claude/projects/<slug>/memory/*.md
  └─ 解析 frontmatter 元数据
  └─ 分类：profile / corrections / notes
  └─ 写入 profile.json / corrections.json

③ 记忆自治维护                  daily-start /   按需        ❌ 手动
  MemoryLifecycle.maintenance()  memory-maintenance
  └─ 老化检查（>90天未更新的纠正 → 归档）
  └─ 摘要压缩（偏好列表 >10条 → 裁剪）
  └─ 冲突检测（同一概念纠正 ≥3次 → 标注）
  └─ 仅报告，不自动执行（除 --auto-age）

④ 手动管理                      CLI 命令        按需        ❌ 手动
  memory-list / memory-delete / memory-export / memory-import
```

### 1.3 核心差距

| # | 差距 | 影响 |
|---|------|------|
| 1 | **正则提取太粗糙** | `apply_text_update` 只能识别固定句式，无法理解自然语言表达 |
| 2 | **只分析问题，不分析对话** | 只检查用户原始输入，不看 LLM 回答和完整上下文 |
| 3 | **会话模式无晋升机制** | 用户在 session 中反复问同一主题，不会自动进入长期记忆 |
| 4 | **Claude Code 记忆同步非实时** | 只有 daily-start 才会同步，延迟最长 24h |
| 5 | **自治维护只诊断不治疗** | 检测到冲突/老化只报告，需人工介入 |

### 1.4 流程示意（当前）

```
用户提问 "记住，我喜欢简短的回答"
        │
        ▼
  MemoryUpdater.apply_updates()
        │
        ├── EXPLICIT_MEMORY_RE 匹配 ✓
        ├── "我喜欢" → _extract_after("简短的回答")
        └── profile.json: likes += ["简短的回答"]
        │
        ▼
  ✅ 偏好已记录

────────────────────────────────────────────────

用户提问 "帮我分析一下这个季度的数据，不用太详细"
        │
        ▼
  MemoryUpdater.apply_updates()
        │
        ├── EXPLICIT_MEMORY_RE? → "帮我分析" 不匹配 ✗
        ├── IMPLICIT_CORRECTION_RE? → 无纠正句式 ✗
        └── 无操作
        │
        ▼
  ❌ "不用太详细" 这个偏好没有被捕捉

────────────────────────────────────────────────

用户提问 "你上次说的那个方案，我后来觉得还是A方案好"
        │
        ▼
  MemoryUpdater.apply_updates()
        │
        ├── 正则全不匹配 ✗
        └── 无操作
        │
        ▼
  ❌ 隐含偏好（用户倾向 A 方案）未被提取
```

---

## 二、自动更新方案

### 2.1 方案总览

```
Phase 1 ──────── Phase 2 ──────── Phase 3
(轻量，立即实施)   (中等，架构升级)   (远期，全自治)

LLM 记忆提取器    会话模式挖掘器     全自治记忆生命周期
    │                │                  │
    ├── LLM 分析     ├── 跨会话分析      ├── 连续学习循环
    │   完整对话     │   主题聚类        ├── 主动记忆维护
    ├── 提取偏好     ├── 模式发现        ├── 冲突自动解决
    ├── 提取纠正     ├── 自动晋升        └── 健康仪表盘
    └── 自动写入     └── Wiki 候选建议
```

### 2.2 Phase 1：LLM 对话记忆提取器

**改动**：改造 `MemoryUpdater`，增加 LLM 分析通道。

```
当前流程：
  question → 正则匹配 → 提取 → 写入

改造后：
  question + answer + memory context
      │
      ├── 正则快速通道（免费，毫秒级）
      │   └── 显式命令："记住X"、"纠正Y" → 直接写入
      │
      └── LLM 深度通道（轻量模型，可选）
          └── 分析完整对话 → 提取隐含记忆 → 写入
```

**实施细节**：

```python
# memory_updater.py 改造
class MemoryUpdater:
    def apply_updates(self, question: str, answer: str = None, 
                      context: dict = None) -> List[str]:
        updates = []
        
        # 通道 1：正则快速匹配（保持现有逻辑)
        if EXPLICIT_MEMORY_RE.search(question):
            updates.extend(self._profile_memory.apply_text_update(question))
            updates.extend(self._correction_memory.apply_text_update(question))
        
        # 通道 2：LLM 深度分析（新增）
        if self._should_deep_analyze(question, answer):
            llm_updates = self._extract_with_llm(question, answer, context)
            updates.extend(llm_updates)
        
        return updates
    
    def _should_deep_analyze(self, question, answer) -> bool:
        """判断是否需要 LLM 分析：问题够长、非纯检索、非显式命令"""
        if not answer:
            return False
        if len(question) < 15:  # 太短的问题通常不需要
            return False
        if EXPLICIT_MEMORY_RE.search(question):
            return False  # 显式命令已被正则处理
        return True
    
    def _extract_with_llm(self, question, answer, context):
        """用轻量 LLM 从对话中提取记忆"""
        prompt = MEMORY_EXTRACTION_PROMPT.format(
            question=question, 
            answer=answer[:2000],  # 截断长回答
            existing_prefs=self._profile_memory.render_for_prompt(),
            existing_corrections=self._correction_memory.render_for_prompt(question),
        )
        response = llm_service.chat(prompt, model="deepseek-v4-flash", 
                                     thinking=False, max_tokens=500)
        return self._parse_llm_memory_response(response)
```

**LLM Prompt 设计**：

```
你是一个记忆提取器。分析以下对话，提取用户的新偏好、纠正或事实。

已知偏好：
{existing_prefs}

已知纠正规则：
{existing_corrections}

用户问题：
{question}

系统回答：
{answer}

请提取对话中隐含的以下信息（仅提取新信息，已有信息不要重复）：
1. 用户偏好（喜欢/不喜欢什么类型的回答、格式、风格）
2. 术语纠正（哪些概念的理解需要修正）
3. 新的事实信息（用户提及的个人信息、工作背景等）

输出 JSON 格式：
{
  "new_likes": ["..."],
  "new_dislikes": ["..."],
  "new_corrections": [{"concept": "...", "preferred": "..."}],
  "new_notes": ["..."],
  "confidence": 0.8
}
仅输出 JSON，不要其他文本。
```

**成本估算**：

| 项目 | 值 |
|------|-----|
| 模型 | deepseek-v4-flash |
| 平均 tokens/次 | ~800 input + ~200 output |
| 成本/次 | < ¥0.001 |
| 日调用量（假设 20 次 Q&A） | < ¥0.02 |
| 触发条件 | 仅当回答存在 + 问题 > 15字 + 非显式命令 |

**改动量**：`memory_updater.py` 改造 ~80 行 + 新增 prompt 模板 ~30 行 = ~110 行。

---

### 2.3 Phase 2：会话模式挖掘器

**目标**：从多次会话中识别模式，自动晋升为长期记忆。

**触发时机**：集成到 `daily-start`，每日运行一次。

**流程**：

```
SessionMemoryStore.load()
    │
    ├── recent_questions (最近 8 个问题)
    ├── topic_threads (主题线程计数)
    └── recent_topics (最近 12 个主题)
    
    ↓ 喂给 LLM
    
Prompt: "分析以下用户最近的问题模式：
         识别 1) 反复出现的主题 2) 隐含的兴趣方向 3) 可形成知识的新事实"
    
    ↓ LLM 输出
    
{
  "recurring_themes": [
    {"theme": "ASR 语音识别", "count": 5, "suggest_wiki": true},
    {"theme": "双周报格式", "count": 3, "suggest_preference": true}
  ],
  "new_facts": [
    "用户正在推进 智能巡检项目，目标准召达标"
  ],
  "preference_patterns": [
    "用户倾向于先看数据再听分析"
  ]
}

    ↓ 自动应用
    
├── 高置信度 (>0.8) → 自动写入 long_term
├── 中置信度 (0.5-0.8) → 写入 notes 待确认
└── 低置信度 (<0.5) → 丢弃
```

**关键能力**：

| 能力 | 说明 | 示例 |
|------|------|------|
| 主题聚类 | 如果 5 次会话涉及同一主题 → 标记关注领域 | "最近频繁讨论 ASR，建议创建 Wiki 页面" |
| 偏好发现 | 如果用户多次要求简短/详细 → 提取偏好 | "用户连续 3 次要求精简 → 偏好短回答" |
| 事实积累 | 从对话中提取零散事实，聚合为新知识 | "已知：用户是数据部门负责人，关注 ASR" |
| Wiki 候选 | 反复讨论的主题 → 自动加入 discover-wiki | "智能巡检" 出现 5+ 次 → 建议创建 Wiki |

**改动量**：新增 `src/iris/memory/session_miner.py` ~150 行 + `daily_start` 集成 ~10 行 = ~160 行。

---

### 2.4 Phase 3：全自治记忆生命周期

**目标**：记忆系统完全自驱动，无需人工介入。

```
┌──────────────────────────────────────────────────────────────┐
│                    全自治记忆生命周期                          │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐  │
│   │ 学习层   │    │ 维护层   │    │ 同步层   │    │ 健康层   │  │
│   │          │    │          │    │          │    │          │  │
│   │ Q&A 记忆 │───→│ 老化归档 │    │ CC↔Iris │    │ 覆盖率   │  │
│   │ 实时提取 │    │ 摘要压缩 │    │ 双向同步 │    │ 冲突率   │  │
│   │          │    │          │    │          │    │          │  │
│   │ 会话模式 │───→│ 冲突自动 │    │ 增量更新 │    │ 新鲜度   │  │
│   │ 定期挖掘 │    │ 解决     │    │ 实时     │    │ 告警     │  │
│   └─────────┘    └─────────┘    └─────────┘    └─────────┘  │
│                                                              │
│   触发机制：                                                  │
│   ┌──────────────────────────────────────────────────────┐  │
│   │ 实时触发    │ 每次 Q&A      │ Phase 1 LLM 提取器     │  │
│   │ 每日触发    │ daily-start   │ Phase 2 会话挖掘器     │  │
│   │            │               │ sync-memory 同步        │  │
│   │            │               │ maintenance 维护        │  │
│   │ 按需触发    │ 用户手动       │ memory-* CLI 命令      │  │
│   └──────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**关键能力**：

| 能力 | 说明 |
|------|------|
| **自动老化** | 纠正规则超过 90 天未更新 → 自动归档，无需 `--auto-age` |
| **冲突自动解决** | 同一概念纠正 ≥ 3 次 → LLM 判断最终值 → 合并 → 归档旧值 |
| **双向同步** | CC memory 更新时自动触发 sync-memory（inotify 监听文件变化） |
| **健康告警** | 冲突数 > 阈值 / 过期记录 > 阈值 / 覆盖率下降 → 主动通知 |
| **记忆健康仪表盘** | `memory-status` 输出完整健康指标 |

**改动量**：~300 行（涉及 lifecycle.py / sync_memory.py / manager.py 改造）。

---

## 三、方案对比

| 维度 | Phase 1 | Phase 2 | Phase 3 |
|------|:------:|:------:|:------:|
| 实施周期 | 半天 | 1-2 天 | 3-5 天 |
| 改动量 | ~110 行 | ~160 行 | ~300 行 |
| LLM 成本增量 | < ¥0.02/天 | < ¥0.01/天 | < ¥0.03/天 |
| 自动化程度 | 从 30% → 70% | 从 70% → 90% | 从 90% → 98% |
| 核心价值 | 不再依赖正则，自然语言理解 | 跨会话模式发现，记忆自然积累 | 完全自驱，零人工 |
| 风险 | 低（独立模块，不影响现有流程） | 中（需改造 daily-start） | 中高（涉及自动决策） |

## 四、推荐实施路径

```
立即（本周）：
  Phase 1 — LLM 对话记忆提取器
  解决最大痛点：自然语言偏好无法被正则捕获

下周：
  Phase 2 — 会话模式挖掘器
  实现长期记忆的自然积累，不再依赖手动触发

按需：
  Phase 3 — 全自治生命周期
  当 Phase 1+2 稳定运行 2 周后，逐步开启自动决策
```

---

## 五、与并发方案的协同

Phase 1 改造 `MemoryUpdater` 时，直接引入 `FileLock`（复用多 Agent 并发方案的步骤 1），确保 LLM 提取的记忆写入与并发 Q&A 不冲突。

Phase 2 的 `session_miner.py` 读取 session 文件 + 写入 long_term 时同样使用 FileLock 保护，天然兼容多 Agent 场景。
