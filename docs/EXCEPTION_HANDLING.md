# 异常处理边界与最佳实践

本文档说明 Iris 项目中异常处理的设计策略与使用边界。

---

## 统一异常体系

### 异常层级

```
IrisError(Exception)                          # 根异常
├── IrisRuntimeError(IrisError, RuntimeError)  # 运行期错误
│   ├── LLMProviderError                      # LLM 调用失败
│   ├── StorageError                          # SQLite/索引错误
│   ├── FeishuClientError                     # 飞书 API 错误
│   ├── TrelloClientError                     # Trello API 错误
│   ├── EmbedderError                         # Embedding 错误
│   └── ...
└── IrisValueError(IrisError, ValueError)      # 输入/配置错误
    ├── ConfigError                           # 配置文件错误
    ├── ValidationError                       # 数据校验错误
    └── ...
```

### 多继承设计

所有自定义异常同时继承 `IrisError` 和对应的标准库异常类：

```python
class ConfigError(IrisValueError):
    """配置文件不合法时抛出。"""

# 调用方可以选择：
try:
    config = load_config()
except IrisError:      # 统一捕获所有 Iris 异常
    ...

try:
    config = load_config()
except ValueError:     # 向后兼容标准库异常
    ...
```

**好处**：
- 新代码可统一 `except IrisError` 处理
- 旧代码 `except RuntimeError` 仍兼容
- IDE 类型推断更准确

---

## 异常处理策略

### 1. 关键路径：收窄异常类型

**原则**：关键路径（配置加载、索引构建、文件处理）必须使用具体异常类型，便于诊断。

#### 配置加载

```python
# ✅ 好的实践
try:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
except json.JSONDecodeError as exc:
    raise ConfigError(f"JSON 解析失败: {path} -> {exc}") from exc
except OSError as exc:
    raise ConfigError(f"配置文件读取失败: {path} -> {exc}") from exc
```

**捕获类型**：
- `json.JSONDecodeError` - JSON 格式错误
- `OSError` - 文件不存在、权限不足等
- `UnicodeDecodeError` - 编码问题

#### 文件操作

```python
# ✅ 好的实践
try:
    text = md_file.read_text(encoding="utf-8")
except OSError as exc:
    logger.warning("读取文件失败: %s -> %s", md_file, exc)
    continue
except UnicodeDecodeError as exc:
    logger.warning("文件编码错误: %s -> %s", md_file, exc)
    continue
```

#### 网络请求

```python
# ✅ 好的实践（Trello 客户端）
try:
    result = subprocess.run(["dig", "+short", host], ...)
except (subprocess.SubprocessError, OSError) as exc:
    logger.debug("DNS 解析失败: %s", exc)
    # 回退到系统 DNS
```

---

### 2. 辅助功能：允许宽泛捕获

**原则**：辅助功能（用量统计、缓存、可选依赖）失败不影响主流程，可使用 `except Exception`。

#### 用量统计

```python
# ✅ 合理的宽泛捕获
def _record_usage(self, ...):
    """记录 LLM 用量（静默失败）。"""
    if self._tracker is None:
        try:
            from iris.llm.usage_tracker import UsageTracker
            self._tracker = UsageTracker(self._data_dir)
        except Exception:
            return  # 初始化失败，放弃统计
    
    try:
        self._tracker.record(...)
    except Exception:
        pass  # 用量记录失败不影响 LLM 调用
```

**适用场景**：
- 用量统计/日志记录
- 缓存加载/保存（失败回退重建）
- 可选功能初始化（Keychain、可选依赖）
- 降级策略（LLM Provider 初始化失败降级）

#### 缓存加载

```python
# ✅ 合理的宽泛捕获
try:
    cached = json.loads(cache_path.read_text())
    return cached
except Exception:
    pass  # 缓存失败，重新计算
```

#### 可选依赖

```python
# ✅ 合理的宽泛捕获
try:
    from iris.config.secrets import get_secret
    val = get_secret(var_name)
    if val is not None:
        return val
except (ImportError, OSError) as exc:
    logger.debug("Keychain 查找失败: %s", exc)
```

---

### 3. 混合场景：分层捕获

**原则**：外层宽泛捕获 + 内层具体捕获。

