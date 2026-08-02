# 信息汇聚管道 `iris-feed` 技术设计

> 版本：v1.1 · 2026-07-27

---

## 一、概述

从飞书聊天记录（群聊 + 单聊）中自动挖掘有价值话题，匹配 OKR 体系，生成话题简报归档到知识库（SOURCE），并支持文档自动转换。

### 核心理念

**以话题为中心**——每次运行产出的不是"某一天的信息汇总"，而是 N 份**独立的话题简报**，每份聚焦一个话题，同一话题的后续更新产生新版本文件。

---

## 二、架构总览

```
┌────────────────────────────────────────────────┐
│                   CLI 层                        │
│  feed-collect  feed-pending  feed-confirm       │
│  feed-ignore   feed-history                     │
└──────────────────┬─────────────────────────────┘
                   │
┌──────────────────▼─────────────────────────────┐
│              Pipeline 编排层                     │
│           feed_pipeline.py                      │
│   Step 0~Step 8 顺序编排 + 错误处理 + 日志       │
└──────────────────┬─────────────────────────────┘
                   │
┌──────────────────▼─────────────────────────────┐
│              模块层（6 子模块）                   │
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Chat     │→│ Message  │→│ Topic    │       │
│  │ Fetcher  │  │ Filter   │  │ Detector │       │
│  └──────────┘  └──────────┘  └────┬─────┘       │
│                                    │              │
│  ┌──────────┐  ┌──────────┐  ┌────▼─────┐       │
│  │ OKR      │←│ Brief    │←│ Topic    │        │
│  │ Loader   │  │ Generator│  │ Detector │        │
│  └──────────┘  └──────────┘  └──────────┘       │
│                                                   │
│  ┌──────────┐  ┌──────────┐                      │
│  │Dispatcher│  │Cursor    │                      │
│  │          │  │Tracker   │                      │
│  └──────────┘  └──────────┘                      │
└──────────────────┬─────────────────────────────┘
                   │
┌──────────────────▼─────────────────────────────┐
│              数据层                               │
│  config/feeds.json  data/feed_cursors.json       │
│  SOURCE/09-工作简报/YYYYMM/                      │
└────────────────────────────────────────────────┘
```

---

## 三、配置体系

### 3.1 配置层级与文件

```
config/
  ├── feeds.json              # 运行时配置（gitignored，含聊天 ID 等敏感信息）
  └── feeds.json.example      # 示例配置（版本控制，填示例值，不放真实 ID）
```

### 3.2 配置全貌

```json
{
  "version": 1,
  "watch_chats": [
    {
      "id": "oc_xxxxxxxxxxxxxxxxxxxxx",
      "name": "部门交流群",
      "type": "group",
      "mode": "auto_import",
      "okr_tags": ["智能巡检", "推荐体验"]
    }
  ],
  "watch_singles": [
    {
      "id": "ou_xxxxxxxxxxxxxxxxxxxxx",
      "name": "张三-王五",
      "mode": "confirm",
      "okr_tags": null
    }
  ],
  "topic_config": {
    "default_range_days": 3,
    "min_msg_length": 10,
    "topic_min_messages": 2,
    "max_topics_per_run": 30,
    "time_window_minutes": 30
  },
  "okr_mapping": {
    "enabled": true,
    "strict_match": false
  }
}
```

### 3.3 配置管理命令

用户不需要手动编辑 JSON，全部通过 CLI 管理：

```bash
# ── 首次配置 ──
iris feed-setup                    # 交互式向导，10 秒完成

# ── 日常管理 ──
iris feed-list                     # 查看当前配置列表
iris feed-add --chat "群名"        # 添加关注（自动解析群 ID）
iris feed-remove --chat "群名"     # 移除关注
iris feed-config --chat "群名"     # 修改某会话的配置
iris feed-config --show            # 查看完整配置

# ── 单聊管理（单独处理，更谨慎） ──
iris feed-add --chat "联系人" --type single --mode confirm
```

### 3.4 首次配置向导（`feed-setup`）

