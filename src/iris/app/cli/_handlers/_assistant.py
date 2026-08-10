"""CLI 命令处理器 — 实时会议助理（meeting-live-assistant）。"""

from __future__ import annotations

import sys


def handle_meeting_live_assistant(args, bundle, logger) -> int:
    """启动实时会议助理守护进程。

    --output 指定过程文档路径（默认 data/meeting-live/YYYYMMDD-HHMMSS-会议记录.md）；
    --fast-only 仅词典校正（跳过所有 LLM）；与 asr-corrector 互斥（独占剪贴板）。
    """
    from iris.assistant.live import MeetingLiveAssistant

    output_path = getattr(args, "output", "") or ""
    if output_path:
        print(f"[Iris] 过程文档将输出到: {output_path}", file=sys.stderr)

    fast_only = getattr(args, "fast_only", False)
    assistant = MeetingLiveAssistant(bundle, output_path=output_path, fast_only=fast_only)
    return assistant.run()


ASSISTANT_HANDLERS = {
    "meeting-live-assistant": handle_meeting_live_assistant,
}
