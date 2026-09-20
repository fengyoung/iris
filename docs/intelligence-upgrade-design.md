# Iris 智能升级设计文档

> **目标**：将 iris3 从「问了才答」升级为「主动织网」的情报系统。
> **范围**：5 个新能力模块，按优先级分三阶段实现。
> **状态**：设计草稿，待实现。编写日期：2026-09-20。

---

## 优先级汇总

| 优先级 | 模块 | 核心价值 |
|:---:|---|---|
| P0 | [M2] 决策追踪库 | 决策作为一等公民管理，可回溯、可检索 |
| P0 | [M4] 会前情报包 | 15 分钟备会变 2 分钟，上下文自动重建 |
| P1 | [M1] 主动情报推送 | daily-start 产出可操作信号而非静默执行 |
| P2 | [M3] OKR 证据持续累积 | 双周检查从"从头检索"变"审核积累" |
| P2 | [M5] 知识质量主动发现 | Wiki 脱节问题主动暴露而非依赖手动 lint |

---

## M2：决策追踪库

### 背景与问题

会议产出的最有价值内容是决策，但 iris 当前把会议纪要当普通文档存储。三个月后回溯某个方向为何走到现在，无法快速找到「谁在什么时间、基于什么背景、做了什么决定」。

### 核心设计

**决策作为结构化实体**，独立于会议纪要存储，可跨文档检索和关联。

#### 数据结构

```python
# src/iris/decisions/schema.py

@dataclass
class Decision:
    decision_id: str          # 自动生成，格式 DEC-YYYYMMDD-NNNN
    title: str                # 决策标题（15字以内）
    summary: str              # 决策内容摘要
    context: str              # 决策背景（为什么要做这个决定）
    outcome: str              # 结论/决定内容
    rationale: str            # 理由和依据
    owners: list[str]         # 执行责任人（人物页名称）
    stakeholders: list[str]   # 相关干系人
    decided_at: date          # 决策日期
    review_at: date | None    # 预计复盘时间（可选）
    status: str               # open | implemented | revised | cancelled
    source_doc: str           # 来源文档路径（相对 SOURCE 目录）
    related_krs: list[str]    # 关联的 OKR KR 标识
    related_decisions: list[str]  # 关联的其他决策 ID
    tags: list[str]           # 自由标签
    created_at: datetime
    updated_at: datetime
```

#### 存储方案

- 索引文件：`data/decisions/index.json`（所有决策的结构化列表）
- 单决策文件：`data/decisions/<decision_id>.json`（完整字段）
- Wiki 页面（可选）：`LLM-WIKI/05-决策/决策-<title>.md`（新增 Wiki 类型）

#### 提取流程

`transcribe-meeting` 在生成纪要后，调用 `DecisionExtractor` 从纪要文本提取决策点：

```
纪要文本
  → DecisionExtractor._extract_candidates()   # LLM 识别候选决策段落
  → DecisionExtractor._structure_decision()   # LLM 填充结构化字段
  → DecisionExtractor._resolve_persons()      # 人名 → 人物页归一化
  → DecisionStore.save()                      # 写入 index.json + 单文件
```

提取 prompt 关键点：
- 识别信号词：「决定」「确认」「同意」「不做」「暂缓」「方案选 X」「采用」「放弃」
- 排除：议题讨论（未有结论）、行动项（已有 todos）、纯信息同步
- 一条会议纪要可提取 0-N 条决策

#### CLI 接口

```bash
# 列出决策（支持过滤）
iris decisions list [--status open] [--owner 张三] [--kr KR1.1] [--days 30]

# 搜索决策
iris decisions search "质检方案"

# 查看单条
iris decisions show DEC-20260915-0003

# 手动录入（无对应会议纪要时）
iris decisions add --interactive

# 更新状态
iris decisions update DEC-20260915-0003 --status implemented

# 导出为 Markdown 报告
iris decisions report [--since 2026-07-01] [--output decisions-q3.md]
```

#### 知识图谱集成

在 `build-graph` 时，将决策节点纳入图谱：
- 节点类型：`decision`
- 边类型：`owns_decision`（人物→决策）、`relates_to`（决策→项目/概念）、`resolves`（决策→问题）
- `graph-query --op neighbors` 支持决策节点

#### 与现有模块的集成点

