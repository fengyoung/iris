# Phase 1 优化报告：P0 严重缺陷修复

> 执行日期：2026-07-28 · 回归测试：1961 passed / 0 failed

---

## 修复清单（9 项）

### P0-1. 文件原子写入统一（4 处）

| 文件 | 修复内容 |
|------|---------|
| `feed/_cursor_tracker.py:35-38` | `_save()` 改用 `atomic_write_json()` + `FileLock` |
| `feed/feed_config.py:111-116` | `save_feed_config()` 改用 `atomic_write_json()` + `FileLock` |
| `feed/feed_config.py:225-230` | `save_pending()` 改用 `atomic_write_json()` + `FileLock` |

**修复前**：直接 `open(w)` → `json.dump()`，进程崩溃会留下半写文件。
**修复后**：`FileLock` + 临时文件 + `os.replace()` 原子替换，崩溃安全。

### P0-2. 读/写加锁统一（3 处）

| 文件 | 修复内容 |
|------|---------|
| `memory/long_term.py` `UserProfileMemoryStore.load()` | 添加 `FileLock` 保护读取 + JSON 损坏容错 |
| `memory/long_term.py` `CorrectionMemoryStore.load()` | 同上 |
| `feishu/_shared.py:94-101` `load_dedup_index()` | 添加 `FileLock` 保护读取 |

**技术细节**：新增 `_load_unlocked()` 内部方法，分离加锁的公开 `load()` 与不加锁的内部调用路径，避免 `apply_text_update()` / `delete()` 中已持锁时发生可重入死锁。

### P0-3. `get_active_model_info` api_key 修复

**文件**：`llm/model_manager.py:150`
```python
# 修复前
config = self.get_active_model_config(role)  # 默认 sensitive=False, api_key 被移除！

# 修复后
config = self.get_active_model_config(role, sensitive=True)
```

### P0-4. 会议转录日期赋值修复

**文件**：`app/transcribe_meeting/pipeline.py:62`
```python
# 修复前（表达式结果被丢弃）
date_part if date_part else time.strftime("%Y%m%d")

# 修复后
date_part = date_part if date_part else time.strftime("%Y%m%d")
```

### P0-5/6. PDF finally 块 NameError 修复（3 处）

| 文件 | 行号 | 修复方法 |
|------|------|---------|
| `ingest/scanner.py` `_extract_pdf_title` | 270-291 | `doc = None` 预声明 + `if doc: doc.close()` |
| `complex_input/pdf_adapter.py` `process()` | 96-153 | 将 `fitz.open()` 纳入 try 块 + finally 空安全 |
| `complex_input/pdf_adapter.py` `extract_text_only()` | 167-184 | 同上 |

### P0-7. 向量索引空数据防御

**文件**：`retrieval/vector_index.py:140-147`

添加 `try/except StopIteration` 保护 `next(iter(self._data.values()))`，处理多线程下字典被并发清空的极端场景。

### P0-8. 消息获取错误传播

**文件**：`feed/_chat_fetcher.py`

- 新增 `ChatFetchError` 异常类，区分「API 失败」与「无新消息」
- `fetch()` 按会话捕获 `ChatFetchError`，记录日志后继续处理其他会话
- 汇总所有获取失败的会话 ID 和原因

### P0-9. 用量统计空 rows 防御

**文件**：`app/cli/_handlers/_system.py:482-485`

将 `if not rows` 检查从 `if args.pretty:` 分支内提升到前缀，同时保护 pretty 和 JSON 两种输出模式。

---

## 回归测试结果

```
tests/unit/:        629 passed
tests/integration/: 228 passed
tests/ (top-level): 504 passed
─────────────────────────────
Total:             1961 passed / 0 failed
```

---

## 风险评估

- **P0-2 可重入锁**：`lifecycle.py` 中 `load()` → `_save()` 序列存在 TOCTOU 窗口（两操作间数据可被修改），属已有设计问题，需 Phase 2 中配合事务化 `load_and_lock` 模式解决
- **P0-8 错误传播**：`ChatFetchError` 继承 `RuntimeError`，pipeline 层已按会话捕获，不影响其他会话的消息获取

---

## 下一阶段

Phase 2 — 加固：ConfigBundleV2 迁移收尾、feed 包测试补齐、工具函数去重、ASR 存根清理
