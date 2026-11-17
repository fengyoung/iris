"""Executable entry, offline demo and deterministic smoke test."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import tempfile


def main(argv=None):
    parser = argparse.ArgumentParser(description="知遇 · Iris-interview")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--data-dir")
    parser.add_argument("--screenshot")
    args = parser.parse_args(argv)
    from PySide6.QtWidgets import QApplication
    from .ui import MainWindow
    from .resume import make_blocks

    application = QApplication(sys.argv[:1])
    temporary = (
        tempfile.TemporaryDirectory(prefix="iris-interview-demo-") if args.demo or args.smoke_test else None
    )
    window = MainWindow(args.data_dir or (temporary.name if temporary else None))
    if args.demo or args.smoke_test:
        p = window.store.create_project(
            name="合成演示 · 算法工程师 · 一面", candidate="演示候选人", job="算法工程师"
        )
        text = "项目经历\n\n负责推荐系统架构升级，实验点击率提升12%。\n\n设计检索增强问答系统，参与评估集建设与线上问题分析。\n"
        blocks = make_blocks(text)
        p["resume"] = {"text": text, "blocks": blocks, "verified": True}
        p["job_snapshot"] = {
            "name": "算法工程师",
            "jd": "推荐与AI应用研发",
            "tier": "基层",
            "management_required": False,
        }
        p["plan"] = {
            "summary": "合成演示，不包含真实候选人信息。",
            "opening": ["请简要介绍目前的工作。"],
            "candidate_questions": ["你希望了解岗位的哪些挑战？"],
            "questions": [
                {
                    "id": "demo-q1",
                    "block_id": blocks[1]["id"],
                    "text": "12% 的提升是如何验证的？",
                    "purpose": "了解实验设计与结果归因",
                    "followups": ["如何分组？", "有哪些护栏指标？"],
                    "dimensions": ["Dive Deep，正确决策"],
                    "status": "unasked",
                }
            ],
        }
        p = window.store.save_project(p)
        window.open_project(p)
        window.tabs.setCurrentIndex(1)
    window.show()
    if args.smoke_test or args.screenshot:
        application.processEvents()
        if args.screenshot:
            path = Path(args.screenshot)
            path.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(path))
        if args.smoke_test:
            window.toggle_theme()
            application.processEvents()
            assert window.aligned.scroll.widget() is not None
            window.save()
            window.close()
            if temporary:
                temporary.cleanup()
            print(json.dumps({"smoke": "passed", "network": False, "microphone": False, "keychain": False}))
            return 0
    result = application.exec()
    if temporary:
        temporary.cleanup()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