| 集成位置 | 改动内容 |
|---|---|
| `transcribe_meeting/` | 生成纪要后调用 `DecisionExtractor`，结果追加到纪要尾部 |
| `wiki/wiki_generator.py` | 新增 `decision` 页面类型（05-决策/），frontmatter 含 `decision_id` |
| `app/cli/handlers.py` | 注册 `decisions` 命令组 |
| `qa/retriever.py` | 检索时可选包含决策文档 |

---

## M4：会前情报包

### 背景与问题

开一个重要会议前需要快速重建上下文，当前需要手动多次 `iris ask` 拼凑信息，15-20 分钟才能备齐。

### 核心设计

一条命令，自动拉取多源信息，产出 1-2 页结构化情报包。

#### 命令接口

```bash
iris briefing \
  --topic "Q4质检策略复盘" \
  [--participants "李嘉晨,王芳,张迪"] \
  [--date 2026-09-25] \
  [--days 60]          # 查看过去多少天的相关内容，默认 45
  [--output briefing.md]  # 不指定则输出到 data/briefings/<topic>-<date>.md
```

#### 输出结构

```markdown
# 会前情报包：<topic>
> 生成时间：2026-09-20 | 会议日期：2026-09-25 | 参与人：李嘉晨、王芳

## 核心背景（2-3 段）
- 该话题的 Wiki 页面摘要 + 关键项目状态

## 近期进展（最近 N 天）
- 相关会议纪要的关键结论列表（带日期/来源）
- 相关的周报要点

## 待解决问题
- 上次会议留下的 open 问题
- 相关 KR 的未达成项

## 关键决策历史
- 该话题下已有的决策记录（来自 M2 决策追踪库）

## 与会人近期动态（如有参与人信息）
- 每人最近 2 周的相关工作记录（来自人物页）

## 推荐讨论点
- 基于以上信息，LLM 建议的 3-5 个值得在会上讨论的点
```

#### 技术流程

```
briefing --topic X --participants A,B
  ├─ TopicResolver           → 识别话题关联的 Wiki 实体（项目/概念/领域）
  ├─ RecentDocsRetriever     → 检索过去 N 天与话题相关的文档（BM25+向量）
  ├─ DecisionRetriever       → 从决策库查询话题相关决策（依赖 M2）
  ├─ PersonRetriever         → 获取参与人的人物页摘要
  ├─ OpenIssueDetector       → 识别历史会议中未闭合的问题项
  └─ BriefingSynthesizer     → LLM 综合以上信息生成结构化情报包
```

#### 关键实现细节

**TopicResolver**：先做向量检索找最相关的 Wiki 页面，再从页面 frontmatter 提取关联实体，构建 2 跳上下文图。

**RecentDocsRetriever**：与 `qa` 模块复用检索栈，但以「时间降序」为主排序维度，相关性为次。返回文档摘要（非全文），每条带来源路径和日期。

**OpenIssueDetector**：扫描近期会议纪要，识别带「待定」「后续确认」「下次讨论」「需要 X 跟进」等标记的段落，如果未在后续文档中被提及/关闭，标记为 open。

**BriefingSynthesizer**：使用 `adv_model`，单次调用，prompt 包含所有检索结果的结构化摘要（非原文），生成情报包。避免多次 LLM 调用导致延迟。

#### 与现有模块的集成点

| 集成位置 | 改动内容 |
|---|---|
| `src/iris/briefing/` | 新建模块，5 个子文件 |
| `app/cli/handlers.py` | 注册 `briefing` 命令 |
| `decisions/` | 依赖 M2 的决策检索（M2 未完成时降级为空） |
| `wiki/` | 复用 Wiki 页面读取逻辑 |

---

## M1：主动情报推送

### 背景与问题

`daily-start` 目前静默执行维护任务，完成后不产出可操作信号。团队活动产生的信号（飞书群讨论、KR 停滞、异常活动）没有主动提醒机制。

### 核心设计

`daily-start` 完成维护后，追加一节「今日信号摘要」，列出值得关注的变化信号。

#### 信号类型

```python
class SignalType(Enum):
    # 文档缺口类
    UNDOCUMENTED_TOPIC    # 飞书群近期高频话题但 SOURCE 里无对应文档
    STALE_WIKI_ACTIVE     # Wiki 页面 30+ 天未更新，但相关文档流活跃

    # OKR 类
    KR_EVIDENCE_GAP       # KR 已 14 天无新增支撑文档
    KR_AT_RISK            # KR 相关讨论含风险/阻塞关键词

    # 决策类（依赖 M2）
    DECISION_REVIEW_DUE   # 决策的 review_at 日期临近（7 天内）
    OPEN_DECISION_STALE   # open 状态的决策超过 30 天无后续文档

    # 知识图谱类
    NEW_PERSON_NO_PAGE    # 近期文档出现人名但无人物页
    ORPHAN_CONCEPT        # 概念被多处引用但无 Wiki 页面
```

