# 谁是卧底 beta 验收记录（2026-09-17）

本轮保留并复核上一会话的全部未提交修改，完成恢复链路修复、测试、页面检查和中文文档更新。功能回归通过；共享环境依赖审计未通过，不能视为全部发布门禁已完成。未提交、合并或部署本轮修改。

## 源码复核与修复

1. 引擎发出完整 `RoundRecord` 断点，存储层原子写入；HTTP 恢复把断点传给 `UndercoverGame.run()`。续跑继承公开/私有历史、身份、存活名单与发言顺序，从下一轮开始，最终复盘保留之前轮次。
2. HTTP 和引擎共用历史结构校验。旧断点、缺失字段、非连续轮次、非法玩家集合被拒绝；终局断点直接产生结果，不继续模型调用。
3. 重复恢复检查与会话注册在同一 `games_lock` 临界区，避免覆盖运行中的会话；校验失败不消耗对局名额，线程启动失败释放名额。
4. 恢复保留自动/手动模式。手动模式在下一轮前等待，可推进或取消，沿用 300 秒超时规则。已完成对局先识别复盘，不因断点删除而误报不存在。
5. 页面“当前对局”接通服务重启后的恢复接口；保留淘汰状态；科学计数法整数种子 `1e3` 按 `1000` 提交。
6. 原全量失败来自测试替换整个 `threading.Thread`，连 HTTP 请求线程都被拦截。现只替换游戏入口，保留真实请求线程。新增恢复接线/落盘测试正确读取复盘顶层 `rounds`。

## 最终验证

| 检查 | 结果与证据 |
|---|---|
| 全量 pytest + coverage | **3,621 passed，104.92 秒，70.01%**；`/tmp/iris-beta-tests.log`、`/tmp/iris-beta-coverage.json` |
| 恢复相关专项 | **85 passed**：`tests/test_games_web.py`、`tests/test_games_undercover.py`、`tests/test_browser_undercover.py` |
| 关键模块覆盖率 | `python scripts/check_coverage_thresholds.py /tmp/iris-beta-coverage.json` 全部通过 |
| Ruff | `ruff check src scripts tests` 通过 |
| mypy | `mypy src/iris --no-incremental`：192 个源文件通过 |
| AST 安全扫描 | `python scripts/security_scan.py` 通过 |
| 页面 DOM 与交互 | 无头 Chrome + 本机 HTTP，桌面 1440×1000、窄屏 500×844 各 26 项通过 |
| 页面视觉检查 | 已查看两种宽度截图；导航与模型选择可见，窄屏图片区单列；`innerWidth` 与 `scrollWidth` 相等，无横向溢出 |
| 构建 | `pip wheel . --no-deps --no-build-isolation` 成功；`/tmp/iris-beta-dist/iris-3.40.4-py3-none-any.whl` |
| SPDX SBOM | `/tmp/iris-beta-dist/iris.spdx.json` 生成成功；产品版本 3.40.4 |
| macOS CLI smoke | `python scripts/run_cli.py --help` 成功；`/tmp/iris-beta-cli-help.txt` |
| 依赖审计 | **未通过**：`pip-audit` 退出码 1，17 个包、119 条漏洞记录；`/tmp/iris-beta-audit.json` |

复现全量回归：

```bash
pytest tests/ --cov=src/iris --cov-report=json:/tmp/iris-beta-coverage.json \
  --cov-report=term -q --tb=short > /tmp/iris-beta-tests.log 2>&1
python scripts/check_coverage_thresholds.py /tmp/iris-beta-coverage.json
```

页面截图与 DOM 留在 `/tmp/iris-beta-page-check/`。Chrome 的无头窗口最小布局宽度为 500px，因此窄屏证据明确记为 500px，未将最初被裁切的 390px 截图认作手机设备模拟通过。浏览器使用测试响应，不调用付费模型；Python HTTP 测试单独覆盖真实服务与持久化，引擎测试覆盖恢复执行。

## 剩余限制

- 共享环境审计发现漏洞的包为 aiohttp、h2、idna、lightning、lxml、msgpack、pillow、pip、pytest、python-multipart、pytorch-lightning、requests、setuptools、soupsieve、starlette、urllib3、yt-dlp。记录数包含漏洞源重复条目，不等于 119 个独立漏洞。requests 2.32.5、pytest 9.0.2、setuptools 82.0.1 等低于仓库既有依赖下界；本轮没有修改共享环境。发布前应在隔离环境按仓库依赖安装并重新审计。
- Linux CI、Docker 非 root smoke、真实外部模型对局未运行；本轮验证不能替代这些发布检查。
- 断点为轮次边界快照，中断轮尚未完成的工作会重跑；旧版仅轮号断点无法迁移为完整历史。
- 恢复后的实时观战时间线从恢复时开始，之前完整轮次保留于引擎上下文和最终复盘。当前页面入口依赖浏览器保存的对局 ID；清除存储后需用已有查询/恢复接口定位断点。
- 随机种子只保证开局身份与顺序的可复现性；生成器内部状态未保存，恢复后平票抽签不保证与不中断时相同。模型输出本身也不保证确定性。
- 版本记为待发布变更，产品版本仍为 3.40.4，协议与配置版本不变。文档和修改保留在工作区供后续审阅。
