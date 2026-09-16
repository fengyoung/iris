#!/usr/bin/env python3
"""运行本地检索黄金集，失败阈值适合直接接入 CI。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from iris.config.loader import load_config_bundle
from iris.evaluation.retrieval_eval import evaluate_retrieval


def main() -> int:
    parser = argparse.ArgumentParser(description="评测 Iris 本地检索质量")
    parser.add_argument("cases", type=Path, help="黄金集 JSON")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-recall", type=float, default=0.8)
    args = parser.parse_args()
    report = evaluate_retrieval(load_config_bundle(args.project_root), args.cases, top_k=args.top_k)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return int(report.recall_at_k < args.min_recall or report.citation_completeness < 1
               or report.stale_evidence_rate > 0)


if __name__ == "__main__":
    raise SystemExit(main())