```python
# ✅ 分层捕获
def handle_asr_corrector(args, bundle, logger):
    # 外层：整体容错
    try:
        # 内层：关键路径具体捕获
        replace_dict, error = _load_asr_replace_dict(dict_path, args)
        if error:
            _emit_output("asr-corrector", {"error": error}, ...)
            return 1
        
        # 外层：辅助功能宽泛捕获
        try:
            service = LLMService(bundle)
            provider = service.get_provider()
        except Exception as e:
            logger.warning("LLM Provider 初始化失败，降级: %s", e)
            provider = None
        
        corrector.run_forever()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.exception("ASR 校正器异常退出")
        return 1
```

---

## 异常传播与转换

### 链式异常（推荐）

```python
# ✅ 保留原始异常
try:
    data = json.load(file)
except json.JSONDecodeError as exc:
    raise ConfigError(f"JSON 解析失败: {path}") from exc
```

### 静默失败（需文档化）

```python
# ✅ 静默失败 + 注释说明
try:
    self._tracker.record(...)
except Exception:
    pass  # 用量记录失败不影响主流程
```

### 降级策略（需日志）

```python
# ✅ 降级 + 日志
try:
    provider = service.get_provider()
except Exception as e:
    print(f"[warn] LLM Provider 初始化失败: {e}", file=sys.stderr)
    print("[warn] 将降级为 fast 模式", file=sys.stderr)
    mode = "fast"
```

---

## pyproject.toml 配置

### 允许宽泛捕获

```toml
[tool.ruff.lint.per-file-ignores]
# BLE001: Do not catch blind exception
# 项目架构依赖外部服务（LLM / 飞书 / 文件 I/O），
# 宽泛捕获是有意的容错策略
"src/iris/**/*.py" = ["BLE001"]
```

---

## 常见异常类型清单

### 标准库异常

| 异常类型 | 使用场景 | 示例 |
|---------|---------|------|
| `OSError` | 文件操作失败 | 文件不存在、权限不足 |
| `json.JSONDecodeError` | JSON 格式错误 | 配置文件损坏 |
| `UnicodeDecodeError` | 编码问题 | 非 UTF-8 文件 |
| `ValueError` | 参数非法 | 类型转换失败 |
| `TypeError` | 类型错误 | None 调用方法 |
| `KeyError` | 字典键缺失 | 配置字段缺失 |
| `subprocess.SubprocessError` | 子进程失败 | dig/pbcopy 失败 |
| `urllib.error.URLError` | 网络请求失败 | API 调用超时 |

### Iris 自定义异常

| 异常类型 | 继承 | 使用场景 |
|---------|------|---------|
| `ConfigError` | `IrisValueError` | 配置文件不合法 |
| `LLMProviderError` | `IrisRuntimeError` | LLM 调用失败 |
| `StorageError` | `IrisRuntimeError` | SQLite 错误 |
| `WriteGuardError` | `IrisError, PermissionError` | 写入路径越界 |
| `FeishuClientError` | `IrisRuntimeError` | 飞书 API 失败 |
| `TrelloClientError` | `IrisRuntimeError` | Trello API 失败 |
| `EmbedderError` | `IrisRuntimeError` | Embedding 失败 |

---

## 反模式

### ❌ 关键路径宽泛捕获

```python
# ❌ 不好：配置加载失败难以诊断
try:
    config = json.load(file)
except Exception:
    return {}
```

### ❌ 无日志静默失败

```python
# ❌ 不好：错误被完全掩盖
try:
    important_operation()
except Exception:
    pass  # 为什么静默？影响什么？
```

### ❌ 吞掉 KeyboardInterrupt

```python
# ❌ 不好：无法 Ctrl+C 中断
try:
    while True:
        work()
except Exception:  # 捕获了 KeyboardInterrupt！
    pass
```

应该：

```python
# ✅ 好：显式排除 KeyboardInterrupt
try:
    while True:
        work()
except KeyboardInterrupt:
    raise
except Exception:
    pass
```

---

## 最佳实践总结

1. **关键路径收窄**：配置/索引/文件处理使用具体异常
2. **辅助功能宽泛**：用量统计/缓存/可选依赖可静默
3. **保留异常链**：使用 `raise ... from exc`
4. **降级需日志**：降级策略必须告知用户
5. **静默需注释**：解释为什么静默、影响什么
6. **测试错误分支**：为收窄的异常添加单元测试

---

**参考**：
- `src/iris/core/exceptions.py` - 异常体系定义
- `src/iris/config/loader.py` - 配置加载示例
- `src/iris/llm/usage_tracker.py` - 辅助功能示例
- `pyproject.toml` - Ruff 配置

---

冯扬  
2026-09-09
