"""Real Chrome/HTTP smoke for the native console; no browser packages required."""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from iris.taskpanel.runner import JobRunner
from iris.taskpanel.server import create_server

_ASSERTIONS = Path(__file__).parents[1] / "browser" / "taskpanel_assertions.js"


def _chrome() -> str:
    for candidate in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        found = (
            str(Path(candidate))
            if Path(candidate).is_file()
            else shutil.which(candidate)
        )
        if found:
            return found
    pytest.skip("Chrome/Chromium unavailable")


@pytest.mark.parametrize("width", [1280, 390])
def test_console_real_browser(tmp_path: Path, width: int) -> None:
    chrome = _chrome()
    script = tmp_path / "fake_cli.py"
    script.write_text(
        "import sys, json\n"
        "from pathlib import Path\n"
        "if sys.argv[1] == 'discover-wiki':\n"
        " print(json.dumps({'items': [{'title': '浏览器测试候选', 'query': '测试主题', 'page_type': 'concept'}]}, ensure_ascii=False))\n"
        "elif sys.argv[1] == 'build-wiki':\n"
        " path = next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--review-file='))\n"
        " items = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]\n"
        " print(json.dumps({'items': [{**i, 'status': 'success', 'markdown': '# 草稿', 'output_path': 'test.md'} for i in items]}, ensure_ascii=False))\n"
        "else:\n"
        " print('控制台日志：你好', flush=True)\n"
        " print('<img src=x onerror=window.__xss=1>', flush=True)\n"
        " print('stderr: diagnostic', file=sys.stderr, flush=True)\n",
        encoding="utf-8",
    )
    server = create_server(tmp_path, tmp_path / "data", port=0, version="browser-test")
    server.runner = JobRunner(
        tmp_path, tmp_path / "data", cli_script=script, python=sys.executable
    )
    server.wizards.runner = server.runner
    assertions = _ASSERTIONS.read_text(encoding="utf-8")
    server.state.index_html = server.state.index_html.replace(
        b"</body>",
        ("<script>" + assertions + "</script></body>").encode(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                chrome,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={tmp_path / 'chrome-profile'}",
                f"--window-size={width},1000",
                "--virtual-time-budget=20000",
                "--dump-dom",
                f"http://127.0.0.1:{server.server_address[1]}/",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
    match = re.search(r'data-test-result="([^"]*)"', result.stdout)
    verdict = html.unescape(match.group(1)) if match else None
    assert verdict == "PASS", (
        f"Chrome exit={result.returncode}; verdict={verdict}; "
        f"stdout bytes={len(result.stdout)}\nstderr={result.stderr[:2000]}\n"
        f"DOM tail={result.stdout[-4000:]}"
    )
