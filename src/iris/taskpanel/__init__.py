"""Iris 任务面板 — 任务状态埋点 + 只读 Web 展示 + 常驻守护进程。

模块组成：
    reporter.py  TaskReporter 埋点 API（长任务上下文管理器）
    store.py     任务状态存储（current/ + history.jsonl）
    probe.py     进程探测兜底 + stale 判定 + watchdog
    server.py    stdlib HTTP 服务（/ + /api/state）
    daemon.py    守护进程本体 + start/stop/status/install 命令
"""
