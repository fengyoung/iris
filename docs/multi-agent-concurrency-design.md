# Iris 多 Agent 并发支持方案

> 目标：使 Iris 在多个 agent 进程同时运行时数据安全、状态一致。
> 创建时间：2026-07-22

---

## 一、问题诊断

### 1.1 当前架构假设

Iris 的设计隐含假设**单进程运行**。证据：
- 无 PID 文件或进程互斥机制
- 无跨进程协调层
- `FileLock`（基于 `fcntl.flock`）仅用于 3 个文件，其余全部裸写
- 两个常驻守护进程（`asr-corrector`、`watch`）可与一次性 CLI 命令并发运行

### 1.2 风险全景图

```
┌─────────────────────────────────────────────────────────────────┐
│                      Iris 数据流 & 并发冲突点                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐                  │
│  │ Agent A  │    │ Agent B  │    │ Daemon   │                  │
│  │ (CLI)    │    │ (CLI)    │    │ (watch)  │                  │
│  └────┬─────┘    └────┬─────┘    └────┬─────┘                  │
│       │               │               │                         │
│       ▼               ▼               ▼                         │
│  ┌────────────────────────────────────────────────────┐        │
│  │              data/ (共享存储层)                      │        │
│  │                                                    │        │
│  │  🔴 metadata/          ← scan/chunk 读写竞态        │        │
│  │  🔴 llm_usage.db       ← SQLite 无 WAL 模式         │        │
│  │  🔴 graph/             ← nodes+edges 非原子写入      │        │
│  │  🔴 cache/biweekly/   ← brief_index RMW 竞态       │        │
│  │  🟡 asr_feedback.jsonl ← 追加写可能字节交错          │        │
│  │  🟡 active_model.json  ← 无锁覆盖                   │        │
│  │  🟢 cache/llm_responses/ ← 内容寻址，幂等安全        │        │
│  └────────────────────────────────────────────────────┘        │
│                                                                 │
│  ┌────────────────────────────────────────────────────┐        │
│  │              memory/ (记忆存储层)                    │        │
│  │                                                    │        │
│  │  🟢 long_term/   ← FileLock 保护 ✅                 │        │
│  │  🔴 session/     ← RMW 无锁                         │        │
│  │  🔴 working/     ← RMW 无锁                         │        │
│  └────────────────────────────────────────────────────┘        │
│                                                                 │
│  ┌────────────────────────────────────────────────────┐        │
│  │              logs/ (日志层)                          │        │
│  │                                                    │        │
│  │  🟡 iris.jsonl   ← 归档 TOCTOU 竞态                 │        │
│  └────────────────────────────────────────────────────┘        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 风险分级定义

| 级别 | 含义 | 判定标准 |
|:----:|------|---------|
| 🔴 P0 | 数据损坏 | 并发写入导致不可逆数据丢失或损坏 |
| 🟡 P1 | 功能异常 | 并发导致结果错误但不损坏存量数据 |
| 🟢 P2 | 体验退化 | 并发导致重试/降级但不影响正确性 |

---

## 二、改造方案

### 2.1 核心策略

**三层防护体系**：

```
第 1 层：文件锁（FileLock）
         └── 保护所有 RMW（读-改-写）模式的文件操作
             已有基础，推广到全项目

第 2 层：存储引擎加固
         └── SQLite → WAL 模式统一
             追加写 → fcntl 保护
             多文件原子性 → 临时目录 + rename

第 3 层：进程级协调（新增）
         └── 命令互斥注册表（PID 文件）
             会话/工作记忆按 Agent 隔离
```

### 2.2 分步实施方案

---

## 步骤 1（P0）— 文件锁推广：RMW 模式加锁

**目标**：所有读-改-写文件操作都用 `FileLock` 保护。

**改动清单**：

| # | 文件 | 方法 | 保护目标 | 改动方式 |
|---|------|------|---------|---------|
| 1.1 | `ingest/chunker.py:171` | `write_hash_index()` | `chunk_hash_index.json` | 整个 RMW 段包裹 `with FileLock(index_path)` |
| 1.2 | `memory/session.py:33` | `save_interaction()` | `latest_session.json` | 整个 RMW 段包裹 `with FileLock(self._path)` |
| 1.3 | `memory/working.py:59` | `update()` | `working_context.md` | 整个 RMW 段包裹 `with FileLock(self._path)` |
| 1.4 | `feishu/_shared.py:94` | `save_dedup_index()` | 排重索引 JSON | `load_dedup_index` + 修改 + `save_dedup_index` 整体加锁 |
| 1.5 | `wiki/graph.py:360` | `save()` | `nodes.json` + `edges.json` | 整个 save 包裹锁，确保双文件原子性 |
| 1.6 | `retrieval/vector_index.py:84` | `save()` | 向量索引三文件 | 整个 save 包裹锁 |

**实施细节**（以 1.1 为例）：

```python
# chunker.py write_hash_index() — 改造前
def write_hash_index(self, summary):
    index = {}
    existing_path = self._metadata_dir / "chunk_hash_index.json"
    if existing_path.exists():
        index = json.loads(existing_path.read_text(...))
    # ... 修改 index ...
    index_path.write_text(json.dumps(index, ...))

