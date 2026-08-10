"""CLI 命令处理器 — 实时会议助理（meeting-live-assistant）。"""

from __future__ import annotations

import sys


def handle_meeting_live_assistant(args, bundle, logger) -> int:
    """启动实时会议助理守护进程。

    --output 指定过程文档路径（默认 data/meeting-live/YYYYMMDD-HHMM-会议记录.md）；
    与 asr-corrector 互斥（独占剪贴板）。
    """
    from iris.assistant.live import MeetingLiveAssistant

    output_path = getattr(args, "output", "") or ""
    if output_path:
        print(f"[Iris] 过程文档将输出到: {output_path}", file=sys.stderr)

    assistant = MeetingLiveAssistant(bundle, output_path=output_path)
    return assistant.run()


ASSISTANT_HANDLERS = {
    "meeting-live-assistant": handle_meeting_live_assistant,
}