#### 信号检测流程

```
SignalDetector.run(since: date)
  ├─ _detect_undocumented_topics()   # 比较飞书群关键词频率 vs SOURCE 文档密度
  ├─ _detect_stale_wiki_active()     # Wiki 更新时间 vs 相关文档流时间
  ├─ _detect_kr_evidence_gap()       # 遍历 KR，检查最近 14 天相关文档数
  ├─ _detect_decision_reviews()      # 读决策库，找 review_at 临近的条目
  └─ SignalRanker.rank()             # 按紧迫度+影响面排序，最多输出 5 条
```

#### 输出格式（追加到 daily-start 末尾）

```
━━━ 今日信号摘要 (3 条) ━━━

⚠️  [KR停滞] KR2.3「AI巡检召回率」已 18 天无新增支撑文档
    → 最近相关文档：08-31 《巡检模型v2评测》
    → 建议：检查该 KR 当前推进状态

💡 [未文档化话题] 「差异化定价」近 7 天在 3 个群聊高频出现，但 SOURCE 无对应文档
    → 群聊来源：质检策略群、商品智能群
    → 建议：会议纪要或讨论文档补录

📋 [决策待复盘] DEC-20260831-0002「采用规则+模型双路融合方案」review_at = 2026-09-25
    → 距今 5 天，建议安排复盘

━━━
```

#### 飞书话题检测实现

飞书话题检测依赖现有 `feed` 模块的关键词提取能力：
- 从 `data/feed/` 缓存读取近 7 天的群聊摘要
- 提取高频实体词（TF-IDF 或 Aho-Corasick），与 SOURCE 文档标题做模糊匹配
- 未命中 SOURCE 的高频词列为「未文档化话题」候选

#### 与现有模块的集成点

| 集成位置 | 改动内容 |
|---|---|
| `src/iris/signals/` | 新建模块 |
| `app/cli/handlers.py` | `daily-start` handler 最后调用 `SignalDetector` |
| `feed/` | 复用群聊关键词数据 |
| `decisions/` | 读取决策库查 review_at（M2 可用后才激活此信号） |

---

## M3：OKR 证据持续累积

### 背景与问题

`iris-okr-check` 每次双周运行时从头全量检索，耗时长且依赖当次检索质量。如果每日增量打标，双周检查变为「审核积累」而非「从头检索」。

### 核心设计

每日 `daily-start` 对新增文档打 KR 标签，维护 per-KR 证据日志。`iris-okr-check` 优先读日志，仅对日志覆盖不足的 KR 补充检索。

#### 数据结构

```
data/okr_evidence/
  ├── index.json          # { kr_id: { last_updated, doc_count, ... } }
  └── <kr_id>.jsonl       # 每行一条证据记录
```

证据记录格式：
```json
{
  "kr_id": "KR2.3",
  "kr_text": "AI巡检召回率达到80%",
  "doc_path": "06-我的周报/2026-09-14-周报.md",
  "doc_date": "2026-09-14",
  "relevance_score": 0.82,
  "evidence_snippet": "本周完成巡检模型v2评测，召回率从71%提升到76%...",
  "tagged_at": "2026-09-15T08:30:00"
}
```

#### 增量打标流程

```
daily-start → OKREvidenceTagger.run_incremental()
  ├─ 读取上次打标时间 index.json
  ├─ 扫描 SOURCE 中新增/修改的文档（mtime > last_tagged）
  ├─ 对每篇新文档：
  │   └─ 向量相似度 vs 所有 KR 向量 → 阈值筛选（>0.65）→ 写入 .jsonl
  └─ 更新 index.json 中的 last_updated
```

#### KR 向量构建

```python
# 在 iris-okr-check 或 daily-start 初始化时执行
class KRVectorIndex:
    def build(self, okr_data: dict) -> None:
        """从飞书 OKR 数据构建 KR 语义向量，缓存到 data/okr_evidence/kr_vectors.npz"""
        for kr_id, kr_text in self._extract_krs(okr_data):
            # 用 embedding 模型编码，与 retrieval 模块共用 EmbeddingService
            ...
```

#### 对 iris-okr-check 的改造

