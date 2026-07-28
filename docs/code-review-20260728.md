# Iris 3.19.22 — 全量代码审查报告

> 审查日期：2026-07-28 · 审查范围：~27,000 行 / 149 文件 / 23 模块
> 审查方法：6 个并行 Agent 深度遍历全部模块 + 主线程交叉验证关键路径

---

## 总体评价

项目整体架构清晰，设计理念先进（"编译器而非解释器"的 Wiki 知识编译模式、三阶段复杂输入流水线、双通道记忆更新引擎等）。核心流程正确性良好，关键路径有防御性代码保护。存在的主要问题集中在：

1. **配置迁移未完成**：ConfigBundle → ConfigBundleV2 渐进迁移留下兼容 shim，部分模块仍用 dict 访问
2. **文件操作安全性不足**：多处非原子写入、读/写锁不匹配、JSON 解析无容错
3. **重复代码**：JSON 解析、模板替换、原子写入等工具函数在多个模块中重复定义
4. **硬编码值过多**：阈值、超时、路径、批大小散落各处，未集中配置

---

## P0 — 严重缺陷（影响正确性/数据安全）

### P0-1. 文件状态写入非原子，进程崩溃可致数据损坏

**影响文件**：
- `feed/_cursor_tracker.py:35-38` — `_save()` 直接覆盖 `feed_cursors.json`
- `feed/feed_config.py:111-116` — `save_feed_config()` 直接覆盖 `feeds.json`
- `feed/_dispatcher.py:229` — `save_pending` 直接覆盖待确认文件

**问题**：进程在 `write_text()` 中途崩溃会留下损坏的 JSON 文件（半写），导致下次启动无法解析，游标/配置/待确认全部丢失。

**修复建议**：统一使用 `utils/shared.py` 的 `atomic_write_json()`（临时文件 + `os.replace`）。该函数已存在，只是未被这些模块采用。

### P0-2. 读/写锁不匹配导致竞态条件

**影响文件**：
- `feishu/_shared.py:97-101` — `load_dedup_index()` **不加锁**读取
- `feishu/_shared.py:106-110` — `save_dedup_index()` **使用 FileLock** 写入
- `memory/long_term.py:31-37` — `UserProfileMemoryStore.load()` **不加锁**
- `memory/long_term.py:116-123` — `_save()` **加锁**写入
- `memory/long_term.py:133-136` — `CorrectionMemoryStore.load()` **不加锁**

**问题**：经典 read-write 竞态：进程 A 读完数据 → 进程 B 写入 → 进程 A 基于过期数据写入 → 进程 B 的更新丢失。

**修复建议**：`load()` 和 `save()` 使用同一把 FileLock，或在 load 时获取共享锁。

### P0-3. llm/model_manager.py: `get_active_model_info` 永远返回空 api_key

**文件**：`llm/model_manager.py:142-162`

```python
def get_active_model_info(self, role: str) -> Dict[str, Any]:
    ...
    config = self.get_active_model_config(role)  # 默认 sensitive=False
    ...
    "api_base_url": config.get("api_base_url", ""),  # 但 api_key 已被 strip！
```

第 150 行调用 `get_active_model_config(role)` 未传 `sensitive=True`，第 88 行 `config.pop("api_key", None)` 会移除 api_key。导致该方法返回的信息中 `api_base_url` 有值但永远看不到对应的 api_key，误导调用者以为模型未配置密钥。

**修复建议**：`get_active_model_info` 传入 `sensitive=True`，或在该方法中单独处理敏感字段。

### P0-4. app/transcribe_meeting/pipeline.py: 表达式结果丢弃（死代码 Bug）

**文件**：`app/transcribe_meeting/pipeline.py:62`

```python
date_part if date_part else time.strftime("%Y%m%d")
```

这是一个**独立表达式**，结果被计算后完全丢弃——没有任何赋值！当 `date_part` 为空字符串时，当前日期永远不会被填入。由于下游 `date_part` 仍然为空，会导致文件名格式异常。

**修复建议**：改为 `date_part = date_part if date_part else time.strftime("%Y%m%d")`。