```
📋 信息汇聚 — 首次配置向导
====================================

Step 1/4：发现可关注的群聊
正在拉取你可用的群聊列表...

  [1] 部门交流群       (成员 N)
  [2] 项目协作群     (成员 N)
  [3] AI 火花组          (成员 N)
  [4] 研发项目组         (成员 N)
  ...

选择要关注的群聊（可多选，逗号分隔）: 1,2,4

Step 2/4：配置每个群聊的导入模式
  部门交流群 → 模式 (auto/confirm) [auto]:
  项目协作群 → 模式 (auto/confirm) [auto]: confirm
  研发项目组 → 模式 (auto/confirm) [auto]:

Step 3/4：关联 OKR 标签（可选）
  部门交流群 → OKR 标签（逗号分隔，留空跳过）: 智能巡检,推荐体验
  项目协作群 → OKR 标签:
  研发项目组 → OKR 标签: 测试研发

Step 4/4：配置话题检测参数
  默认时间范围（天）[3]:
  话题最少消息数 [2]:
  单次最多话题数 [30]:

✅ 配置已保存到 config/feeds.json
```

**关键设计**：用户在向导中只需输入群名，系统通过飞书 API 反查群 ID（`lark-im` 的 `search_chat`）。不需要用户手动查 ID。

### 3.5 配置管理后端模块

在 `feed_config.py` 中扩展，增加配置管理能力：

```python
class FeedConfigManager:
    """配置的管理器，处理增删改查 + 交互式向导"""

    def __init__(self, config_path: Path, feishu_bridge: FeishuBridge):
        ...

    def load(self) -> FeedConfig:
        """加载配置"""

    def save(self, config: FeedConfig):
        """保存配置"""

    def list_chats(self) -> list[WatchChat]:
        """列出关注会话"""

    def add_chat(self, name: str, chat_type: str, mode: str, okr_tags: list[str] | None):
        """添加关注会话（自动通过飞书 API 解析 name → ID）"""

    def remove_chat(self, chat_id_or_name: str):
        """移除关注会话"""

    def update_chat(self, chat_id: str, **kwargs):
        """更新某会话配置"""

    # ── 交互式向导 ──
    def interactive_setup(self) -> FeedConfig:
        """首次配置交互式向导"""

    def _discover_available_groups(self) -> list[ChatInfo]:
        """调用飞书 API 发现用户可用的群聊"""
```

---

## 四、模块详细设计

### 4.1 模块目录结构

```
src/iris/feed/                      # 新模块
├── __init__.py                     # 导出主要类
├── feed_pipeline.py                # Pipeline 编排（主入口）
├── feed_config.py                  # 配置加载
├── _chat_fetcher.py                # 飞书消息获取
├── _message_filter.py              # 消息噪音过滤
├── _topic_detector.py              # 话题检测 + 跨群聚合（含 OKR 匹配注入）
├── _okr_loader.py                  # 从 SOURCE/01-目标管理 加载 OKR（v1.1 新增）
├── _brief_generator.py             # 话题简报生成（含 OKR 关联章节）
├── _dispatcher.py                  # 分发（auto/confirm）
├── _cursor_tracker.py              # 游标追踪（增量）
├── _feishu_bridge.py               # 飞书接口桥接层
└── _types.py                       # 类型定义
```

### 4.2 ChatFetcher — 飞书消息获取

**文件**：`_chat_fetcher.py`

**职责**：
- 遍历 `watch_chats` 列表
- 对每个会话，通过飞书 API 拉取指定时间范围的消息
- 记录游标，支持增量

**增量策略**：

```
每次执行：
1. 读取 data/feed_cursors.json
2. 对每个会话：
   a. 有 last_cursor → 从游标之后拉取
   b. 无 last_cursor → 拉取最近 N 天（默认 3 天）
3. 执行完成后更新游标
```

**依赖**：`lark-im` skill（`search_message` / `list_message` API）

**关键接口**：

```python
class ChatFetcher:
    def __init__(self, cursor_tracker: CursorTracker):
        ...
    
    def fetch(
        self,
        chats: list[WatchChat],
        since: datetime | None,
        until: datetime | None,
    ) -> dict[str, list[RawMessage]]:
        """返回 {chat_id: [messages]}"""
```

### 4.3 MessageFilter — 消息噪音过滤

**文件**：`_message_filter.py`

**过滤策略**：

| 规则 | 示例/说明 |
|------|----------|
| 长度过滤 | 消息纯文本 < 10 字（且不含文档/图片/链接） |
| 红包消息 | 包含「红包」、「已领取」等关键词 |
| 接龙/打卡 | 群接龙、打卡提醒、签到 |
| 纯表情/贴图 | 仅有 emoji 或贴图 |
| 纯系统消息 | 「xxx 加入了群聊」、「xxx 修改了群名」 |
| 重复内容 | 与已导入简报内容高度重复（用 SimHash 或简单编辑距离） |
| 纯转发链接无评论 | 仅发了一个链接没有自己的评论 |