```python
# 原流程：全量检索
# 新流程：读日志 + 补充检索

def check_kr(kr_id: str, kr_text: str) -> KRCheckResult:
    # 1. 从日志读取已积累证据
    evidence = OKREvidenceStore.get(kr_id, since=days_ago(14))

    # 2. 若证据不足（< 3 条），补充向量检索
    if len(evidence) < 3:
        extra = retriever.search(kr_text, top_k=5)
        evidence.extend(extra)

    # 3. LLM 综合写小结
    return synthesize(kr_text, evidence)
```

---

## M5：知识质量主动发现

### 背景与问题

`wiki-lint` 需要手动触发，`stale` 检测基于写入时间而非文档流活跃度，导致「文档流活跃但 Wiki 不更新」的脱节问题无法自动发现。

### 核心设计

为每个 Wiki 页面维护「文档流鲜度分」，`daily-start` 产出需要更新的 Wiki 页面清单。

#### 鲜度评分模型

```python
def compute_freshness_score(wiki_page: WikiPage, source_docs: list[SourceDoc]) -> float:
    """
    鲜度分 = 文档流活跃度 - Wiki 更新滞后惩罚

    取值 0-1：
    - >0.8  正常，Wiki 跟上了文档流
    - 0.5-0.8  轻微滞后，低优先级关注
    - <0.5  显著滞后，建议更新
    """
    # 相关文档中最新的日期
    latest_source_date = max(d.date for d in source_docs) if source_docs else None
    wiki_update_date = wiki_page.frontmatter.get("updated_at")

    if not latest_source_date:
        return 1.0  # 无相关文档，不视为滞后

    lag_days = (latest_source_date - wiki_update_date).days if wiki_update_date else 999
    activity_score = len([d for d in source_docs if d.days_ago < 30]) / 5  # 近 30 天文档数，归一化

    freshness = max(0.0, 1.0 - lag_days / 60) * min(1.0, activity_score)
    return freshness
```

#### 每日检测流程

```
daily-start → WikiFreshnessChecker.run()
  ├─ 遍历所有 Wiki 页面（取 frontmatter updated_at）
  ├─ 对每页：向量检索最相关的 SOURCE 文档（top 10）
  ├─ compute_freshness_score()
  ├─ 找出分数 < 0.5 的页面，限制最多 5 条（按分数升序）
  └─ 追加到 daily-start 输出（或并入 M1 信号摘要的 STALE_WIKI_ACTIVE 信号）
```

#### 与 M1 的关系

M5 的「Wiki 脱节检测」对应 M1 信号类型 `STALE_WIKI_ACTIVE`。两个模块可独立实现，`daily-start` 集成时 M1 的信号输出包含 M5 的结果。

---

## 三阶段实施规划

### 阶段一：基础设施（P0 核心）

**目标**：M2 决策追踪库 + M4 会前情报包可独立运行。

**时长**：约 3-4 周

**里程碑**：
1. `iris decisions` 命令组全功能（list/search/show/add/update）
2. `transcribe-meeting` 自动提取决策并写入决策库
3. `iris briefing` 输出完整情报包（话题+时间范围，不含参与人动态）
4. 决策库读写有完整测试覆盖（≥ 80%）

**实现顺序**：

```
Week 1：
  - decisions/schema.py + decisions/store.py（读写、原子写、FileLock）
  - decisions/cli.py（list/search/show/add 命令）
  - 测试：20 项 schema + store 测试

Week 2：
  - decisions/extractor.py（LLM 提取，prompt 设计）
  - 集成 transcribe_meeting：纪要生成后触发提取
  - 测试：提取器 mock LLM 测试 10 项

Week 3：
  - briefing/topic_resolver.py + briefing/doc_retriever.py
  - briefing/synthesizer.py（单次 adv_model 调用）
  - briefing/cli.py（briefing 命令）

Week 4：
  - briefing/open_issue_detector.py
  - 端到端测试：给定话题，情报包完整性检查
  - docs 补充使用说明
```

**新增文件清单**：
```
src/iris/decisions/__init__.py
src/iris/decisions/schema.py
src/iris/decisions/store.py
src/iris/decisions/extractor.py
src/iris/decisions/cli.py
src/iris/briefing/__init__.py
src/iris/briefing/topic_resolver.py
src/iris/briefing/doc_retriever.py
src/iris/briefing/open_issue_detector.py
src/iris/briefing/synthesizer.py
src/iris/briefing/cli.py
tests/test_decisions_store.py
tests/test_decisions_extractor.py
tests/test_briefing_synthesizer.py
```