### P0-5. ingest/scanner.py: PDF 标题提取 finally 块可能 NameError

**文件**：`ingest/scanner.py:270-291`

```python
try:
    doc = fitz.open(str(path))  # 如果这里抛异常，doc 未定义
    ...
finally:
    doc.close()  # NameError: name 'doc' is not defined
```

`fitz.open()` 若抛出异常，`doc` 变量未绑定，`finally` 块中的 `doc.close()` 引发 `NameError`，掩盖原始异常。

**修复建议**：`doc = None` 预声明 + `if doc: doc.close()`。

### P0-6. complex_input/pdf_adapter.py: 同样的 finally 块问题（两处）

**文件**：`complex_input/pdf_adapter.py:96,148` 和 `:162,178`

与 P0-5 相同的模式，`fitz.open()` 在 try 块外，finally 中 `doc.close()` 可能 `NameError`。

### P0-7. retrieval/vector_index.py: 空索引搜索引发 StopIteration

**文件**：`retrieval/vector_index.py:141`

```python
def _ensure_matrix(self) -> None:
    dim = len(next(iter(self._data.values())))
```

如果 `self._data` 为空（索引刚创建、尚未添加数据），`next(iter(...))` 抛出 `StopIteration`，导致搜索崩溃。

**修复建议**：在 `_ensure_matrix()` 开头检查 `if not self._data: return`，调用方处理空索引情况。

### P0-8. feed/_chat_fetcher.py: 获取失败与无新消息无法区分

**文件**：`feed/_chat_fetcher.py:88-90`

```python
except Exception as e:
    logger.error("获取消息失败 [%s]: %s", chat_id, e)
    return []
```

返回空列表。`feed_pipeline.py:122-126` 中调用者无法区分「获取 API 失败」与「确实没有新消息」，两种情况都会产生 `PipelineResult.empty("没有新消息")`，导致静默丢失数据。

**修复建议**：区分返回 `None`（错误）和 `[]`（空结果），或抛出专用异常由 pipeline 层决定策略。

### P0-9. usage_tracker.py: 空 rows 时 IndexError

**文件**：`app/cli/_handlers/_system.py:483,520`

```python
last_period = rows[-1]["period"]  # rows 为空时 IndexError
```

第 483 行的 `if not rows:` 守卫仅在 `args.pretty is True` 分支中，但第 520 行在 `else` 分支无条件执行。无数据时必定崩溃。

**修复建议**：将 `if not rows` 检查提前到分支出之前。

---

## P1 — 重要设计问题（影响可维护性/可扩展性）

### P1-1. ConfigBundle 兼容 shim 未清理

6 个文件通过 `E402` (延迟导入规避循环依赖) 的 ruff 豁免暴露了设计问题：
- `app/cli/_handlers/_wiki.py`、`feishu/doc_convert.py`、`llm/provider.py`
- `memory/long_term.py`、`retrieval/planner.py`、`retrieval/vector_index.py`
- `wiki/_relation_extractor.py`、`wiki/navigation.py`、`wiki/term_extractor.py`

`ConfigBundle` 是个工厂函数却用大写命名（像类），返回 `ConfigBundleV2`，导致类型混淆。

**建议**：完成 ConfigBundleV2 迁移，移除所有 dict 兼容 shim，统一使用 Pydantic 属性访问。

### P1-2. ASR 向后兼容存根（5 个文件）应清理

- `wiki/asr_formatter.py`、`wiki/asr_hotwords.py`、`wiki/asr_prompt_optimizer.py`、`wiki/asr_version.py`
- `wiki/term_extractor.py`

这些文件仅发出 `DeprecationWarning` 然后 `from ... import *`。所有导入已迁移到 `wiki/asr/` 子包后即可删除。

### P1-3. `_extract_json_object` 重复定义 3 次

| 位置 | 行号 |
|------|------|
| `qa/memory_updater.py` | 493-506 |
| `memory/session_miner.py` | 292-305 |
| `wiki/generator.py` | 267-301 (相关逻辑) |

应提取到 `utils/llm_parsing.py` 统一使用。同理，markdown 代码块剥离逻辑在 `session_miner.py:188-193` 和 `memory_updater.py:213-220` 中也重复。

