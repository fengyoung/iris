# Iris 智能升级设计文档

> **目标**：将 iris3 从「问了才答」升级为「主动织网」的情报系统。
> **范围**：5 个新能力模块，按优先级分三阶段实现。
> **状态**：已补充需求澄清与决策分级入库方案，待实现。编写日期：2026-09-20；更新日期：2026-09-21。

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
    decision_id: str          # 正式入库时生成，格式 DEC-YYYYMMDD-NNNN，按本地入库日期每日递增
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
    sources: list[DecisionSource]  # 多来源：SOURCE 相对路径、原文引用、位置及字段证据
    review_status: str        # auto_approved | approved（待审核候选另存）
    review_details: dict      # 程序校验、模型复核、不确定项及审核记录
    change_history: list[dict]  # 字段变更与修订历史
    related_krs: list[str]    # 关联的 OKR KR 标识
    related_decisions: list[str]  # 关联的其他决策 ID
    tags: list[str]           # 自由标签
    created_at: datetime
    updated_at: datetime
```

#### 存储方案

- 索引文件：`data/decisions/index.json`（正式决策的结构化列表，包含 schema 版本与每日编号计数）
- 单决策文件：`data/decisions/<decision_id>.json`（完整字段）
- 待审核候选：使用独立 `candidate_id`，与正式决策隔离存储；批准后才分配 DEC 编号。
- 索引、单决策、编号计数等关联数据通过 generation 目录原子发布，整个读改写过程置于同一 `FileLock` 临界区。
- Wiki 页面（可选）：`LLM-WIKI/05-决策/决策-<title>.md`（新增 Wiki 类型）

#### 提取流程

`transcribe-meeting` 在生成纪要后，调用 `DecisionExtractor` 从纪要文本提取决策点：

```
纪要文本
  → 按来源文件名/标题进行敏感文档过滤
  → DecisionExtractor._extract_candidates()   # base_model 识别候选
  → DecisionExtractor._structure_decision()   # 结构化字段 + 原文位置与证据
  → DecisionExtractor._resolve_persons()      # 人物归一化，不确定则保留待确认
  → 程序校验                                 # 原文、字段、日期、重复与冲突
  → 独立 adv_model 复核                      # 同篇纪要候选批量复核
  → 分级：高置信度自动入库 / 有歧义进入待审核区 / 非决策丢弃
  → DecisionStore                            # 多来源合并或正式入库，原子发布
```

提取 prompt 关键点：
- 识别信号词：「决定」「确认」「同意」「不做」「暂缓」「方案选 X」「采用」「放弃」
- 排除：议题讨论（未有结论）、行动项（已有 todos）、纯信息同步
- 一条会议纪要可提取 0-N 条决策

#### 已确认的提取与分级入库规则（2026-09-21）

**失败处理**：决策提取失败后重试一次，两次都失败则跳过本次提取，记录失败原因，不阻断纪要生成。独立复核失败不得视为通过，候选保留待审核；不得因缺少复核而自动入库。

**证据要求**：每条候选必须携带原文引用、引用位置，以及支持结论、负责人和决策日期的字段证据。程序必须确认引用确实存在于原文。负责人、复盘日期、关联 KR 等可选字段没有依据时留空，不允许猜测；“下个月考虑评估”不能生成确定的 `review_at`。必填信息缺失或人物归一化有歧义时转待审核，不补造事实。

**两层校验**：先以程序检查敏感来源、原文引用、必填字段、日期、状态、重复与冲突；再通过独立模型调用对照原文，核对是否已拍板、否定词/范围/前提是否保留，以及是否存在尚未解决的矛盾。初期采用 `base_model` 提取、`adv_model` 复核，同篇纪要候选批量复核以控制成本。不得仅依赖提取模型自报的置信度；两个模型意见一致也不替代原文校验与抽查。

| 分级 | 判据 | 处理 |
|---|---|---|
| 高置信度 | 明确拍板、原文可核验、程序校验和独立复核通过、无未解决冲突 | 自动进入正式决策库，标记 `auto_approved` |
| 待确认 | 有决策价值，但结论、条件、上下文或必填信息存在歧义 | 进入待审核区，人工确认或修改后入库 |
| 非决策 | 普通讨论、提议、纯行动项或缺乏原文支撑 | 不进入正式库或人工审核队列 |

审核状态与执行状态分离：审核状态为 `pending / auto_approved / approved / rejected`；执行状态仍为 `open / implemented / revised / cancelled`。候选使用独立编号，只有正式入库才在锁内分配 `DEC-YYYYMMDD-NNNN`，按本地入库日期每日递增。待审核候选不作为已确认事实进入问答、图谱、Wiki 决策页或情报包。

**去重与修订**：
- 相同决策合并为一条，追加不同来源及各自原文证据；重复处理同一来源不得重复追加。
- 补充信息只有在有明确依据时才更新字段，并记录变更历史。
- 改变原结论的决策保留为新记录，关联旧决策；只有明确的取代证据才可自动将旧记录标记为 `revised`，否则进入待确认。

**人工审核**：首版提供 CLI 的待审核列表、批准和拒绝入口；允许人工修改候选后批准。终端与每日信号文件展示待审核数量，发给冯扬的飞书摘要附候选结论、原文证据及待确认原因。飞书按钮审核作为后续扩展，不纳入首版必需范围。

**准确率验收**：整理包含明确拍板、暂缓、否定、条件决策、意见冲突的真实纪要验证集。初期采用严格自动入库条件并抽查结果，以自动入库准确率达到 95% 以上为初始验收目标，同时记录自动入库比例、待审核数量与误判类型，再按实测决定是否放宽条件。

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

# 人工审核（候选可先修改再批准）
iris decisions pending
iris decisions approve <candidate_id>
iris decisions reject <candidate_id> --reason "仍在讨论"

# 导出为 Markdown 报告
iris decisions report [--since 2026-07-01] [--kr KR2] [--output decisions-q3.md]
```