**关键接口**：

```python
class MessageFilter:
    def filter(
        self,
        messages: dict[str, list[RawMessage]],
    ) -> dict[str, list[RawMessage]]:
        """返回过滤后的 {chat_id: [messages]}"""
    
    def is_noise(self, msg: RawMessage) -> bool: ...
```

### 4.4 TopicDetector — 话题检测 + 跨群聚合

**文件**：`_topic_detector.py`

**两步法**：

```
原始消息流
    │
    ▼
Step 1：规则分割（免费，批量）
  ├── 同群内：同话题消息间隔 < 30min 归为一组
  ├── 按关键词/参与者/OKR 标记分组
  └── 产出：候选话题组（含单个群内的片段）
    │
    ▼
Step 2：LLM 聚合（按需，控制 token）
  ├── 跨群的候选组是否同一话题 → 合并
  ├── 与历史话题（已归档的简报）对比 → 新/更新/重复
  ├── 生成话题标题 + 核心摘要
  └── 产出：最终话题列表
```

**LLM 调用设计**（v1.1 新增：OKR 上下文注入）：

```
Prompt 设计要点：
- 输入：多组候选话题消息（含来源群名、发言人、时间）+ 当前 OKR 目标
- 输出：JSON 格式的话题列表（含 OKR 匹配结果）
  [
    {
      "title": "...",
      "messages": [msg_id, ...],
      "summary": "...",
      "cross_chat": true/false,
      "okr_match": {
        "kr_id": "O2-KR3",
        "match_strength": "strong",
        "reason": "讨论内容与...直接相关"
      }
    }
  ]
- 限制：每批最多 10 组候选话题（避免过长的上下文）
```

**历史话题匹配**：

扫描 `SOURCE/09-工作简报/` 下已有话题简报的 frontmatter，提取 `topic_id` + `title`，与当前候选话题做语义匹配判断是"新话题"还是"更新版"。

**关键接口**：

```python
@dataclass
class TopicCandidate:
    topic_id: str                # feed-YYYYMMDD-NNN
    title: str
    summary: str
    messages: list[RawMessage]   # 包含的所有消息
    source_chats: list[str]      # 来源群/聊
    is_update: bool              # 是否对已有话题的更新
    previous_version: str | None # 关联的旧文件名
    okr_tags: list[str]          # OKR 匹配结果（由 OKRMatcher 填充）

@dataclass  
class DetectedTopic:
    topic_id: str
    title: str
    summary: str
    messages: list[RawMessage]
    source_chats: list[SourceRef]
    is_update: bool
    previous_version: str | None
    okr_tags: list[str]
    okr_match_strength: Literal["strong", "weak", "none"]  # v1.1 新增

class TopicDetector:
    def detect(
        self,
        filtered_messages: dict[str, list[RawMessage]],
        llm_service: LLMService,
    ) -> list[DetectedTopic]: ...
```

### 4.5 OKRLoader — OKR 加载与匹配（v1.1 更新）

> 实际实现将 OKR 匹配合并到话题检测阶段，不再单独设 OKRMatcher 模块。

**文件**：`_okr_loader.py`（v1.1 新增）

**设计变更**：原始设计（v1.0）将 OKR 匹配分为独立模块 + 两轮策略（关键词粗筛 + LLM 精判）。实际实现改为：

1. **OKRLoader** 纯加载层：从 `SOURCE/01-目标管理/<年份>/` 加载最新的 OKR Markdown 文件
2. **LLM 一体检测**：在话题检测 Prompt 中注入 OKR 上下文，LLM 一次完成「话题发现 + OKR 匹配」
3. **语义化展示**：`feed-list` 命令展示 OKR 标签时自动解析为实际描述

**变更理由**：
- 减少一次 LLM 调用（topic detection 和 OKR matching 共享同一上下文）
- OKR 上下文让 LLM 更精准判断话题价值
- 本地文件加载零 API 成本

**OKR 加载逻辑**：