### P1-4. 文件锁应用不一致

| 模块 | 文件 | 有锁? |
|------|------|:---:|
| `memory/long_term.py` | profile/corrections JSON | 仅写入加锁 |
| `feed/_cursor_tracker.py` | `feed_cursors.json` | 无锁 |
| `feed/feed_config.py` | `feeds.json` | 无锁 |
| `feed/_dispatcher.py` | pending JSON | 无锁 |
| `feishu/_shared.py` | dedup index | 仅写入加锁 |
| `analysis/_biweekly_cache.py` | brief index | 有锁 |

**建议**：制定统一的文件 I/O 安全策略：所有 JSON 状态文件 → `atomic_write_json()` + `FileLock` 双保险。

### P1-5. `__init__.py` 中的静默导入失败

**文件**：`core/__init__.py:15-21`

```python
try:
    from .storage import ChunkStore, StorageError
except ImportError:
    ChunkStore = None  # type: ignore
```

如果 `storage.py` 有任何语法错误或依赖缺失，`ChunkStore` 被静默设为 `None`，后续调用 `None()` 才会报错，延迟且误导。

**建议**：至少记录 warning，或让 ImportError 直接传播（fail-fast）。

### P1-6. `qa/service.py`: 记忆更新文本直接拼接到 LLM 回答后

**文件**：`qa/service.py:85-89`

```python
response = QAResponse(..., answer=response.answer + "\n\n已同步记忆更新：\n" + update_text, ...)
```

在 LLM 生成答案后直接拼接记忆更新通知，LLM 从未"看到"或"批准"这段文本，影响语义一致性。

**建议**：记忆更新通知作为单独字段返回，由前端/CLI 层决定如何展示。

### P1-7. `analysis/service.py`: `_try_parse_json` 结果未做 None 检查

**文件**：`analysis/service.py:290-291`

```python
parsed = _try_parse_json(result.text)
directions = parsed.get("directions", [])  # parsed 可能为 None!
```

`_try_parse_json` 返回 `Optional[Dict]`，在 `None` 上调用 `.get()` 会抛出 `AttributeError`。

**修复建议**：`if parsed is None: return []` 或 `(parsed or {}).get(...)`。

### P1-8. 全局 `_circuit_breaker` 单例共享状态

**文件**：`llm/provider.py:56`

```python
_circuit_breaker = _CircuitBreaker()
```

所有 `EnvironmentConfiguredLLMProvider` 实例默认共享同一个熔断器。一个 provider 的失败会影响所有其他实例的熔断器状态。

**建议**：默认每实例独立熔断器，或至少通过工厂方法创建（ASR 已通过 `set_circuit_breaker()` 支持独立实例）。

### P1-9. `_is_deepseek_thinking_model` 定义但从未调用

**文件**：`llm/provider.py:559-562`

死代码，应删除或接入实际调用路径。

### P1-10. `llm/cache.py`: LRU 状态仅内存，重启后磁盘无限增长

**文件**：`llm/cache.py:71,177`

LRU 驱逐列表仅在内存中维护。进程重启后 `len(self._lru) == 0`，磁盘缓存文件不会被主动清理。长期运行下磁盘空间持续增长。

**建议**：启动时扫描缓存目录，基于文件 mtime 重建 LRU 或定期清理过期条目。

---

## P2 — 优化建议（影响性能/用户体验）

### P2-1. 大量硬编码值应配置化