#### 知识图谱集成

在 `build-graph` 时，将决策节点纳入图谱：
- 节点类型：`decision`
- 边类型：`owns_decision`（人物→决策）、`relates_to`（决策→项目/概念）、`resolves`（决策→问题）
- `graph-query --op neighbors` 支持决策节点

#### 与现有模块的集成点

| 集成位置 | 改动内容 |
|---|---|
| `transcribe_meeting/` | 生成纪要后调用 `DecisionExtractor`，正式入库结果与待审核提示分开展示在纪要尾部 |
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
    DECISION_REVIEW_DUE   # 0 <= (review_at - 本地今天).days <= 7，包含当天
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

#### 信号交付（已确认）

每次生成的摘要同时输出到终端、写入本地文件，并通过飞书由 **Iris → 冯扬** 发送。首版不是仅终端展示；文件保存到 `data/signals/`，使用本地时间命名并原子写入。摘要包含信号、来源、建议动作及决策待审核信息。

实现时解析并配置冯扬的飞书收件人标识，使用 Iris 身份发送；用户已授权这一通知方向。记录投递状态，避免对同一份摘要重复发送；发送失败保留本地结果并明确告知，不影响 daily-start 已完成的维护工作。不得将敏感来源内容带入通知。

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
  │   └─ 向量相似度 vs 所有 KR 向量 → 阈值筛选（> 配置值，默认 0.65）→ 写入 .jsonl
  └─ 更新 index.json 中的 last_updated
```

证据相似度阈值纳入配置，默认 `0.65`，校验为 0–1 范围；遵循现有配置加载约定，并在配置示例和使用文档中说明。

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
2. **持久化**：JSON 使用 `atomic_write_json`，Markdown/JSONL 等使用对应的 `atomic_write_text/bytes`；索引与明细等多文件制品用 generation 目录原子切换，避免仅单文件原子写导致整体不一致。
3. **锁**：`data/decisions/`、`data/okr_evidence/` 目录的读改写操作加 `FileLock`。
4. **LLM 调用**：提取类任务用 `base_model`，综合生成类用 `adv_model`，遵循现有路由规则扩充（在 `config/llm_routes.json` 补充 `decision_extraction` / `briefing_synthesis` 规则，并增加独立决策复核路由 `decision_review`）。
5. **复杂度**：新函数 ruff C901 门禁 `max-complexity = 20`，超限拆分。
6. **测试**：每个新模块至少 10 项基础测试；提取器/合成器 mock LLM 调用。
7. **CLI 注册**：在 `app/cli/handlers.py` 的 facade 层注册，保持命令平铺风格（`iris decisions list`）。
8. **敏感文档**：所有新消费者继承 `src/iris/wiki/_sensitive.py` 的唯一判定规则，仅匹配文件名/标题，绝不匹配正文。提取前过滤，并确保决策库、审核候选、情报包、OKR 证据、质量信号和飞书通知均不产生敏感文档的下游衍生内容。
9. **本地时间**：日期窗口、编号、复盘提醒、增量游标与输出日期统一采用本地时间；持久化时间戳携带时区偏移，不混用无时区时间与 UTC。复盘提醒范围明确为 `0 <= (review_at - 今天).days <= 7`。
10. **版本迁移**：决策、信号、OKR 证据等持久化结构显式携带 `schema_version`；JSONL/向量缓存版本由对应清单管理。提供逐版本迁移机制，迁移前备份，在锁内生成并校验新 generation 后原子发布，失败保留旧版本并支持恢复。重复执行迁移应安全；遇到未知高版本拒绝写入，避免覆盖。版本迁移必须覆盖索引、明细与关联关系，并补充旧版升级、重复执行、失败恢复和未知版本测试。
11. **长任务与预算**：评估接入 `TaskReporter`，记录提取、复核、检索、合成和发送等阶段；对批量 LLM/Embedding 调用设置预算，避免每日维护成本失控。

### 澄清后的实施清单补充

- **阶段一**：加入多来源数据结构、按天编号、重复处理幂等性、敏感来源过滤、提取重试、独立复核、分级入库、待审核 CLI、变更历史和决策存储迁移；增加真实纪要验证集及自动入库准确率验收。情报包只读取正式决策。
- **阶段二**：每日摘要完成终端、文件、飞书三路交付，配置 Iris 身份与冯扬收件人，记录发送状态并防止重复投递；汇总待审核决策。图谱仅纳入正式决策。
- **阶段三**：OKR 证据阈值配置化（默认 0.65），实现证据索引/日志/向量缓存的版本兼容与迁移测试。
- **贯穿全部阶段**：统一本地时间及边界测试，验证敏感来源不进入任何新制品；补充并发编号、去重追加来源、修订冲突、复核失败、候选隔离、迁移恢复与发送失败的行为测试。


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

*文档签名：Iris Intelligence Upgrade Design v1.1 · 2026-09-21*
