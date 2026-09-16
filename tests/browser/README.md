# 谁是卧底浏览器回归

`undercover_assertions.js` 在完整页面 DOM 上测试视图显隐、稳定编号、事件筛选、复盘私有思考缓存、总结状态、历史筛选和保存反馈，不调用真实模型。

生成可独立打开的测试页：

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

使用 Chrome 打开生成的文件，在开发者工具查看 `document.body.dataset.testResult`，期望 `PASS`；`dataset.tests` 为通过项。也可使用 Chrome `--headless=new --dump-dom` 验证输出中的 `data-test-result="PASS"`。

HTTP/SSE 与持久化验证运行：

```bash
pytest tests/test_games_web.py tests/test_games_undercover.py tests/unit/test_games_undercover_pure.py -q
```

浏览器断言使用真实 DOM，但未模拟浏览器网络断线与长时间休眠；HTTP 测试验证广播、断点读取及控制接口。