| 值 | 文件 | 行号 | 建议配置键 |
|---|---|---|---|
| `_DEFAULT_TEMPERATURE = 0.2` | `llm/provider.py` | 15 | llm.json |
| BM25 `K1=1.5, B=0.75` | `retrieval/searcher.py` | 23-25 | retrieval.json |
| Embedding cache `MAXSIZE=128, TTL=600` | `retrieval/embedder.py` | 12-13 | retrieval.json |
| Boost 系数 `2.0, 1.8, 1.2` | `retrieval/enhanced.py` | 28-30 | retrieval.json |
| `STALE_DAYS_THRESHOLD = 30` | `wiki/_constants.py` | 36 | wiki.json |
| `LINT_STALE_DAYS = 90` | `wiki/_constants.py` | 38 | wiki.json |
| `_LISTEN_WINDOW_SEC = 3.0` | `wiki/asr/corrector.py` | 52 | asr_profiles.json |
| `_POLL_INTERVAL = 0.2` | `wiki/asr/corrector.py` | 55 | asr_profiles.json |
| `_LOCK_TIMEOUT = 30` | `core/locks.py` | 24 | app.json |
| `_MAX_LOG_SIZE_BYTES = 10MB` | `utils/logging.py` | 17 | app.json |
| `_MAX_IMAGE_BYTES = 20MB` | `complex_input/detector.py` | 28 | app.json |
| `_DEFAULT_MAX_RENDER_PAGES = 5` | `complex_input/pdf_adapter.py` | 23 | app.json |

### P2-2. deep_eval 顺序 LLM 调用缺乏批处理

**文件**：`evaluation/deep_eval.py:110-198`

每个引用的验证都是一次串行 LLM API 调用。500 个引用的页面 = 500 次顺序 HTTP 请求。

**建议**：按每批 10-20 条引用并发发出 LLM 调用（独立引用间无依赖）。

### P2-3. 飞书 API 子进程模式的性能瓶颈

**影响文件**：`feishu/client.py`、`feed/_feishu_bridge.py`

每次飞书 API 调用 = `fork + exec lark-cli + 进程间 JSON 序列化`。对于高频操作（`batch_enrich_messages` 50条/批、`fetch_all_messages` 逐页），延迟累积显著。

**建议**：评估是否可在 lark-cli 中支持批量操作，或迁移到直接 HTTP SDK（长期）。

### P2-4. `memory/long_term.py` 纠正规则匹配用子串而非全词

**文件**：`memory/long_term.py:182`

```python
if concept.lower() in query_lower:  # "ABC" 匹配 "AABCC"
```

应改为分词或词边界匹配，减少误匹配。

### P2-5. `ingest/watcher.py` 每次回调创建新的 SourceWatcher

**文件**：`ingest/watcher.py:210-216`

文件变更回调每次都实例化完整的 `SourceWatcher(config)`，仅为了访问 `_sources` 属性。应复用实例或提取轻量级访问器。

### P2-6. `retrieval/searcher.py` BM25 缓存签名用 mtime 容忍度 0.01s

**文件**：`retrieval/searcher.py:146`

```python
abs(cached.get("index_mtime", 0) - index_mtime) < 0.01
```

FAT32/NFS 等文件系统 mtime 精度为 1 秒，0.01s 容忍度会导致频繁的误判（认为缓存未过期而反复重建）。

### P2-7. `app/cli/_handlers/_wiki.py`: asr-audit 输出路径与 build-asr-prompt 不一致

**文件**：`app/cli/_handlers/_wiki.py:235,337,369` vs `:608`

`build-asr-prompt` 输出到 `output/`，`asr-audit` 在 `output/asr-modify/` 查找。审计命令几乎必定找不到热词文件。

### P2-8. `wiki/asr/corrector.py`: 长 ASR 文本（>500 字符）发送 >500 次 Delete 按键

**文件**：`wiki/asr/_clipboard_io.py:70-83`

AppleScript 循环发送 `repeat {raw_length} times` 次 Delete 击键，速度慢且不可靠。应使用 `CMD+A` → `CMD+V` 或直接通过剪贴板 API 替换。

### P2-9. `ingest/pdf_extractor.py`: 字号阈值硬编码

**文件**：`ingest/pdf_extractor.py:131-138`

字号 16/13/12/11 的硬编码阈值对不同 PDF 生成器适应性差。14pt 粗体的 H1 会被误判为 H2。

---

## P3 — 代码质量/清理（影响可读性/一致性）

### P3-1. 模块级导入泄漏（函数体内 import）

4 处函数级 import 违反 PEP 8：

| 文件 | 行号 | 导入内容 |
|------|------|----------|
| `feed/_feishu_bridge.py` | 151, 162 | `re`, `datetime` |
| `feed/_dispatcher.py` | 64 | `re` |
| `feishu/client.py` | 52 | `random`, `time` |
| `feishu/chat_digest.py` | 234 | `sys` |

