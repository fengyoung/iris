# Phase 2 优化报告：加固 — 架构债务消除

> 执行日期：2026-07-28 · 回归测试：1977 passed / 0 failed

---

## 修复清单

### 2.1 ASR 向后兼容存根清理（5 文件删除）

删除以下仅含 `DeprecationWarning` + `from ... import *` 的存根文件：

- `wiki/asr_formatter.py`（→ `wiki/asr/formatter.py`）
- `wiki/asr_hotwords.py`（→ `wiki/asr/hotwords.py`）
- `wiki/asr_prompt_optimizer.py`（→ `wiki/asr/prompt_optimizer.py`）
- `wiki/asr_version.py`（→ `wiki/asr/version.py`）
- `wiki/term_extractor.py`（→ `wiki/asr/` 子包）

**迁移动作**：
- `app/cli/_handlers/_wiki.py:180` — `from iris.wiki.term_extractor` → `from iris.wiki.asr`
- `tests/test_asr_hotwords.py:17` — 同上
- `tests/test_term_extractor.py:12` — 同上
- `wiki/asr/extractor.py` — docstring 更新为新导入路径

### 2.2 工具函数去重（2 处重复消除）

#### `extract_json_object` 去重

| 之前 | 之后 |
|------|------|
| `qa/memory_updater.py:493-506` 本地定义 | 导入 `iris.utils.llm_parsing.extract_json_object` |
| `memory/session_miner.py:292-305` 本地定义 | 同上 |

**收益**：消除 ~25 行重复代码，统一 JSON 提取逻辑到 `utils/llm_parsing.py`。

### 2.3 feed 包测试补齐（16 用例新增）

新增 `tests/unit/test_feed_core.py`，覆盖 feed 包 3 个核心模块：

| 测试类 | 覆盖模块 | 用例数 |
|--------|---------|:---:|
| `TestMessageFilter` | `_message_filter.py` | 9 |
| `TestCursorTracker` | `_cursor_tracker.py` | 8 |
| `TestPipelineResult` | `_types.py` | 2 |

覆盖场景：空输入、短消息过滤、噪音模式、系统消息、链接纯转发、混合会话、游标持久化、损坏文件恢复、原子写入验证。

---

## 回归测试结果

```
Phase 1 基线：1961 passed
Phase 2 新增：  16 passed (tests/unit/test_feed_core.py)
─────────────────────────────
Total:         1977 passed / 0 failed
```

---

## 待完成项（后续阶段）

- **ConfigBundleV2 迁移**：6 个文件的 `E402` 豁免（延迟导入规避循环依赖）需逐一重构
- **feed 包剩余模块测试**：`_topic_detector.py`、`_brief_generator.py`、`_okr_loader.py` 等 8 个文件仍需测试

---

## 下一阶段

Phase 3 — 优化：核心硬编码值配置化、deep_eval 并发化