---

### 阶段二：集成与扩展（P0 完善 + P1）

**目标**：决策纳入知识图谱；情报包含参与人动态；M1 信号推送上线。

**时长**：约 3 周

**里程碑**：
1. `build-graph` 包含决策节点和边
2. `iris briefing --participants` 产出参与人近期动态章节
3. `daily-start` 末尾输出「今日信号摘要」（3-5 条信号）
4. `iris decisions report` 可导出季度决策报告

**实现顺序**：

```
Week 5：
  - 知识图谱集成：graph/decision_nodes.py（决策节点 builder）
  - build-graph 加入决策节点和边（owns_decision / relates_to）
  - briefing/person_retriever.py（读人物页近期记录）

Week 6：
  - signals/__init__.py + signals/detector.py（信号基础框架）
  - signals/detectors/kr_gap.py
  - signals/detectors/undocumented_topic.py（依赖 feed 缓存）

Week 7：
  - signals/detectors/stale_wiki.py（M5 的信号版）
  - signals/detectors/decision_review.py（依赖 M2）
  - signals/ranker.py（紧迫度排序）
  - daily-start handler 集成 SignalDetector
  - decisions/reporter.py（Markdown 报告导出）
```

---

### 阶段三：持续积累与智能化（P2）

**目标**：M3 OKR 证据日志 + M5 完整知识鲜度评分；整体形成闭环。

**时长**：约 3 周

**里程碑**：
1. `daily-start` 增量打标新文档到 OKR 证据日志
2. `iris-okr-check` 读日志，双周检查提速 50%+
3. Wiki 鲜度分完整实现，daily-start 输出需更新清单
4. 全量测试覆盖率保持 ≥ 70%

**实现顺序**：

```
Week 8：
  - okr_evidence/__init__.py + okr_evidence/store.py
  - okr_evidence/kr_vector_index.py（KR 向量构建与缓存）
  - okr_evidence/tagger.py（增量打标逻辑）

Week 9：
  - daily-start 集成 OKREvidenceTagger（增量，每日）
  - iris-okr-check 改造：读日志优先，补充检索兜底
  - 测试：打标准确率基线（与全量检索结果对比）

Week 10：
  - wiki/freshness.py（鲜度评分模型）
  - WikiFreshnessChecker 集成 daily-start
  - 回归测试修复 + 覆盖率补全
  - docs 更新：所有新模块 usage 文档
```

---

## 开发约定补充

以下约定适用于本次所有新模块，与 iris3 现行约定一致：

1. **异常**：新增异常继承 `IrisRuntimeError` 或 `IrisValueError`，不得直接 raise `Exception`。
2. **持久化**：所有写操作使用 `atomic_write_json`，多文件制品用 generation 目录原子切换。
3. **锁**：`data/decisions/`、`data/okr_evidence/` 目录的读改写操作加 `FileLock`。
4. **LLM 调用**：提取类任务用 `base_model`，综合生成类用 `adv_model`，遵循现有路由规则扩充（在 `config/llm_routes.json` 补充 `decision_extraction` / `briefing_synthesis` 规则）。
5. **复杂度**：新函数 ruff C901 门禁 `max-complexity = 20`，超限拆分。
6. **测试**：每个新模块至少 10 项基础测试；提取器/合成器 mock LLM 调用。
7. **CLI 注册**：在 `app/cli/handlers.py` 的 facade 层注册，保持命令平铺风格（`iris decisions list`）。

---

## 依赖关系图

```
M2 (决策追踪库)
  └─ 被 M4 依赖（历史决策章节）
  └─ 被 M1 依赖（决策复盘信号）

M4 (会前情报包)
  └─ 依赖 M2（阶段一可降级为空）
  └─ 依赖现有 retrieval 模块
  └─ 依赖现有 wiki 模块

M1 (主动情报推送)
  └─ 依赖 M2（部分信号）
  └─ 依赖 M5（Wiki 鲜度信号）
  └─ 依赖现有 feed 模块

M3 (OKR 证据累积)
  └─ 依赖现有 retrieval/embedding 模块
  └─ 依赖飞书 OKR 数据（lark-okr）

M5 (知识质量发现)
  └─ 依赖现有 retrieval 模块
  └─ 被 M1 包含（信号来源之一）
```

---

## 典型使用场景

