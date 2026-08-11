"""CLI 命令处理器 — 实时会议助理（meeting-live-assistant）。"""

from __future__ import annotations

import sys


def handle_meeting_live_assistant(args, bundle, logger) -> int:
    """启动实时会议助理守护进程。

    --output 指定过程文档路径（默认 data/meeting-live/YYYYMMDD-HHMMSS-会议记录.md）；
    --asr 指定 ASR 模式（local|remote，默认从 app.json 读取）。
    """
    from iris.assistant.live import MeetingLiveAssistant

    output_path = getattr(args, "output", "") or ""
    if output_path:
        print(f"[Iris] 过程文档将输出到: {output_path}", file=sys.stderr)

    asr_mode = getattr(args, "asr", "") or ""
    assistant = MeetingLiveAssistant(bundle, output_path=output_path, asr_mode=asr_mode)
    return assistant.run()


ASSISTANT_HANDLERS = {
    "meeting-live-assistant": handle_meeting_live_assistant,
}
