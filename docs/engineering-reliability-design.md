# Iris 工程可靠性设计

> 版本：Iris 3.28.1 · 日期：2026-08-26（v3.28.1 增补 2026-08-30）· 状态：已实现

## 1. 目标

本轮治理处理的不是新增业务能力，而是长期运行和多进程并发下的四类基础风险：资源泄漏、部分写入、锁失效和多文件制品撕裂。设计目标如下：

- 进程退出前可预测地释放 SQLite 连接等持久资源。
- 单文件写入在崩溃、磁盘满或并发读取时只呈现旧版本或完整新版本。
- 同一资源的多个进程始终竞争同一个锁 inode。
- 向量索引等多文件制品只发布完整代际。
- 缓存重启后仍遵守 TTL、容量和最近访问顺序。
- 从任意工作目录启动时，配置、模板和委托脚本解析到同一个项目根。

## 2. 持久化分层

| 场景 | 机制 | 约束 |
|------|------|------|
| 单文件覆盖 | `atomic_write_text/bytes/json` | 同目录临时文件，flush + fsync 后 `os.replace` |
| 共享文件 RMW | `FileLock` + 原子写 | 读取、修改、发布必须处于同一临界区 |
| 多文件制品 | generation + 原子指针 | generation 写全后才更新 `current.json` |
| 结构化高频数据 | SQLite WAL | 显式事务，使用后 `close()` 或上下文管理器 |
| 外部路径写入 | `safe_write_text/bytes` | 先执行写入守卫，再原子发布 |

### 2.1 稳定 inode 文件锁

`FileLock(path)` 实际锁定 `<path>.lock` 的 inode。释放动作只执行 `LOCK_UN` 和关闭文件描述符，锁文件必须长期保留。

如果释放后 unlink，已经等待在旧 inode 上的进程仍可能随后取得旧锁，而新进程会创建并锁住新 inode，两个临界区同时运行。因此 `.lock` 不是临时垃圾，清理脚本不得删除运行数据旁的锁文件。

### 2.2 单文件原子发布

共享工具位于 `iris.utils.shared`：

- `atomic_write_bytes(path, data)`：二进制基础实现。
- `atomic_write_text(path, content)`：UTF-8 文本发布。
- `atomic_write_json(path, data)`：统一 JSON 编码与发布。

临时文件必须创建在目标文件同目录，保证 `os.replace` 不跨文件系统。原子发布解决文件撕裂，但不替代 RMW 锁，也不提供多文件事务回滚。

### 2.3 向量索引 generation

每个数据源的向量索引由 `vectors.npy`、`ids.json`、`meta.json` 组成。保存流程为：

```text
获取索引锁
  -> 创建 generations/<uuid>/
  -> 写全 vectors.npy、ids.json、meta.json
  -> 原子更新 current.json
  -> 清理旧 generation
释放索引锁
```

读取方先读取 `current.json`，再从指定 generation 加载三个文件。旧版平铺索引仍可读取，用于平滑迁移；新写入只使用 generation 格式。

### 2.4 向量索引增量正确性（v3.28.1）

generation 机制保证「发布完整性」，但不保证「内容正确性」。v3.28.1 修复增量更新的两个语义洞：

- **按 hash 判定重嵌**：chunk_id（`路径::序号`）不含内容指纹，仅凭 `exists(chunk_id)` 跳过会让编辑过的文档永远使用旧向量。`ids.json` 新增 `doc_hashes` 字段记录每个 chunk 入索引时的 `document_hash`，hash 变化即重嵌；旧索引无该字段时不触发重嵌（避免升级即全量重嵌），由一次 `--force-rebuild` 补齐。
- **差集清理死向量**：增量更新按「本次全量语料 chunk_id 集合」的差集删除残留向量（已删除/归档文档）。因此 `build_vector_index` 的 `chunks` 参数是**全量语料语义**——调用方必须传入该数据源的完整 chunk 列表，传子集会把缺失部分当已删除清理。

### 2.5 LLM 响应文本提取正确性（v3.28.1）