### 场景一：周一早晨，10 分钟定优先级

`iris daily-start` 跑完后，末尾多出一节：

```
━━━ 今日信号摘要 (4 条) ━━━

⚠️  [KR停滞] KR2.3「AI巡检召回率」已 16 天无新增支撑文档
    → 最近相关文档：09-04 《巡检模型v2评测》

💡 [未文档化话题] 「差异化定价策略」近 5 天在商品智能群高频出现，SOURCE 无对应文档
    → 建议：本周安排一次讨论并录入

📋 [决策待复盘] DEC-20260831-0002「采用规则+模型双路融合」review_at = 09-25（5 天后）

🔄 [Wiki 脱节] 项目-搜推质检v2 页面 42 天未更新，但近 2 周有 7 篇相关文档
━━━
```

扫一眼 4 条信号：KR 停滞是真问题，立刻在飞书给负责人发消息问进展；未文档化话题先记下来，周三团队会上加一个议题；决策复盘已有提醒，排进日历。整个过程不超过 10 分钟，没有遗漏。

---

### 场景二：会议前 2 分钟备战

下午 3 点有一个「Q4 质检策略对齐」，与会人李嘉晨、王芳、张迪。2:58 跑：

```bash
iris briefing --topic "Q4质检策略" --participants "李嘉晨,王芳,张迪" --days 60
```

90 秒后输出一份情报包：

- **核心背景**：项目-搜推质检 Wiki 的当前状态摘要
- **近期进展**：最近 60 天 6 篇相关会议纪要的关键结论，带日期
- **待解决问题**：上两次会上留下的 3 个 open 问题（规则库维护权责、误报率基线怎么定、和商品团队的接口协议）
- **历史决策**：该话题下 4 条已有决策，含当前状态
- **参与人动态**：李嘉晨近 2 周在这个方向上的工作记录；王芳的相关周报片段
- **推荐讨论点**：3 个建议在会上明确的问题

进会议室时已经知道上下文，不用再花 5 分钟「热身」回忆，直接问上次遗留的问题。

---

### 场景三：会后，决策自动沉淀

会开完了，`iris transcribe-meeting` 生成纪要。底部自动追加：

```
── 本次会议提取到 2 条决策 ──

DEC-20260920-0011
标题：Q4 质检方案采用「规则先行、模型兜底」双路架构
结论：规则库覆盖已知缺陷类型，模型补充规则盲区，不做全量模型替换
执行人：李嘉晨
review_at：2026-11-01
关联 KR：KR2.1, KR2.3

DEC-20260920-0012
标题：误报率基线定为 ≤8%（相对当前 -30%）
结论：高于 8% 时触发人工复核，不自动上线
执行人：王芳
关联 KR：KR2.2
────────────────────────────────
```

这两条自动写入决策库，纳入知识图谱，下次 `iris briefing` 时出现在「历史决策」章节。不需要手动整理，也不会在三个月后问「这个方案是什么时候定的、为什么这么定的」时找不到答案。

---

### 场景四：双周 OKR 检查，从「找」变「审」

跑 `iris-okr-check`。KR2.3 的证据日志里已经积累了 11 条打标记录，时间跨度横贯过去 30 天，每条带相关度分数和摘要。`iris-okr-check` 直接读日志，LLM 审核综合，比从头检索快约一半。产出的检查记录里，KR2.3 那条因为日志里有「召回率 76%」「v3 模型上线灰度」等具体证据，小结比以前精准得多。

花在「找信息」上的时间接近零，花在「判断和决策」上的时间没变。

---

### 场景五：季度复盘，历史轨迹一键拉出

```bash
iris decisions report --since 2026-07-01 --kr KR2 --output q3-decisions.md
```

输出一份按时间轴排列的决策记录，17 条，每条有背景、结论、执行人、当前状态（implemented / revised / open）。一眼看出：7 月的两个架构决策已 implemented，9 月的「误报率基线」还是 open，还有一条 8 月的决策后来被 9 月的会推翻了（状态 revised，关联了新的决策）。这份文档成为复盘 PPT 的骨架，也是下一季度方向讨论的起点。

---

### 整体体验变化

不再是每次手动去问 iris，而是 iris 知道什么信号值得关注、知道你开什么会需要什么上下文、知道每个决策后来怎么了。与 iris 的关系从「搜索引擎」变成了「知道你工作全貌的同事」。

---

*文档签名：Iris Intelligence Upgrade Design v1.0 · 2026-09-20*
