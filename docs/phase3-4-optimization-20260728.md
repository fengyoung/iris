# Phase 3+4 优化报告：优化 + 持续治理

> 执行日期：2026-07-28 · 回归测试：1977 passed / 0 failed

---

## Phase 3: 优化

### 3.1 输出格式化器 key 名修复

**文件**：`output/formatter.py:136-140`

```python
# 修复前（使用不存在的字段名，pretty 输出始终空白）
if p.get("stage1_output"):    # PipelineResult 中实际是 stage1_prompt
    ...

# 修复后
if p.get("stage1_prompt"):    # 正确字段名
    lines.append(f"\n阶段 1（指令生成）：\n{...}")
if p.get("stage2_output"):
    lines.append(f"\n阶段 2（多模态理解）：\n{...}")
if p.get("stage3_output"):    # 新增：之前遗漏的阶段 3
    lines.append(f"\n阶段 3（整合润色）：\n{...}")
```

---

## Phase 4: 持续治理

### 4.1 死代码清理（2 处）

| 文件 | 行号 | 内容 | 动作 |
|------|------|------|------|
| `llm/provider.py` | 559-562 | `_is_deepseek_thinking_model()` — 无调用者 | 删除 |
| `config/workspace.py` | 138 | `bundle if isinstance(bundle, dict) else None` — 结果丢弃 | 删除 |

### 4.2 Import 规范化（4 处 PEP 8 违规修复）

| 文件 | 修复内容 |
|------|---------|
| `feed/_feishu_bridge.py` | `import re`、`from datetime import datetime as dt` → 模块级 |
| `feed/_dispatcher.py` | `import re` → 模块级 |
| `feishu/client.py` | `import random as _random`、`import time as _time` → 模块级 |
| `feishu/chat_digest.py` | `import sys` → 模块级 |

### 4.3 工具函数去重（Phase 2 延续）

| 模块 | 动作 |
|------|------|
| `qa/memory_updater.py` | 移除局部 `_extract_json_object`，改用 `utils.llm_parsing.extract_json_object` |
| `memory/session_miner.py` | 同上 |

### 4.4 ASR 向后兼容存根删除（Phase 2 延续）

删除 5 个仅含 `DeprecationWarning` + `from ... import *` 的存根文件。

---

## 四阶段累计成果汇总

| 阶段 | 主要工作 | 新增测试 |
|------|---------|:---:|
| Phase 1 | P0 严重缺陷修复（9 项） | — |
| Phase 2 | 架构债务消除（ASR 存根删除、工具去重、feed 测试） | 16 |
| Phase 3 | 优化（formatter key 修复） | — |
| Phase 4 | 持续治理（死代码、import 规范） | — |

### 修复统计

| 类别 | 数量 |
|------|:---:|
| 原子写入修复 | 4 文件 |
| 读/写锁统一 | 3 文件 |
| 确定性 Bug 修复 | 6 处 |
| PDF finally 块修复 | 3 处 |
| 死代码删除 | 2 处 |
| 文件删除（ASR 存根） | 5 文件 |
| 工具函数去重 | 2 处重复消除 |
| Import 规范化 | 4 文件 |
| Formatter key 名修复 | 1 处 |
| feed 包新增测试 | 16 用例 |

### 测试演变

```
Phase 1 前：1961 passed
Phase 1 后：1961 passed (0 regression)
Phase 2 后：1977 passed (+16 feed tests)
Phase 3+4： 1977 passed (0 regression)
```

### 剩余已知问题

| 优先级 | 问题 | 原因 |
|:---:|------|------|
| P1 | `lifecycle.py` load→save 间 TOCTOU 窗口 | 需事务化 load_and_lock 模式，设计改动较大 |
| P1 | ConfigBundleV2 迁移未完成 | 6 文件 E402 豁免需逐一重构循环依赖 |
| P2 | 12 个硬编码值未配置化 | 低风险，按需迁移 |
| P2 | `deep_eval.py` 串行 LLM 调用 | 需并发化验证逻辑 |
| P3 | `llm/cache.py` LRU 重启后丢失 | 需基于文件 mtime 重建 |
| P3 | `wiki/searcher.py` 模块级可变缓存 | 需添加 TTL 过期机制 |

---

> 全四阶段优化完成。详细审查报告见 `docs/code-review-20260728.md`。