### P3-2. 通配 `except Exception` 掩盖调试信息

**高频位置**：
- `qa/memory_updater.py:145,181,431,439` — 记忆更新
- `wiki/asr/corrector.py:164,181,353,374,819,839,1371` — ASR 校正
- `feed/_chat_fetcher.py:88` — 消息获取
- `app/cli/_handlers/_system.py:219` — Wiki 维护

建议至少记录 `logger.debug("...", exc_info=True)` 以保留堆栈信息。

### P3-3. `utils/metrics.py` 年份过滤硬编码 `"202"`

**文件**：`utils/metrics.py:70`

```python
p.stem.startswith("202")
```

2099 年后失效。使用 `re.match(r"\d{4}-W\d{2}", p.stem)` 更健壮。

### P3-4. `llm/cache.py` 和 `core/locks.py` 使用 `Path.unlink(missing_ok=True)`

要求 Python 3.8+（项目要求 3.9+，无问题，但早期开发中可能引入假设）。

### P3-5. `wiki/asr/feedback.py` 有不必要的 `import os`

`os` 已导入但代码中未使用。

### P3-6. `config/loader.py` `resolve_env_vars` 的 `seen` 参数从未使用

**文件**：`config/loader.py:93`

`seen` 参数声明但从未被填充（注释说防止递归但未实现），死参数。

### P3-7. `config/workspace.py:138` 死代码

```python
bundle if isinstance(bundle, dict) else None  # 计算结果被丢弃
```

### P3-8. `wiki/_graph_engine.py` NetworkX/纯 Python 双路径语义差异

**文件**：`wiki/_graph_engine.py`

`bridges()` 纯 Python 路径内部变量遮蔽、`neighbors()` 纯 Python 路径多出 `visited.discard(node_id)` 但 NetworkX 路径没有。

### P3-9. `output/formatter.py` `_fmt_process` 键名不匹配

**文件**：`output/formatter.py:134-141`

查找 `stage1_output` / `stage2_output`，但 `complex_input/pipeline.py` 中的实际键名是 `stage1_prompt` / `stage2_output`。`--pretty` 输出总是显示空白。

### P3-10. `wiki/searcher.py` 模块级可变缓存无清理机制

**文件**：`wiki/searcher.py:69`

`_WIKI_CACHE` 是模块级可变字典，在多个 `WikiSearcher` 实例间共享，且永不过期（只能通过重新导入模块刷新）。

### P3-11. `llm/usage_tracker.py`: 每次 `record()` 打开新 SQLite 连接

**文件**：`llm/usage_tracker.py:145`

高频调用下效率低。应使用持久连接 + 定期 flush。

### P3-12. `core/thread_pool.py`: 访问私有属性 `_work_queue`

**文件**：`core/thread_pool.py:97-101`

`stats()` 访问 `ThreadPoolExecutor._work_queue`（CPython 实现细节），PyPy/未来版本可能失效。

### P3-13. `core/script_loader.py`: 污染 `sys.modules`

**文件**：`core/script_loader.py:43`

加载脚本后未清理 `sys.modules` 中的注册项，可能影响后续同名模块导入。

---

## 测试覆盖缺口

基于代码审查识别的未测试/欠测试区域：

1. **文件 I/O 竞态**：FileLock 交错读/写场景
2. **损坏 JSON 恢复**：多个 `load()` 函数对损坏文件无容错
3. **PDF 提取边缘情况**：损坏 PDF、非标准字号、无文字 PDF
4. **CGEventTap 回调**：`corrector.py` 的 macOS 原生代码路径
5. **LLM 降级链**：`_fallback_loop` 的各种失败组合
6. **向量索引维度不匹配**：embedding 模型切换场景
7. **会话挖掘空结果**：`mine_and_promote()` 返回 `{"mined": False}`
8. **`_graph_engine.py` 纯 Python 回退路径**：与 NetworkX 路径的语义差异
9. **`_clipboard_io.py` macOS 原生操作**：子进程调用未 mock
10. **Trello 自定义 DNS 解析**：`dig` 命令依赖