`_extract_chat_completions_text`（`llm/provider.py`）原在 `content` 为空时**静默回退返回 `reasoning_content`**（思考过程）——思考模型（deepseek-v4-flash）max_tokens 耗尽（finish_reason=length）时 content 为空而 reasoning 非空，思考文本被当最终输出返回。下游感知「成功」而把思考写入产物（实测 w35 双周报 Stage 4b 质量审查 13k 思考字符直接写入归档文件），比显式失败危害更大。

v3.28.1 确立的可靠性约定：

- **绝不静默返回思考文本**：`content` 为空直接抛 `LLMProviderError`，走上层重试/降级链——「失败」是安全态，「伪成功」是危险态，宁可让调用方降级也不产出「看起来成功」的垃圾文本。
- **业务关键路径必须防御性降级**：消费方不得假定 `generate()` 永远成功；重要产物路径应有显式 fallback（如双周报 Stage 4b 质量审查失败回退 Stage 4a 组装稿并告警）。

## 3. 缓存一致性

LLM 响应缓存以内容哈希寻址，但“同一 key 写入幂等”不足以保证 TTL 与 LRU 正确。v3.28.0 增加：

- 跨进程 `FileLock`，串行化缓存元数据 RMW。
- 启动时扫描磁盘，清理过期与损坏项。
- 从持久化访问时间重建 LRU 顺序，并执行容量淘汰。
- 命中时持久化最近访问时间，使重启后的淘汰顺序保持有效。

Embedding 的进程内 LRU 仍用于热点加速；其持久向量制品由 generation 机制保证一致性。

## 4. 资源生命周期

`ChunkStore` 支持上下文管理器并暴露 `close()`。一次性调用方必须采用以下任一形式：

```python
with ChunkStore(db_path) as store:
    rows = store.load_all()
```

```python
store = ChunkStore(db_path)
try:
    rows = store.load_all()
finally:
    store.close()
```

禁止依赖对象析构或垃圾回收关闭连接。WAL 改善读写并发，但不消除连接和事务的生命周期责任。

## 5. 配置与项目根

写入守卫的规范字段为 `safety.enforce_write_guard`。旧字段 `deny_write_outside_allowed_paths` 继续兼容；两者都缺失时默认启用。关闭守卫只跳过路径限制，写入仍采用原子发布。

项目根解析优先使用 `IRIS_PROJECT_ROOT`。该入口用于 launchd、容器、委托脚本和从仓库外启动的 CLI，避免依赖当前工作目录或包安装位置猜测路径。

归档配置 `config/source_archive.json` 缺失时回退 `.example`（v3.28.1，与 config/loader.py 惯例一致）——否则干净 checkout（`config/*.json` gitignored）下归档模式整体退化为 flat，归档路径行为与生产环境不一致。

## 5.1 队列追加语义（v3.28.1）

「锁内 RMW」原则的一个易错变体是**队列/清单类文件**：写方持有本次批次数据、在锁内直接原子写整个文件，看似合规，实际是「锁内盲写」而非「锁内读-合并-写」，会覆盖其他批次留下的条目（feed 待确认队列曾因此丢失历史未确认话题）。规范：追加类共享文件必须提供 `append_*` 接口，在锁临界区内完成 load → 按业务键去重合并 → 原子写；整体覆盖语义的 `save_*` 仅限「已读旧数据再回写」的调用方使用。

## 6. 质量门禁

- 支持 Python 3.11、3.12、3.13；最低版本为 3.11。
- CI、Makefile 和 pre-commit 的 Ruff 范围统一为 `src scripts tests`。
- 开发依赖通过 `constraints.txt` 约束；安全审计使用 `pip-audit`。
- 提交前至少执行 `make lint`、`pytest` 和 `git diff --check`。

## 7. 边界

- `fcntl.flock` 面向单机 POSIX 本地文件系统，不承诺 NFS/SMB 或 Windows 行为。
- 单文件原子写不解决两个写者的业务语义冲突；同一 Wiki 页面等资源仍需上层协调。
- generation 只保证发布完整性，不自动提供跨代际业务回滚。
- 写入守卫是应用层边界，不替代操作系统权限与密钥管理。
