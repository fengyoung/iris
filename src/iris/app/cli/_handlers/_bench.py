"""llm-bench 命令 — LLM 通道/模型 连接速度(TTFT) + 吞吐基准。

用法：
    iris llm-bench                              # 全部模型，输出 JSON rows
    iris llm-bench --channel bailian
    iris llm-bench --bench-model deepseek-v4-flash --pretty
    iris llm-bench --phase1-only --concurrency 1

--pretty 输出人类可读对齐表格；默认输出 JSON（供 Skill/脚本消费）。
引擎与测量口径见 iris.llm.benchmark。
"""
from __future__ import annotations

from typing import Dict

from iris.app.cli.helpers import _emit_output
from iris.llm.benchmark import render_rows, run_benchmark


def handle_llm_bench(args, bundle, logger) -> int:
    """执行 LLM 通道/模型基准并输出。"""
    payload = run_benchmark(
        bundle,
        channel=args.channel,
        model_ids=getattr(args, "bench_model", None),
        concurrency=getattr(args, "concurrency", 4),
        max_tokens=getattr(args, "max_tokens", 3000),
        phase1_only=bool(getattr(args, "phase1_only", False)),
        phase2_only=bool(getattr(args, "phase2_only", False)),
    )
    if args.pretty:
        # 对齐表格输出到 stdout（供终端查看）；JSON 已含全部指标可另行消费
        print(render_rows(payload["rows"]))
        return 0
    _emit_output(args.command, payload, pretty=False)
    return 0


BENCH_HANDLERS: Dict[str, object] = {
    "llm-bench": handle_llm_bench,
}