```python
# 文件扫描规则
# 1. 在 SOURCE/01-目标管理/<年份>/ 中搜索
# 2. 文件名含「数据部门」且不含 OP/双周/团队/个人/检查
# 3. 按文件名日期降序取最新

# 解析规则
# 1. 去掉 frontmatter
# 2. 提取 ## O<数字>：标题  → Objective
# 3. 提取 ### KR<数字>：标题  → KR（含 Owner 字段）
# 4. KR ID 拼接为 "O1-KR1" 格式
```

**话题检测 Prompt 注入**：

```
## 当前 OKR 目标
- O1：智能质检技术升级…
  - O1-KR1：【质量】影像3.0主观项检测…
  - O1-KR2：【扩展】多品类复用技术基座…

## 任务
6. **匹配 OKR**：判断话题内容与哪个 OKR/KR 相关，给出匹配强度

## 输出格式
"okr_match": {
  "kr_id": "O2-KR3",
  "match_strength": "strong",
  "reason": "讨论内容与..."
}
```

**关键接口**（实际实现）：

```python
@dataclass
class KR:
    kr_id: str          # "O1-KR1"
    title: str          # "【质量】影像3.0主观项检测…"
    short_title: str    # "【质量】影像3.0主观项检测"
    owner: str = ""
    content: str = ""   # 完整内容

@dataclass  
class Objective:
    obj_id: str         # "O1"
    title: str          # "智能质检技术升级…"
    krs: Dict[str, KR]

class OKRDocument:
    objectives: Dict[str, Objective]
    source_file: str
    
    def resolve_tags(self, tags: List[str]) -> Dict[str, str]:
        """将 ["O1-KR1"] 解析为 {"O1-KR1": "【质量】影像3.0主观项检测…"}"""
    
    def to_prompt_context(self) -> str:
        """格式化为 LLM Prompt 注入文本"""

class OKRLoader:
    def load(self) -> Optional[OKRDocument]:
        """加载最新 OKR 文档（带内存缓存）"""
    
    def resolve_tags(self, tags: List[str]) -> Dict[str, str]:
        """解析标签为实际描述"""
```

### 4.6 DocExtractor — 飞书文档提取与转换

**文件**：`_doc_extractor.py`

