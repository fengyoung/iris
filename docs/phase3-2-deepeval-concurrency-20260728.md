# 第 2/4 项优化报告：deep_eval 引用校验并发化

> 执行日期：2026-07-28 · 回归测试：1977 passed / 0 failed

---

## 方案规划

**问题**：`AccuracyVerifier.verify()` 逐条串行调用 LLM 校验引用。500 条引用 = 500 次串行 API 调用。

**方案**：新增 `max_workers` 参数（默认 5），使用 `ThreadPoolExecutor` 并发化 `_verify_one()` 调用。

**关键设计决策**：
- `max_workers <= 1` 时保持串行路径（调试友好）
- 结果按原始索引收集再按序输出（不改变外部语义）
- 单条引用异常不影响其他引用（捕获后标记为 `verdict="error"`）
- 页面级兜底校验（`_verify_page_level`）仍在主线程串行执行（占比低，无需并行）

---

## 代码变更

**文件**：`evaluation/deep_eval.py:101-158`

```python
# 改造前
def verify(self, entries, wiki_title="", wiki_content=""):
    verdicts = []
    for entry in entries:          # 串行，500条 = 500次LLM调用
        verdict = self._verify_one(entry)
        ...
    return verdicts

# 改造后
def verify(self, entries, wiki_title="", wiki_content="", max_workers=5):
    if max_workers <= 1 or len(entries) <= 1:
        ...  # 串行路径

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 并发提交全部引用
        # 按原始索引收集结果
        # 页面级兜底校验（串行，占比低）
```

**预期性能提升**：500 条引用从 `~500s`（串行 1s/条）降至 `~100s`（5 并发）。

---

## 优化确认

- [x] 串行路径不变（`max_workers=1` 显式保留）
- [x] 结果顺序与输入一致
- [x] 单条异常不阻塞其余
- [x] 深度评估测试 52 passed
- [x] 全量回归 1977 passed
