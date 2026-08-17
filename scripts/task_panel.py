"""任务面板 CLI 入口脚本 — iris task-panel <start|stop|status|install>。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from iris.taskpanel.daemon import do_install, do_start, do_status, do_stop


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Iris 任务面板 — Web 只读展示 iris 任务状态与进程")
    parser.add_argument("--project-root", default=str(_PROJECT_ROOT),
                        help="Iris 项目根目录")
    sub = parser.add_subparsers(dest="command", required=True)

    start_parser = sub.add_parser("start", help="启动守护进程（后台常驻）")
    start_parser.add_argument("--port", type=int, default=None,
                              help="面板端口（默认 8765，可用 IRIS_TASK_PANEL_PORT 覆盖）")
    start_parser.set_defaults(handler=do_start)

    sub.add_parser("stop", help="停止守护进程").set_defaults(handler=do_stop)
    sub.add_parser("status", help="查看守护进程状态").set_defaults(handler=do_status)
    sub.add_parser("install", help="生成 launchd 开机自启配置").set_defaults(handler=do_install)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