# chunker.py write_hash_index() — 改造后
def write_hash_index(self, summary):
    index_path = self._metadata_dir / "chunk_hash_index.json"
    with FileLock(index_path):
        index = {}
        if index_path.exists():
            index = json.loads(index_path.read_text(...))
        # ... 修改 index ...
        index_path.write_text(json.dumps(index, ...))
```

**工作量**：6 个文件，每个 ~10 行改动，共约 60 行。

---

## 步骤 2（P0）— SQLite WAL 模式 + 重试

**目标**：消除 `database is locked` 错误和静默丢数据。

**改动清单**：

| # | 文件 | 改动 |
|---|------|------|
| 2.1 | `llm/usage_tracker.py:103` | `_connect()` 加 `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000` |
| 2.2 | `llm/usage_tracker.py:110` | `record()` 增加重试逻辑（最多 3 次，指数退避），不再静默吞异常 |

**实施细节**：

```python
# usage_tracker.py — 改造后
def _connect(self) -> sqlite3.Connection:
    conn = sqlite3.connect(str(self._db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn

def record(self, ...):
    # ... 现有逻辑，但移除 except 中的静默吞异常
    # 改为记录 warning 级别日志（而非 debug）
```

**工作量**：1 个文件，约 10 行改动。

---

## 步骤 3（P1）— 双周报缓存加锁

**目标**：防止并发双周报生成导致 brief 文件被误删。

**改动清单**：

| # | 文件 | 方法 | 改动 |
|---|------|------|------|
| 3.1 | `analysis/_biweekly_cache.py:150` | `flush_brief_index()` | 读写 index + 清理旧文件整体包裹 `FileLock` |
| 3.2 | `analysis/service.py:640` | `_save_brief_index()` | 同上（静态方法版本） |

**工作量**：2 个文件，约 20 行改动。

---

## 步骤 4（P1）— 会话/工作记忆按 Agent 隔离

**目标**：每个 agent 进程拥有独立的会话和工作记忆，避免互相覆盖。

**方案**：通过环境变量 `IRIS_AGENT_ID` 区分 agent。

```
data/agents/
├── default/                    ← 未设置 IRIS_AGENT_ID 时使用
│   ├── session.json
│   └── working_context.md
├── agent-1/                    ← IRIS_AGENT_ID=agent-1
│   ├── session.json
│   └── working_context.md
└── agent-2/                    ← IRIS_AGENT_ID=agent-2
    ├── session.json
    └── working_context.md
```

**改动清单**：

| # | 文件 | 改动 |
|---|------|------|
| 4.1 | `memory/session.py:20` | `__init__` 中根据 `IRIS_AGENT_ID` 计算子目录路径 |
| 4.2 | `memory/working.py:25` | `__init__` 中根据 `IRIS_AGENT_ID` 计算子目录路径 |
| 4.3 | `utils/paths.py` | 新增 `get_agent_data_dir()` 工具函数 |

**实施细节**：

```python
# utils/paths.py 新增
def get_agent_data_dir(data_root: Path) -> Path:
    agent_id = os.environ.get("IRIS_AGENT_ID", "default")
    return data_root / "agents" / agent_id
```

```python
# session.py 改造
def __init__(self, session_summary_dir: Path):
    agent_dir = get_agent_data_dir(session_summary_dir.parent)
    self._path = agent_dir / "latest_session.json"
    self._path.parent.mkdir(parents=True, exist_ok=True)
```

**工作量**：3 个文件，约 40 行改动。

---

## 步骤 5（P1）— 日志归档竞态修复

**目标**：消除两个进程同时判断需要归档时的 TOCTOU 竞态。

**改动清单**：

| # | 文件 | 方法 | 改动 |
|---|------|------|------|
| 5.1 | `utils/logging.py:79` | `_rotate_with_lock()` | 检查大小移到 `fcntl.flock` 内部 |

**实施细节**：

```python
# 改造前：检查和加锁分离
if self._file.tell() > self._max_bytes:    # ← TOCTOU 窗口
    self._rotate_with_lock()
# ... 加锁在 _rotate_with_lock 内部

# 改造后：加锁后再检查
fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
try:
    if self._file.tell() > self._max_bytes:
        self._do_rotate()                  # 归档逻辑
finally:
    fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
```

**工作量**：1 个文件，约 20 行改动。

---

## 步骤 6（P2）— 进程互斥注册表

**目标**：防止守护进程重复启动，提供进程可见性。

**改动清单**：

| # | 文件 | 改动 |
|---|------|------|
| 6.1 | `core/locks.py` | 新增 `ProcessRegistry` 类：PID 文件管理 +  stale 检测 |
| 6.2 | `wiki/asr/corrector.py:1040` | 启动时注册 PID，退出时清理 |
| 6.3 | `app/cli/_handlers/_data.py:313` | watch 命令启动时注册 PID |

**实施细节**：

```python
# core/locks.py 新增
class ProcessRegistry:
    """进程注册表 — 基于 PID 文件防止重复启动。"""

    def __init__(self, name: str, pid_dir: Path):
        self._pid_file = pid_dir / f"{name}.pid"

    def register(self) -> bool:
        """注册当前进程。返回 False 表示已有同名进程运行。"""
        if self._pid_file.exists():
            stale_pid = int(self._pid_file.read_text().strip())
            if self._is_alive(stale_pid):
                return False  # 已有进程运行
            # PID 文件残留（进程已死），覆盖
        self._pid_file.write_text(str(os.getpid()))
        return True

    def unregister(self):
        self._pid_file.unlink(missing_ok=True)

    @staticmethod
    def _is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
```

**工作量**：3 个文件，约 50 行改动。

---

## 步骤 7（P2）— 追加写文件 fcntl 保护

**目标**：确保 JSONL 追加写的字节级原子性。

**改动清单**：

| # | 文件 | 改动 |
|---|------|------|
| 7.1 | `wiki/asr/feedback.py:49` | 追加写前获取 `fcntl.flock(LOCK_EX)` |
| 7.2 | `wiki/discovery.py:171` | JSONL 导出追加写同样保护 |

**工作量**：2 个文件，约 15 行改动。

---

## 三、实施计划

### 3.1 优先级排序

```
P0（必须修，否则数据损坏）
│
├── 步骤 1：RMW 加锁（6 文件，~60 行）     ← 风险最大
└── 步骤 2：SQLite WAL（1 文件，~10 行）    ← 改造成本最低
                                           ∑ ~70 行

P1（重要，影响功能正确性）
│
├── 步骤 3：双周报缓存加锁（2 文件，~20 行）
├── 步骤 4：Agent 记忆隔离（3 文件，~40 行）
└── 步骤 5：日志归档修复（1 文件，~20 行）
                                           ∑ ~80 行

P2（改善健壮性）
│
├── 步骤 6：进程注册表（3 文件，~50 行）
└── 步骤 7：追加写保护（2 文件，~15 行）
                                           ∑ ~65 行

总计：~215 行 / 18 文件
```

### 3.2 依赖关系

```
步骤 1 ──→ 步骤 3（共享 FileLock 基础设施）
步骤 2      （独立，可并行）
步骤 4      （独立，可并行）
步骤 1 ──→ 步骤 5（fcntl 使用模式一致）
步骤 1 ──→ 步骤 6（复用 FileLock / PID 写入模式）
步骤 5 ──→ 步骤 7（fcntl 保护模式一致）
```

### 3.3 建议执行顺序

```
第 1 轮（P0，立即修复）
  步骤 2（SQLite WAL，5 分钟）
  步骤 1（RMW 加锁，30 分钟）
  运行现有 1,753 个测试确认无回归
  提交

第 2 轮（P1，功能加固）
  步骤 5（日志归档，10 分钟）
  步骤 3（双周报缓存，15 分钟）
  步骤 4（Agent 隔离，20 分钟）
  运行现有测试
  提交

第 3 轮（P2，体验优化）
  步骤 7（追加写保护，10 分钟）
  步骤 6（进程注册表，20 分钟）
  新增并发场景测试
  提交
```

### 3.4 测试策略

| 步骤 | 现有测试覆盖 | 需新增测试 |
|------|:----------:|:--------:|
| 步骤 1 | `test_file_lock.py` 覆盖 FileLock 基础 | 每个加锁文件增加并发写入测试 |
| 步骤 2 | 无 SQLite 测试 | `test_usage_tracker.py` + 并发写入场景 |
| 步骤 3 | 无缓存并发测试 | `test_biweekly_cache.py` + 双进程场景 |
| 步骤 4 | 无 agent 隔离测试 | `test_agent_isolation.py` |
| 步骤 5 | 无日志测试 | `test_logging.py` + 归档竞态 |
| 步骤 6 | 无 | `test_process_registry.py` |
| 步骤 7 | 无 | `test_feedback.py` + 并发追加 |

关键原则：**现有 1,753 个测试必须全部保持通过**。每轮改动后立即跑全量测试。

---

## 四、风险评估

### 4.1 技术风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| `FileLock` 增加延迟 | 每个 RMW 操作多等 0-30s（竞争时） | 大部分场景无竞争；超时可配置 |
| 锁文件残留 | 磁盘占用 .lock 文件 | `release()` 已做 `unlink(missing_ok=True)` |
| macOS NFS 上 `fcntl.flock` 不可靠 | 挂载卷上锁失效 | 文档已说明，本地磁盘不受影响 |
| Agent 隔离改变文件路径 | 存量会话数据不可见 | 首次运行时自动迁移 default 目录数据 |

### 4.2 兼容性风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| `IRIS_AGENT_ID` 未设置 | 所有 agent 仍共享 session/working | 默认使用 `default` 子目录，行为不变 |
| 旧版数据路径 | lock 文件路径变化 | `FileLock` 使用 `.lock` 后缀，与数据文件同目录 |

---

## 五、与已有关联方案的协同

### 5.1 软链配置方案（已完成）

当前 worktree 已通过软链指向 main 分支的 `config/`、`data/`、`.env`：
- ✅ 本方案的所有改动都在代码层面，不改变数据文件路径
- ✅ 软链使多 agent 共享同一份数据 → 本方案保护这份共享数据
- ✅ 两个方案互补：软链解决"配置一致"，本方案解决"并发安全"

### 5.2 后续演进方向（不在本方案范围）

- 引入 `aiosqlite` 实现真正的异步 SQLite 访问
- 考虑 SQLite → PostgreSQL 迁移（若 agent 数量 > 10）
- 引入任务队列（Redis/RQ）替代进程级协调

---

## 六、适用场景

方案实施后，以下场景在并发下可安全运行：

### 多 Agent 日常协作

- 多个 Claude Code agent 在同一台机器上并发调用 `iris` CLI 命令（`iris ask`、`iris search`、`iris build-wiki` 等）
- 一个 agent 执行 `daily-start` 维护链，另一个 agent 同时执行 `feishu-doc-convert` 导入文档
- 多个 agent 各自调用 `iris ask` 进行知识问答（只读为主，天然低冲突）

### 守护进程 + 手动命令并发

- `iris watch` 文件监听守护进程运行期间，手动执行 `iris build-chunks` 或 `iris scan-source`
- `iris asr-corrector` 常驻运行期间，手动执行 `iris asr-audit` 或 `iris asr-report`
- 守护进程写反馈数据（`asr_feedback.jsonl`）的同时，手动命令读取分析

### 批量操作并发

- 多个 `iris feishu-doc-convert` 同时从飞书导入不同文档（排重索引加锁保护）
- 多个 `iris chat-digest` 同时提炼不同聊天记录
- 多个 `iris build-biweekly-report` 为不同部门/方向生成双周报

### 记忆独立维护

- 每个 agent 通过 `IRIS_AGENT_ID` 环境变量持有独立的会话记忆和工作上下文
- 长期记忆（用户画像、概念纠正）跨 agent 安全共享（FileLock 保护）
- 一个 agent 更新用户画像，另一个 agent 同时读取——读会被阻塞直到写完成，读到的一定是完整数据

### 共享基础设施

- 所有 agent 共享同一份 LLM 响应缓存（内容寻址，写入幂等，无锁安全）
- 所有 agent 共享同一份 Embedding 向量缓存（进程内 LRU，互不干扰）
- 所有 agent 共享 `chunk_store.db`（已开启 WAL 模式，多读一写）
- 知识图谱跨 agent 共享（`nodes.json` + `edges.json` 在锁内原子更新）

---

## 七、不适用场景

方案存在明确边界，以下场景**不在保护范围内**：

### 多机分布式部署

- **不适用**：多台机器挂载同一 NFS/AFP 共享目录运行 Iris
- **原因**：`fcntl.flock` 在 macOS NFS 挂载卷上不可靠，锁可能完全不生效，数据必然损坏
- **正确做法**：每台机器独立的数据目录，或切换到 PostgreSQL 等 C/S 架构数据库

### 网络文件系统

- **不适用**：数据目录位于任何网络挂载卷（NFS、AFP、SMB、NAS）
- **原因**：文件锁语义在不同网络文件系统实现中差异巨大，部分完全忽略 `fcntl`
- **正确做法**：`data/` 目录必须位于本地磁盘（APFS / ext4 / XFS）

### 同资源并发写冲突

- **不适用**：两个 agent 同时生成同一个 Wiki 页面（如同时 `build-wiki --page "领域-数据智能"`）
- **原因**：本方案保护文件写入的原子性，但不提供业务层面的"同一页面不应被两个进程同时生成"的语义。后写入者覆盖先写入者
- **正确做法**：业务层面避免对同一页面并发构建，或由调用方（Claude Code Skill）做协调

### 跨文件事务回滚

- **不适用**：需要"要么全部成功、要么全部回滚"的跨文件操作
- **原因**：`FileLock` + `atomic_write` 只保证单文件写入不损坏，不提供 WAL 或事务日志那样的回滚能力。例如 graph 的 `nodes.json` + `edges.json` 虽然在同一个锁内写入，但如果进程在写完 nodes 后、写完 edges 前被 SIGKILL，状态仍然不一致
- **正确做法**：使用临时目录写入全部文件后 `os.rename` 原子切换，或引入 SQLite 存储图数据

### 高频写入场景

- **不适用**：每秒数十次以上的对同一文件的写入
- **原因**：`FileLock` 使用 100ms 轮询 + 30s 超时，锁竞争激烈时吞吐量急剧下降。这是**进程级协调锁**，不是高性能并发原语
- **正确做法**：引入消息队列缓冲写入，批量合并后落盘

### 实时性要求高的场景

- **不适用**：要求写入延迟 < 100ms 且不可预测等待的场景
- **原因**：锁竞争时，一个进程最多阻塞 30 秒。虽然典型场景下无竞争（毫秒级），但无法给实时系统提供确定性延迟保证
- **正确做法**：使用无锁数据结构或读写分离架构

### Python multiprocessing fork 场景

- **不适用**：使用 `multiprocessing.Process` fork 子进程，且父进程已持有 `FileLock`
- **原因**：`fork()` 会复制父进程的文件描述符，包括锁文件 fd。子进程继承了一个"已加锁"的 fd，但内核认为锁属于父进程——导致锁语义混乱
- **正确做法**：使用 `spawn` 而非 `fork` 启动子进程；或在 fork 前释放所有锁，子进程重新获取

### 跨平台异构部署

- **不适用**：同一数据目录在 macOS 和 Windows 之间共享
- **原因**：`fcntl.flock` 是 POSIX 专用 API，Windows 不支持。如果未来 Iris 需支持 Windows，锁机制需抽象为平台无关层
- **正确做法**：引入 `portalocker` 等跨平台库，或使用 SQLite 内置锁作为替代

### Agent 间实时协作

- **不适用**：需要 agent 之间实时通信、任务分配、结果汇总
- **原因**：本方案是**被动数据安全**方案——确保并发写入不损坏数据，不提供进程间消息传递、任务队列、事件通知等主动协调能力
- **正确做法**：在 Iris 上层（Claude Code Skill 或外部编排层）实现任务队列和消息传递

### 边界速查表

| 场景 | 是否适用 | 关键限制 |
|------|:---:|------|
| 单机多 agent 并发 CLI | ✅ 适用 | — |
| 守护进程 + 手动命令 | ✅ 适用 | — |
| 批量导入并发 | ✅ 适用 | — |
| 多 agent 各自独立记忆 | ✅ 适用 | 需设置 `IRIS_AGENT_ID` |
| 同一 Wiki 页面并发构建 | ❌ 不适用 | 后写覆盖，需业务层协调 |
| 多机共享 NFS 数据目录 | ❌ 不适用 | fcntl 在 NFS 不可靠 |
| 网络挂载卷（AFP/SMB） | ❌ 不适用 | 文件锁语义不确定 |
| 每秒 50+ 次同文件写入 | ❌ 不适用 | 锁竞争导致吞吐量骤降 |
| 要求写入延迟 < 100ms | ❌ 不适用 | 竞争时阻塞最长 30s |
| Python fork 子进程 | ❌ 不适用 | 锁 fd 继承导致语义混乱 |
| Windows 跨平台 | ❌ 不适用 | fcntl 是 POSIX 专用 |
| Agent 间实时任务分配 | ❌ 不适用 | 不提供进程间消息传递 |
