# 第 1/4 项优化报告：ConfigBundleV2 迁移收尾

> 执行日期：2026-07-28 · 回归测试：1977 passed / 0 failed

---

## 方案规划

**问题诊断**：
- `ConfigBundle` 是 `loader.py` 中定义的**工厂函数**（接受原始 dict，返回 `ConfigBundleV2`）
- 但 30+ 个文件将其用作**类型注解**（`config: ConfigBundle`），运行时正常（Python 忽略类型注解），但语义错误
- `retrieval/enhanced.py:108-122` 存在 try/except/fallback 兼容 shim，增加了不必要的复杂度

**改造方案**：
```
改造前：                             改造后：
ConfigBundle(root, app, ...)    →    make_config_bundle(root, app, ...)
  # 大写命名的工厂函数                  # 动词短语，语义清晰

无类型别名：                          ConfigBundle = ConfigBundleV2
  # 函数无法作为类型注解                  # 真正的 Pydantic 模型引用
```

---

## 代码变更

### 1. `config/loader.py` — 重命名 + 类型别名

```python
# 工厂函数：ConfigBundle → make_config_bundle
def make_config_bundle(root, app, data_source, llm, ...) -> ConfigBundleV2:
    ...

# 新增：向后兼容类型别名
ConfigBundle = ConfigBundleV2
```

### 2. `pyproject.toml` — 移除无效豁免

`src/iris/wiki/term_extractor.py` 的 E402 豁免已移除（文件在 Phase 2 中删除）。

### 3. `retrieval/enhanced.py` — 简化兼容 shim

移除 try/except/fallback 三段式兼容逻辑（~10 行），统一使用 `config.app.get("retrieval", {})`。

### 4. 测试文件 — 工厂调用迁移（5 文件）

| 文件 | ConfigBundle() → make_config_bundle() |
|------|:---:|
| `tests/test_biweekly_pipeline.py` | 6 处 |
| `tests/test_memory_manager.py` | 3 处 |
| `tests/test_provider_fallback.py` | 2 处 |
| `tests/integration/test_graph.py` | 26 处 |

---

## 优化确认

- [x] `ConfigBundle` 现在是 `ConfigBundleV2` 的类型别名（`is` 检查通过）
- [x] 所有测试通过（1977 passed）
- [x] `pyproject.toml` 中 `term_extractor.py` 豁免已移除
- [x] `retrieval/enhanced.py` 兼容 shim 简化为单一路径
- [x] 源文件中 30+ 个 `config: ConfigBundle` 类型注解不动——运行时语义不变
