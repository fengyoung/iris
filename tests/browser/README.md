# 谁是卧底浏览器回归

`undercover_assertions.js` 在完整页面 DOM 上测试视图显隐、稳定编号、事件筛选、复盘私有思考缓存、总结状态、历史筛选、保存反馈，以及参与模型区（裁判互斥 / 全选全取消 / 记住上次选择）。不调用真实模型。

新增断点恢复回归：安全整数种子的校验与提交一致（`1e3` 提交为 `1000`）、恢复保留淘汰状态、服务重启后实际调用恢复接口并按原手动模式连接事件流。当前共 26 项 DOM/交互断言；网络响应使用测试夹具，真实 HTTP、引擎续跑与持久化另由 Python 测试覆盖。

## 运行

```bash
pytest tests/test_browser_undercover.py -q
```

`tests/test_browser_undercover.py` 会把断言脚本注入页面副本、起一个本地 HTTP 服务提供页面，再用无头 Chrome 打开并检查 `data-test-result`。**本机没有 Chrome/Chromium 时跳过**，装上即可运行。

它同时跑在 CI 的集成测试步骤里（ubuntu runner 预装 `/usr/bin/google-chrome`）。工作流中另有一条显式断言：runner 上找不到 Chrome 就直接失败——否则这里会静默跳过，看着像有覆盖其实没有。

**为什么不用 `file://`**：「记住上次选择」一组断言依赖 `localStorage`，而 `file://` 的来源是不透明源，各浏览器对它的存储策略不一致；走 HTTP 既消除这层环境差异，也与真实用法一致。

## 手动调试

想在真实浏览器里逐步看，生成可独立打开的测试页：

```bash
python - <<'PY'
from pathlib import Path
html = Path('src/iris/games/templates/undercover_web.html').read_text()
html = html.replace('init();', '')
assertions = Path('tests/browser/undercover_assertions.js').read_text()
Path('/tmp/undercover-browser-test.html').write_text(
    html.replace('</body>', '<script>' + assertions + '</script></body>')
)
PY
```

用 Chrome 打开后，在开发者工具查看 `document.body.dataset.testResult`，期望 `PASS`；`dataset.tests` 为通过项清单。

## 覆盖边界

HTTP/SSE 与持久化另由 pytest 验证：

```bash
pytest tests/test_games_web.py tests/test_games_undercover.py tests/unit/test_games_undercover_pure.py -q
```

浏览器断言使用真实 DOM，但**未模拟**网络断线与长时间休眠；这两者由 HTTP 测试覆盖（广播、断点读取、控制接口）。
