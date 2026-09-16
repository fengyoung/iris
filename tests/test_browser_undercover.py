"""谁是卧底 Web 界面的浏览器回归：把真实 DOM 上的交互断言接进常规测试链路。

断言脚本在 `tests/browser/undercover_assertions.js`，需要真实 DOM 才能跑——
纯 pytest 覆盖不到。而这类问题恰恰已经发生过两次：合并 0916-beta 时丢了
`playerRowHtml` 定义、`judge-role` 的 DOM 引用悬空导致开局即抛错。两者都是
「函数都在、语法也过、跑起来才炸」，`node --check` 与静态检查全都看不见。

需要本机有 Chrome / Chromium；找不到时跳过（CI 的 ubuntu runner 预装
`/usr/bin/google-chrome`，工作流里另有断言防止镜像变更后静默跳过）。
"""
from __future__ import annotations

import html as html_module
import os
import re
import shutil
import subprocess
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator, Optional

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE = _ROOT / "src/iris/games/templates/undercover_web.html"
_ASSERTIONS = _ROOT / "tests/browser/undercover_assertions.js"

#: 常见安装位置；绝对路径直接查存在性，其余走 PATH。
_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


def _find_chrome() -> Optional[str]:
    for candidate in _CHROME_CANDIDATES:
        if os.path.isabs(candidate):
            if Path(candidate).exists():
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def _build_test_page(tmp_path: Path) -> Path:
    """把断言脚本注入页面副本。

    `init()` 换成空串：它会去请求后端接口，这里只测 DOM 交互。
    """
    markup = _TEMPLATE.read_text(encoding="utf-8").replace("init();", "")
    assertions = _ASSERTIONS.read_text(encoding="utf-8")
    page = markup.replace("</body>", "<script>" + assertions + "</script></body>")
    path = tmp_path / "undercover-browser-test.html"
    path.write_text(page, encoding="utf-8")
    return path


def _attribute(dom: str, name: str) -> Optional[str]:
    """从 --dump-dom 的输出里取 body 上的 data-* 属性（HTML 已转义，需还原）。"""
    matched = re.search(rf'{name}="([^"]*)"', dom)
    return html_module.unescape(matched.group(1)) if matched else None


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: A002 — 沿用基类签名
        pass


@contextmanager
def _serve(directory: Path) -> Iterator[str]:
    """用本地 HTTP 提供页面。

    刻意不用 `file://`：断言的「记住上次选择」一组依赖 localStorage，而
    file:// 的来源是不透明源，浏览器对它的存储策略各不相同。走 HTTP 既消除
    这层环境不确定性，也与真实用法一致——页面本来就是由 Web 服务提供的。
    """
    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_browser_assertions_pass(tmp_path: Path) -> None:
    chrome = _find_chrome()
    if chrome is None:
        pytest.skip("未找到 Chrome/Chromium；装上即可运行浏览器回归")

    _build_test_page(tmp_path)
    with _serve(tmp_path) as base_url:
        result = subprocess.run(
            [
                chrome,
                # 用 `--headless` 而非 `--headless=new`：旧版 Chrome 下前者是旧无头、
                # 新版下已是新无头，`--dump-dom` 两代都支持；`--headless=new` 在新版
                # 已标弃用，跨版本更脆。
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--virtual-time-budget=8000",
                "--dump-dom",
                f"{base_url}/undercover-browser-test.html",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )

    dom = result.stdout
    verdict = _attribute(dom, "data-test-result")
    assert verdict is not None, (
        f"页面未产出测试结果（Chrome 退出码 {result.returncode}，"
        f"stdout {len(dom)} 字节）\nstderr: {result.stderr[:500]}"
    )
    if verdict != "PASS":
        passed = _attribute(dom, "data-tests") or ""
        raise AssertionError(f"浏览器断言失败：{verdict}\n已通过的检查（{passed.count('；') + 1 if passed else 0} 项）：{passed}")