**职责**：
- 扫描消息中的飞书文档链接（[token 识别](https://xxx.feishu.cn/docx/xxx) / wiki 链接）
- 调用 `feishu-doc-convert` 管道转换为本地 Markdown
- 按内容路由归档到对应的 SOURCE 子目录

**链接识别**：

```python
# 正则匹配飞书文档链接
FEISHU_DOC_RE = re.compile(r'https?://[^/]*feishu[^/]*/(docx|wiki|sheet|base)/(\w+)')
```

**路由规则**（复用现有 `transcribe-meeting` 的规则）：

| 路由目标 | 判定条件 |
|---------|---------|
| `03-方案报告/` | 方案/设计/规范类文档 |
| `04-讨论思考/` | 讨论纪要/观点分享 |
| `08-参考资料/` | 外部资料/学习材料/评测报告 |

**关键接口**：

```python
class DocExtractor:
    def extract(
        self,
        messages: list[RawMessage],
    ) -> list[ConvertedDoc]:
        """返回转换后的本地文档列表"""
    
    def _route_doc(self, doc_content: str, doc_title: str) -> str:
        """判断 SOURCE 子目录"""
```

### 4.7 BriefGenerator — 话题简报生成

**文件**：`_brief_generator.py`

**产出**：每个话题一篇 Markdown 文件

**文件命名格式**：

```
YYYYMMDD-简报-{标题}（from{来源}）.md
```

命名规则：
- `YYYYMMDD`：本次执行日期（`until` 日期，未指定则为今天）
- `简报-`：固定前缀
- `{标题}`：话题标题（中文，去特殊字符）
- `（from{来源}）`：来源渠道，多个来源时简写为 `from飞书`

文件名生成函数：

```python
def _build_filename(topic: DetectedTopic, exec_date: str) -> str:
    title = _sanitize_filename(topic.title)
    source_tag = "from飞书"
    return f"{exec_date}-简报-{title}（{source_tag}）.md"
```

**若为话题更新版（`is_update=True`）**：
- 文件名中的日期改为**本次执行日期**
- 来源标注合并简写为 `from飞书`
- 原文件名记录在 frontmatter `previous_versions` 中

**文件内容模板**：

```markdown
---
type: discussion
date: {exec_date}
updated: {exec_date}
topic_id: {topic.topic_id}
status: {status}  # active / confirmed / ignored
okr_tags: {topic.okr_tags|to_json}
sources:
{for src in topic.source_chats}
  - type: {src.type}
    name: {src.name}
    msg_count: {src.msg_count}
{endfor}
documents:
{for doc in converted_docs}
  - path: {doc.relative_path}
{endfor}
previous_versions:
{for ver in topic.previous_versions}
  - {ver}
{endfor}
---

# {topic.title}

> **来源**：{source_description}
> **整理日期**：{today}
> **话题 ID**：{topic.topic_id}

---

## 话题概览

{topic.summary}

---

## 关键信息

### 当前状态

{key_status}

### 讨论要点

{discussion_points}

### 已明确的决策

{decisions}

---

## 相关文档

{doc_links}

---

## OKR 关联

{okr_section}

---

## 参与者

{participants_section}

---

## 原始消息精选

{quotes_section}

---

*生成时间：{generated_at} · 数据源：{source_summary}*
```

**关键接口**：

```python
class BriefGenerator:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir  # SOURCE/09-工作简报/YYYYMM/
    
    def generate(
        self,
        topics: list[DetectedTopic],
        converted_docs: list[ConvertedDoc],
        exec_date: str,
    ) -> list[Path]:
        """返回生成的文件路径列表"""
```

### 4.8 Dispatcher — 分发

**文件**：`_dispatcher.py`

**逻辑**：

```python
class Dispatcher:
    def dispatch(
        self,
        topics: list[DetectedTopic],
        brief_files: list[Path],
    ) -> DispatchResult:
        """
        mode=auto_import 的会话的话题 → 直接保存
        mode=confirm 的会话的话题 → 推送飞书卡片 + 暂存到待确认队列
        """
```

**待确认队列**：`data/feed_pending.json`

```json
[
  {
    "topic_id": "feed-20260724-001",
    "title": "智能巡检准召目标策略",
    "summary": "...",
    "sources": ["部门交流群", "张三-王五"],
    "brief_path": "...",
    "created": "2026-07-24T10:00:00",
    "status": "pending"
  }
]
```

### 飞书卡片推送

确认卡片由 **Iris bot 直接发送到你的飞书单聊**（通过 `--as bot --user-id "ou_xxxxxxxxxxxxxxxxxxxxxxxxx"`），API 自动建立 bot↔用户 P2P 通道。

```
┌──────────────────────────────────┐
│ 📋 新话题待确认                   │
│                                  │
│ 【智能巡检准召目标策略】        │
│ 来源：部门交流群 + 张三-王五  │
│ 消息数：17 条 · 关联 2 个 OKR    │
│                                  │
│ 摘要：王五提出准召第二阶段      │
│ 验证方案…                        │
│                                  │
│ [✅ 确认入库] [👁️ 看原文] [✕ 忽略] │
└──────────────────────────────────┘
```

**实现**：`lark-cli im +messages-send --as bot --user-id "ou_xxxxxxxxxxxxxxxxxxxxxxxxx" --markdown "..."`

**`feed-confirm` / `feed-ignore` 命令**：

```bash
iris feed-confirm feed-20260724-001    # 确认该话题入库（实际写入文件）
iris feed-ignore feed-20260724-001     # 忽略，删除待确认记录
iris feed-confirm --all                # 批量确认全部待确认
```

### 4.9 CursorTracker — 游标追踪

**文件**：`_cursor_tracker.py`

**存储**：`data/feed_cursors.json`

```json
{
  "chats": {
    "oc_xxx": {
      "last_cursor": "1122334455",
      "last_fetch_time": "2026-07-24T10:00:00",
      "last_msg_id": "om_xxxx"
    },
    "oc_yyy": {
      "last_cursor": null,
      "last_fetch_time": "2026-07-22T10:00:00",
      "last_msg_id": null
    }
  }
}
```

**关键接口**：

```python
class CursorTracker:
    def get_cursor(self, chat_id: str) -> str | None: ...
    def update_cursor(self, chat_id: str, cursor: str): ...
    def get_last_fetch(self, chat_id: str) -> datetime | None: ...
```

### 4.10 FeishuBridge — 飞书接口桥接

**文件**：`_feishu_bridge.py`

**职责**：封装 `lark-im` 相关 API 调用，统一错误处理

```python
class FeishuBridge:
    def search_group_messages(
        self,
        chat_id: str,
        since: datetime,
        until: datetime,
        cursor: str | None = None,
    ) -> tuple[list[RawMessage], str | None]: ...
    
    def search_single_messages(
        self,
        user_id: str,
        since: datetime,
        until: datetime,
        cursor: str | None = None,
    ) -> tuple[list[RawMessage], str | None]: ...
    
    def send_confirm_card(
        self,
        user_id: str,
        topic: DetectedTopic,
    ) -> bool: ...
```

---

## 五、Pipeline 编排

### 5.1 主流程

**文件**：`feed_pipeline.py`

```python
class FeedPipeline:
    def __init__(self, bundle, logger):
        self.bundle = bundle
        self.logger = logger
    
    def run(self, args) -> PipelineResult:
        """执行一次完整的信息汇聚"""
        
        # Step 0 - 配置加载
        config = load_feed_config(self.bundle.config_dir)
        self.logger.info("已加载 %d 个关注会话", len(config.watch_chats))
        
        # Step 1 - 消息获取
        fetcher = ChatFetcher(CursorTracker(self.bundle.data_dir))
        raw_messages = fetcher.fetch(
            chats=config.watch_chats,
            since=args.since,
            until=args.until,
        )
        self.logger.info("获取到 %d 条消息", sum(len(v) for v in raw_messages.values()))
        if not any(raw_messages.values()):
            return PipelineResult.empty("没有新消息")
        
        # Step 2 - 噪音过滤
        filter_ = MessageFilter()
        filtered = filter_.filter(raw_messages)
        self.logger.info("过滤后剩余 %d 条", sum(len(v) for v in filtered.values()))
        
        # Step 3 - 话题检测 + OKR 匹配（合并一步）
        okr_context = OKRLoader(source_root=self._source_dir).load()
        detector = TopicDetector(
            self.bundle.llm_service,
            brief_dir=...,
            okr_context=okr_context.to_prompt_context() if okr_context else "",
        )
        topics = detector.detect(filtered, ...)
        # Step 3b - OKR 标签解析（将 kr_id 展开为实际描述）
        if okr_context:
            for t in topics:
                t.okr_tags = okr_context.resolve_tags(t.okr_tags)
        self.logger.info("检测到 %d 个话题（含 OKR 匹配）", len(topics))
        
        # Step 4 - 简报生成（v1.1: 简报模板含 OKR 关联章节）
        exec_date = (args.until or datetime.now()).strftime("%Y%m%d")
        generator = BriefGenerator(self._get_brief_dir(exec_date))
        brief_files = generator.generate(topics, converted_docs, exec_date)
        self.logger.info("生成了 %d 份简报", len(brief_files))
        
        # Step 5 - 分发
        dispatcher = Dispatcher(self.bundle)
        result = dispatcher.dispatch(topics, brief_files)
        self.logger.info(
            "直接入库: %d, 待确认: %d",
            len(result.auto_imported),
            len(result.pending),
        )
        
        return result
```

### 5.2 错误处理原则

| 异常场景 | 处理方式 |
|---------|---------|
| 飞书 API 超时/限流 | 重试 1 次（指数退避），跳过该会话继续处理其他会话 |
| OKR 文件不存在 | 降级，跳过 OKR 上下文注入，话题不关联 OKR 标签 |
| 文档转换失败 | 记录日志，在简报中标记为「文档转换失败，请手动处理」 |
| LLM 调用失败 | 降级到规则聚合（不退化为完全无话题）；v1.1 新增 deadline 超时控制 |
| 某步骤局部异常 | 不中断整个 Pipeline，跳过该话题继续处理 |

### 5.3 执行日志

每次执行生成一份简要日志文件：

```
SOURCE/09-工作简报/YYYYMM/
  └── 信息汇聚日志-YYYYMMDD.md
```

内容示例：

```markdown
# 信息汇聚日志 · 2026-07-24

## 执行概要
- 扫描会话：4 个（群聊 3 + 单聊 1）
- 获取消息：127 条（过滤前）→ 86 条（过滤后）
- 检测话题：5 个（其中 3 个关联 OKR）
- 转换文档：2 篇
- 生成简报：5 份
  - ✅ 自动入库：3 份
  - 👁️ 待确认：2 份

## 话题清单
| 话题 | OKR 关联 | 消息数 | 来源 | 状态 |
|------|---------|:------:|------|:----:|
| 智能巡检准召目标 | 智能巡检 | 17 | 2 个会话 | ✅ |
| 评测框架 | — | 8 | 1 个会话 | 👁️ |
```

---

## 六、CLI 命令设计

### 6.1 命令注册

在 `src/iris/app/cli/_handlers/_content.py`（或新建 `_feed.py`）中增加处理器函数，在 `src/iris/app/cli/` 的主路由中注册。

```python
# 在 CLI 路由表中注册：
# iris feed-collect [--since DATE] [--until DATE] [--chat CHAT_ID] [--dry-run] [--mode auto|confirm|all]
# iris feed-pending [--limit N]
# iris feed-confirm <topic_id> [--all]
# iris feed-ignore <topic_id>
# iris feed-history [--days N]
```

### 6.2 命令详情

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `feed-setup` | 交互式配置向导 | （无参数） |
| `feed-list` | 查看关注列表 | （无参数） |
| `feed-add` | 添加关注会话 | `--chat`, `--type`, `--mode`, `--tags` |
| `feed-remove` | 移除关注会话 | `--chat` |
| `feed-config` | 修改配置 | `--chat`, `--mode`, `--tags`, `--show` |
| `feed-collect` | 执行一次信息汇聚 | `--since`, `--until`, `--chat`, `--mode`, `--dry-run` |
| `feed-pending` | 列出待确认的话题 | `--limit` |
| `feed-confirm` | 确认某话题入库 | `topic_id` / `--all` |
| `feed-ignore` | 忽略某话题 | `topic_id` / `--all` |
| `feed-history` | 查看汇聚历史 | `--days`（默认 7） |

---

## 七、类型定义

### `_types.py`

```python
@dataclass
class RawMessage:
    """原始飞书消息"""
    msg_id: str
    chat_id: str
    chat_name: str
    sender: str
    content: str           # 纯文本内容
    raw_content: dict      # 原始消息结构（含链接、图片等）
    msg_type: str          # text/image/file/doc等
    send_time: datetime
    has_doc_link: bool     # 是否包含飞书文档链接
    doc_links: list[str]   # 提取的文档 URL

@dataclass
class SourceRef:
    """来源引用"""
    type: Literal["group", "single"]
    name: str              # 群名或联系人名
    msg_count: int

@dataclass
class DetectedTopic:
    """检测到的话题"""
    topic_id: str          # feed-YYYYMMDD-NNN
    title: str
    summary: str           # LLM 生成的核心摘要
    key_status: str        # 当前状态
    discussion_points: list[str]
    decisions: list[str]
    quotes: list[Quote]
    participants: list[str]
    messages: list[RawMessage]
    source_chats: list[SourceRef]
    is_update: bool
    previous_version: str | None
    okr_tags: list[str]
    okr_match_strength: Literal["strong", "weak", "none"]

@dataclass
class Quote:
    text: str
    speaker: str
    time: str

@dataclass
class ConvertedDoc:
    """转换后的本地文档"""
    original_url: str
    local_path: Path
    relative_path: str
    title: str
    source_chat: str

@dataclass
class PipelineResult:
    """Pipeline 执行结果"""
    fetched_count: int
    filtered_count: int
    topics: list[DetectedTopic]
    brief_files: list[Path]
    converted_docs: list[ConvertedDoc]
    auto_imported: list[str]
    pending: list[str]
    
    @staticmethod
    def empty(reason: str) -> PipelineResult: ...
```

---

## 八、涉及的外部依赖

| 依赖 | 用途 | 说明 |
|------|------|------|
| `lark-im`（飞书 IM API） | 消息获取 | `search_message` / `list_message` |
| `lark-okr`（飞书 OKR API） | OKR 数据加载 | 获取当前周期 OKR |
| `feishu-doc-convert` 管道 | 文档转换 | 聊天中出现的文档链接 → 本地 MD |
| `LLMService`（Iris LLM 模块） | 话题聚合/摘要生成/OKR 匹配 | 复用现有 Provider 和用量追踪 |
| 现有路由规则（transcribe-meeting） | 文档归档路由 | 复用 `config/meeting_routes.json` |

---

## 九、开发实现计划

### Phase 1 — MVP（预计 3-5 天）

**范围**：
- ✅ 配置体系：`feeds.json` + `feeds.json.example` + `FeedConfig` 数据类
- ✅ 交互式配置向导：`feed-setup`（飞书群聊自动发现 + 分步配置）
- ✅ 配置管理命令：`feed-list` / `feed-add` / `feed-remove` / `feed-config`
- ✅ 消息获取 + 游标追踪（仅群聊）
- ✅ 噪音过滤
- ✅ 话题检测（规则分割 + LLM 聚合）
- ✅ 简报生成（基础模板）
- ✅ 分发（auto_import 即可，确认机制后置）
- ✅ CLI `feed-collect` 命令（基础版）

**不含**：
- ❌ `watch_singles` 单聊支持（Phase 2）
- ❌ OKR 匹配（Phase 2）
- ❌ 确认卡片推送（Phase 3）
- ❌ 文档提取转换（Phase 2）

### Phase 2 — 功能补齐（预计 2-3 天）

- `watch_singles` 单聊消息获取
- ~~OKR 数据加载 + 匹配~~ ✅ **v1.1 已完成**（变更为 OKRLoader + LLM 一体检测）
- 文档链接提取与转换
- 跨话题聚合增强

### Phase 3 — 体验完善（预计 2 天）

- 飞书确认卡片推送
- `feed-pending` / `feed-confirm` / `feed-ignore` 命令
- `feed-history` 命令
- `--dry-run` 模式
- 执行日志生成

---

## 十、与现有系统的关系

```
初始化 → 独立模块（src/iris/feed/）
成熟后 → feed-collect 作为 daily-start 的一个阶段
         （在 discovery → build 之间插入“信息汇集”阶段）
```

| 阶段 | 集成方式 |
|------|---------|
| 当前 | 独立子模块 + 独立 CLI 命令 |
| 成熟期 | `daily-start` 中增加 `--with-feed` 可选阶段 |
| | 或自动执行（每天一次，在 build-wiki 之前） |

---

## 十一、已决策事项

以下为开发前已确认的决策，设计文档以此为基准：

| # | 事项 | 决策 | 依据 |
|:-:|------|------|------|
| 1 | 消息搜索 API | **`lark-cli im +messages-search`**，支持 `--chat-id` + `--start/--end`(ISO 8601 含时区) + 分页 `--page-size`/`--page-token` | 2026-07-24 实测验证通过 |
| 2 | 群聊发现 API | **`lark-cli im +chat-search`**（按名称搜索）和 **`+chat-list`**（列出用户群聊列表） | 实测验证通过 |
| 3 | Bot 发送消息 | **已建立推送通道**：私密群「Iris 信息汇聚」(`oc_xxxxxxxxxxxxxxxxxxxxxxx`)，bot+用户两人。Phase 3 直接往此群发确认卡片。Phase 1 先用控制台输出 + CLI 确认 | 2026-07-24 实测 `+chat-create --as bot` 建群 + `+messages-send --as bot` 成功 |
| 4 | OKR 数据来源 | **从本地 SOURCE 读取**：`SOURCE/01-目标管理/<年份>/` 下的最新 OKR 文件（按文件名日期排序取最新） | 用户确认 |
| 5 | OKR 加载策略 | 每次 `feed-collect` **重新扫描本地**（无缓存问题，读取本地文件无 API 成本） | 用户确认 |
| 6 | 确认卡片接收人 | 控制台输出 + CLI 交互（Phase 1），后续 bot 推送到用户飞书单聊（Phase 3） | 用户确认 |

### OKR 本地加载方式（v1.1 已实现）

> 实现文件：`src/iris/feed/_okr_loader.py`

```
SOURCE/01-目标管理/YYYY/
  └── YYYYMMDD-数据部门-张三-2026年Q3-OKR.md
```

加载逻辑（`_find_latest_okr_file`）：
1. 扫描 `SOURCE/01-目标管理/<年份>/` 目录（按年份降序）
2. 文件名必须含「数据部门」关键词
3. 排除含 OP/双周/周报/团队/个人/检查 的无关文件
4. 按文件名降序取最新

解析逻辑（`_parse_okr_file`）：
- 去掉 frontmatter
- 正则提取 `## O<数字>：标题` → Objective
- 正则提取 `### KR<数字>：标题` → KR（含 Owner）
- KR ID 拼接为 "O1-KR1" 格式
- 支持 `short_title`（提取 `【】` 内标记）

当前最新文件：`20260715-数据部门+测试研发-张三-2026年Q3-OKR.md`

### 待后续关注

- **话题去重匹配精度**：Phase 1 写死在 LLM prompt 中，运行几次后根据实际效果调优
