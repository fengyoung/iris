#!/usr/bin/env python3
"""LLM 通道/模型 连接速度(TTFT) + 吞吐 基准（人类可读表格版）。

引擎与测量口径见 src/iris/llm/benchmark.py；本脚本仅负责读配置、跑基准、打印表格。

用法（在项目根目录）：
    python scripts/llm_bench.py                          # 全部模型
    python scripts/llm_bench.py --channel bailian
    python scripts/llm_bench.py --model gpt-5.6-sol-zz
    python scripts/llm_bench.py --phase2-only            # 复用上次 TTFT 缓存
    python scripts/llm_bench.py --concurrency 6 --max-tokens 3000

等价 CLI：iris llm-bench（输出 JSON，--pretty 同款表格）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from iris.config.loader import load_config_bundle  # noqa: E402
from iris.llm.benchmark import render_rows, run_benchmark  # noqa: E402

DEFAULT_CACHE = os.environ.get("IRIS_LLM_BENCH_CACHE", "/tmp/llm_bench_ttft.json")


def _load_cache(path: str) -> dict:
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_cache(path: str, cache: dict) -> None:
    try:
        with open(path, "w") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 通道/模型 连接速度 + 吞吐基准（表格）")
    ap.add_argument("--channel", help="只看某通道，如 zz_tokenhub/deepseek/bailian")
    ap.add_argument("--model", help="只看某 model_id（可多次）", action="append")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=3000, help="散文输出上限")
    ap.add_argument("--phase1-only", action="store_true")
    ap.add_argument("--phase2-only", action="store_true")
    ap.add_argument("--cache", default=DEFAULT_CACHE, help="TTFT 缓存路径")
    args = ap.parse_args()

    bundle = load_config_bundle(os.getcwd())

    def _progress(text: str) -> None:
        print(text, file=sys.stderr)

    cache = _load_cache(args.cache) if (args.phase2_only or not args.phase1_only) else None
    payload = run_benchmark(
        bundle,
        channel=args.channel,
        model_ids=args.model,
        concurrency=args.concurrency,
        max_tokens=args.max_tokens,
        phase1_only=args.phase1_only,
        phase2_only=args.phase2_only,
        ttft_cache=cache if args.phase2_only else None,
        progress=_progress,
    )
    if not args.phase2_only and payload.get("ttft_cache"):
        _write_cache(args.cache, payload["ttft_cache"])

    print(render_rows(payload["rows"]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        raise SystemExit(130)