---

## 测试覆盖分析

### 整体情况

- 测试文件 108 个（含 conftest），~21,191 行代码
- 单元测试目录 `tests/unit/` 31 文件 · 集成测试目录 `tests/integration/` 12 文件
- 覆盖率阈值：`fail_under = 53` · 实际约 60%+
- 测试组织清晰，auto-mark 机制成熟（`pytest_collection_modifyitems` 自动为 unit/integration 打标记）

### 零测试的模块（最大缺口）

**严重** — `feed/` 包 11 个文件**完全没有测试**：

| 文件 | 功能 |
|------|------|
| `feed_pipeline.py` | 信息汇聚主编排 |
| `_chat_fetcher.py` | 飞书消息获取 |
| `_topic_detector.py` | LLM 话题检测 |
| `_brief_generator.py` | 简报 Markdown 生成 |
| `_dispatcher.py` | 分发与通知 |
| `_message_filter.py` | 噪音过滤 |
| `_cursor_tracker.py` | 游标持久化 |
| `_okr_loader.py` | OKR 文档解析 |
| `_feishu_bridge.py` | lark-cli 桥接 |
| `feed_config.py` | 配置文件管理 |
| `_types.py` | 数据模型 |

**高** — 基础工具模块无测试：
- `utils/paths.py`、`utils/logging.py`、`utils/prompting.py`、`utils/llm_parsing.py`、`utils/template_loader.py`
- `core/memory_cache.py`、`core/async_http.py`、`core/agent_adapter.py`

**中** — CLI handler 5 个文件仅有间接测试（通过 `test_cli_handlers.py`），无直接单元测试
- `wiki/_relation_extractor.py`、`wiki/_wiki_io.py`
- `llm/model_manager.py`、`trello/llm.py`

### 测试质量问题

- `test_person_enricher.py`（46 行）仅测试 dataclass，未覆盖实际的飞书 API 调用和页面更新逻辑
- `test_core.py`（49 行）覆盖 3 个不相关模块，范围过窄
- `wiki/_graph_engine.py` 纯 Python 回退路径与 NetworkX 路径语义差异未被测试捕获（bridges/neighbors 行为不一致）

---

## 跨模块共性问题统计

| 类别 | 数量 | 关键示例 |
|------|:---:|----------|
| 非原子文件写入 | 4 | cursor_tracker, feed_config, dispatcher, dedup index |
| 读/写锁不匹配 | 5 | long_term(x2), _shared(x2), searcher cache |
| 重复工具函数 | 4 | _extract_json_object, strip_code_fence, atomic_write, _safe_format |
| 硬编码值应配化 | 12 | BM25参数、缓存大小、ASR阈值、PDF字号等 |
| 函数体内 import | 4 | _feishu_bridge, _dispatcher, client, chat_digest |
| 通配 except 吞异常 | 12+ | memory_updater, corrector, _chat_fetcher 等 |
| 向后兼容存根 | 7 | ASR shim x4, term_extractor, ConfigBundle factory, E402 豁免 |

---

## 建议的优化路线图

### 第一阶段：修复 P0 缺陷（1-2 周）
1. 全部 JSON 状态文件迁移到 `atomic_write_json()`
2. 统一 `load/save` 加锁策略
3. 修复 P0-3 ~ P0-9 的具体 Bug

### 第二阶段：P1 设计改进（2-4 周）
1. 完成 ConfigBundleV2 迁移，移除所有兼容 shim
2. 提取共享工具函数消除重复
3. 清理 ASR 向后兼容存根
4. 建立文件 I/O 安全规范

### 第三阶段：P2 优化（按需推进）
1. 核心硬编码值迁移到配置文件
2. deep_eval 并发化
3. 纠正规则的分词匹配
4. 飞书 API 调用性能优化

### 第四阶段：P3 清理（持续）
1. 模块级 import 规范化
2. 异常处理精细化
3. 死代码清理
4. 测试覆盖率从 60% → 70%

---

> 本报告由 Claude Code 6-Agent 并行审查生成。
> 各 Agent 原始分析记录：`subagents/agent-*.jsonl`
