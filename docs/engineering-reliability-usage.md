# Iris 工程可靠性使用指南

> 适用版本：Iris 3.28.1+

## 环境基线

```bash
python3 --version                 # 需要 3.11+
python3 -m pip install -e ".[dev]" -c constraints.txt
iris check-config
```

从仓库外、launchd 或其他自动化环境启动时，显式设置项目根：

```bash
export IRIS_PROJECT_ROOT=/absolute/path/to/iris3
iris workspace current
```

`workspace current` 应返回该目录；`workspace list` 可查看已发现的工作空间。

## 配置迁移

### 升级到 v3.28.1

**LLM 思考文本污染修复（无需操作，行为说明）**：provider 层不再把思考过程（`reasoning_content`）当最终输出返回——`content` 为空（如思考模型 max_tokens 耗尽）时直接抛错，走各业务路径的重试/降级链。此前双周报 Stage 4b 质量审查曾把 13k 思考字符直接写入归档文件，升级后此类场景表现为「该阶段失败并回退组装稿」而非「污染产物」，属预期行为。

**索引数据止损（需手动重建一次）**：v3.28.1 修复了增量切块与向量索引增量的正确性缺陷（未变更 chunk 丢失、编辑文档旧向量、死向量残留），代码修复只防新增，存量数据需手动重建一次：

```bash
iris build-chunks --write-summary
iris build-vector-index --force-rebuild
```

重建后新索引的 `ids.json` 带 `doc_hashes` 字段，后续增量按源文档 hash 精准判定重嵌。

### 写入守卫

`config/app.json` 的写入守卫推荐配置为：

```json
{
  "safety": {
    "enforce_write_guard": true,
    "allowed_write_paths": [
      "${IRIS_OUTPUT_DIR}",
      "./temp",
      "./memory",
      "./data"
    ]
  }
}
```

旧字段 `deny_write_outside_allowed_paths` 暂时兼容，无需立即修改运行配置。新配置应只使用 `enforce_write_guard`。如临时关闭守卫，确认调用目标是明确路径；关闭守卫不会关闭原子写保护。

## 开发约定

新增文件写入时按场景选择：

| 需求 | 使用方式 |
|------|---------|
| 普通文本/JSON/二进制覆盖 | `atomic_write_text/json/bytes` |
| 受配置约束的输出 | `safe_write_text/bytes` |
| 共享 JSON 的读-改-写 | `with FileLock(path)` 包住完整 RMW，再原子写 |
| 多文件索引或模型制品 | generation 写全后原子切换指针 |
| SQLite 一次性读取 | `with ChunkStore(path) as store` |

不要在释放 `FileLock` 后删除 `.lock` 文件。看到长期存在的 `.lock` 文件属于正常状态，不应手动清理。

## 提交前验证

```bash
make lint
pytest -q
git diff --check
pip-audit
```

精确审计项目约束时，应基于 `constraints.txt` 和项目声明判断结果。不要把当前机器全局环境中与 Iris 无关的包漏洞计入项目结论。

涉及锁、缓存、SQLite 或多文件制品的改动，还应运行对应专项测试：

```bash
pytest -q tests/test_file_lock.py tests/test_llm_cache_lru.py \
  tests/test_vector_index.py tests/integration/test_storage.py
```

## 故障排查

### 项目根解析错误

```bash
IRIS_PROJECT_ROOT=/absolute/path/to/iris3 iris workspace current
IRIS_PROJECT_ROOT=/absolute/path/to/iris3 iris check-config
```

确认变量指向包含 `pyproject.toml`、`config/` 和 `src/iris/` 的仓库根目录。

### 向量索引加载失败

检查对应索引目录中的 `current.json` 是否指向存在的 `generations/<id>/`，且该目录同时包含 `vectors.npy`、`ids.json`、`meta.json`。不要手工把单个文件从一个 generation 混入另一个 generation；必要时使用既有重建命令完整重建索引。

### 缓存持续膨胀

缓存初始化时会按 TTL 和容量自动清理。先确认没有运行中的 Iris 进程，再检查缓存配置；不要在进程运行期间删除 `.lock` 文件。缓存内容可重新生成，但删除前应保留诊断信息以定位 TTL 或容量配置问题。

### SQLite 文件无法删除或替换

确认一次性调用方已经执行 `close()`，守护进程已正常退出。开发代码优先使用上下文管理器，避免等待垃圾回收释放连接。
